"""AI content generation — draft copy the user reviews before it is published.

Three rules shape this module:

* **The assistant never publishes anything.** :func:`generate` writes one row to
  ``ai_generated_content`` and returns it. Nothing is attached to a post and no
  publishing job is created; :func:`accept` only flips the ``is_accepted`` flag
  when the *user* decides to use the text. The master prompt requires that
  AI-generated content is never published without the user's authorization.
* **The prompt is built here, not in the provider.** A provider is a transport;
  the platform guidance, tone, style and per-kind instructions that make the
  output usable are the application's business and live in
  :data:`_KIND_INSTRUCTIONS`, :data:`_PLATFORM_GUIDANCE` and friends.
* **Every suggestion is kept.** Each generation is persisted with the exact
  prompt that produced it plus the model that answered, so the history is
  auditable and a result can be shown back to the user on a later visit.

The service is provider-agnostic: it talks to
:class:`~app.services.ai.base.AIProvider` and lets that layer's error taxonomy
propagate, which the API maps onto status codes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.enums import AIContentKind, SocialPlatform
from app.models.ai_content import AIGeneratedContent
from app.services import ai_router
from app.services import publishing as publishing_service
from app.services.ai.base import CompletionRequest

logger = get_logger(__name__)

#: Caption tones the user can pick from. Free-form values are rejected so the
#: stored ``tone`` column stays queryable.
TONES: tuple[str, ...] = (
    "professional",
    "casual",
    "friendly",
    "playful",
    "inspirational",
    "bold",
    "witty",
    "energetic",
    "calm",
    "luxury",
)

#: Caption structures ("multiple caption styles" in the phase brief).
STYLES: tuple[str, ...] = (
    "short",
    "standard",
    "storytelling",
    "listicle",
    "question",
    "how-to",
)

#: Every kind the assistant can produce.
KINDS: tuple[AIContentKind, ...] = tuple(AIContentKind)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class AIContentError(Exception):
    """Base class for AI-content-domain failures."""


class InvalidAIRequestError(AIContentError):
    """The request is well-formed but not usable (unknown tone, missing draft)."""


class AIPostNotFoundError(AIContentError):
    """The referenced post does not exist in this workspace."""


class AIContentNotFoundError(AIContentError):
    """No such generated suggestion in this workspace."""


# ---------------------------------------------------------------------------
# Capability
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class AICapability:
    """What the assistant can do on this server, and whether it is configured.

    ``configured`` is the one field the UI reads first: when it is ``False`` the
    page explains that no model is set up instead of offering a button that can
    only fail.
    """

    provider: str
    model: str
    configured: bool
    kinds: tuple[AIContentKind, ...]
    tones: tuple[str, ...]
    styles: tuple[str, ...]
    max_brief_chars: int
    max_output_tokens: int


def capability(db: Session | None = None) -> AICapability:
    """Describe the assistant's abilities and configuration state.

    ``provider``/``model`` are the pair that will actually answer: the primary
    registered model when the router has a usable one, otherwise the single
    environment-configured default. ``configured`` therefore agrees with what
    :func:`generate` will do rather than with one vendor's env var.
    """
    provider_name, model, configured = ai_router.describe(db)
    return AICapability(
        provider=provider_name,
        model=model,
        configured=configured,
        kinds=KINDS,
        tones=TONES,
        styles=STYLES,
        max_brief_chars=settings.ai_max_brief_chars,
        max_output_tokens=settings.openai_max_output_tokens,
    )


# ---------------------------------------------------------------------------
# Prompt engineering
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = (
    "You are the content assistant inside a social media video publishing tool. "
    "You write platform-appropriate copy that the user can publish exactly as "
    "you return it. Reply with only the requested content: no preamble, no "
    "explanation, no surrounding quotation marks, and no markdown headings or "
    "code fences."
)

_KIND_INSTRUCTIONS: dict[AIContentKind, str] = {
    AIContentKind.CAPTION: (
        "Task: write one social media caption for a video. Make the first line a "
        "hook that works even when the rest is truncated."
    ),
    AIContentKind.HASHTAGS: (
        "Task: return one line of 8 to 15 relevant hashtags. Every tag starts "
        "with '#', tags are separated by single spaces, and nothing else is "
        "returned."
    ),
    AIContentKind.DESCRIPTION: (
        "Task: write a longer video description suitable for a video page. Lead "
        "with a one-sentence summary, then two or three short paragraphs."
    ),
    AIContentKind.REWRITE: (
        "Task: rewrite the supplied text, keeping its meaning and the facts in "
        "it. Return only the rewritten text."
    ),
    AIContentKind.SUGGESTION: (
        "Task: suggest three distinct content angles for the described video. "
        "Return them as three short numbered lines."
    ),
}

_PLATFORM_GUIDANCE: dict[SocialPlatform, str] = {
    SocialPlatform.INSTAGRAM: (
        "Channel: Instagram. Warm and visual; a few emoji are welcome; keep it "
        "under about 2,200 characters and place hashtags at the end."
    ),
    SocialPlatform.FACEBOOK: (
        "Channel: Facebook. Conversational and informative; the first line must "
        "stand alone as a hook."
    ),
    SocialPlatform.YOUTUBE: (
        "Channel: YouTube. Searchable and keyword-rich, written for the video's "
        "title and description."
    ),
    SocialPlatform.TIKTOK: (
        "Channel: TikTok. Short, punchy and trend-aware; the first few words "
        "decide whether the video is watched."
    ),
}


def _build_prompt(
    *,
    kind: AIContentKind,
    brief: str,
    tone: str | None,
    style: str | None,
    platform: SocialPlatform | None,
    draft: str | None,
) -> tuple[str, str]:
    """Assemble the ``(system, user)`` pair a provider will send.

    Kept as one function so every kind is described in one place and the exact
    text is what gets stored on the row for audit.
    """
    lines = [_KIND_INSTRUCTIONS[kind]]
    if platform is not None:
        lines.append(_PLATFORM_GUIDANCE[platform])
    if tone:
        lines.append(f"Tone: {tone}.")
    if style:
        lines.append(f"Structure: {style}.")
    if draft and draft.strip():
        lines.append(f'Rewrite this text:\n"""\n{draft.strip()}\n"""')
    if brief.strip():
        lines.append(f"About the video: {brief.strip()}")
    return _SYSTEM_PROMPT, "\n".join(lines)


def _validate(
    *,
    kind: AIContentKind,
    brief: str,
    tone: str | None,
    style: str | None,
    draft: str | None,
) -> None:
    """Refuse a request the assistant cannot act on, before spending a call."""
    if tone is not None and tone not in TONES:
        raise InvalidAIRequestError(
            f"Unknown tone '{tone}'. Choose one of: {', '.join(TONES)}."
        )
    if style is not None and style not in STYLES:
        raise InvalidAIRequestError(
            f"Unknown style '{style}'. Choose one of: {', '.join(STYLES)}."
        )
    if kind is AIContentKind.REWRITE and not (draft or "").strip():
        raise InvalidAIRequestError("Rewriting needs the text you want rewritten.")
    if not brief.strip() and not (draft or "").strip():
        raise InvalidAIRequestError("Describe what you want the assistant to write.")
    if len(brief) > settings.ai_max_brief_chars:
        raise InvalidAIRequestError(
            f"Keep the brief under {settings.ai_max_brief_chars} characters."
        )


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
def generate(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID | None,
    kind: AIContentKind,
    brief: str = "",
    tone: str | None = None,
    style: str | None = None,
    platform: SocialPlatform | None = None,
    post_id: uuid.UUID | None = None,
    draft: str | None = None,
) -> AIGeneratedContent:
    """Generate one suggestion and persist it for review.

    Nothing is attached to the post and nothing is published — the returned row
    is a *draft* the user may accept, edit or discard.

    The call is handed to :mod:`app.services.ai_router`, which picks the
    highest-priority compatible model, fails over to the next on a model-local
    failure and records every attempt; the model that actually answered is what
    is stored on the row.

    Raises:
        InvalidAIRequestError: unknown tone/style, a missing draft for a rewrite,
            an empty brief, or a brief over the configured limit.
        AIPostNotFoundError: ``post_id`` is not in this workspace.
        AINotConfiguredError: no provider credential is configured.
        AITransientError / AIRateLimitError: the provider is unavailable.
        AIContentRejectedError: the provider refused for safety reasons.
        AIProviderError: the provider rejected the request or returned garbage.
    """
    _validate(kind=kind, brief=brief, tone=tone, style=style, draft=draft)

    if post_id is not None:
        # Re-resolve inside the workspace: the body's id is a reference, not an
        # authority, and another tenant's post must answer "not found".
        try:
            publishing_service.get_post(db, workspace_id, post_id)
        except publishing_service.PostNotFoundError as exc:
            raise AIPostNotFoundError("That post is not in this workspace.") from exc

    system, prompt = _build_prompt(
        kind=kind,
        brief=brief,
        tone=tone,
        style=style,
        platform=platform,
        draft=draft,
    )

    routed = ai_router.complete(
        db,
        request=CompletionRequest(system=system, prompt=prompt),
        kind=kind,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    completion = routed.completion

    content = AIGeneratedContent(
        workspace_id=workspace_id,
        post_id=post_id,
        created_by_id=user_id,
        kind=kind,
        model=completion.model,
        prompt=prompt,
        output=completion.text,
        tone=tone,
        is_accepted=False,
    )
    db.add(content)
    db.flush()
    logger.info(
        "ai content generated",
        extra={
            "workspace_id": str(workspace_id),
            "kind": kind.value,
            "model": completion.model,
            "routed_model": routed.model_name,
            "attempts": routed.attempts,
            "switched": routed.switched,
        },
    )
    return content


# ---------------------------------------------------------------------------
# Reads + acceptance
# ---------------------------------------------------------------------------
def list_history(
    db: Session,
    workspace_id: uuid.UUID,
    *,
    kind: AIContentKind | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AIGeneratedContent]:
    """The workspace's past suggestions, newest first."""
    stmt = AIGeneratedContent.scoped(workspace_id)
    if kind is not None:
        stmt = stmt.where(AIGeneratedContent.kind == kind)
    stmt = stmt.order_by(AIGeneratedContent.created_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt))


def accept(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    content_id: uuid.UUID,
) -> AIGeneratedContent:
    """Mark a suggestion as accepted, so the user's choice is recorded.

    This is an audit flag only: accepting does not attach the text to a post or
    publish it — the user still applies it in the composer and publishes
    explicitly.

    Raises:
        AIContentNotFoundError: no such suggestion in this workspace.
    """
    content = get_content(db, workspace_id, content_id)
    content.is_accepted = True
    db.flush()
    return content


def get_content(
    db: Session, workspace_id: uuid.UUID, content_id: uuid.UUID
) -> AIGeneratedContent:
    """One suggestion in this workspace, or raise.

    Raises:
        AIContentNotFoundError: no such suggestion in this workspace.
    """
    content = db.scalar(
        AIGeneratedContent.scoped(workspace_id).where(AIGeneratedContent.id == content_id)
    )
    if content is None:
        raise AIContentNotFoundError("That suggestion is not in this workspace.")
    return content


__all__ = [
    "KINDS",
    "STYLES",
    "TONES",
    "AICapability",
    "AIContentError",
    "AIContentNotFoundError",
    "AIPostNotFoundError",
    "InvalidAIRequestError",
    "accept",
    "capability",
    "generate",
    "get_content",
    "list_history",
]
