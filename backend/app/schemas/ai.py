"""Request/response schemas for the AI content assistant.

The shape is deliberately small: a *capability* describing what this server can
do, a *generation* request that names the kind of copy wanted plus the brief,
tone and style, and the stored *suggestion* the user reviews.

No schema exposes a credential or a token, and ``output`` is the model's own
text — nothing is rewritten or inferred in transit.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.db.enums import AIContentKind, SocialPlatform
from app.models.ai_content import AIGeneratedContent
from app.services import ai_content as ai_service

#: Generous sanity bound on the brief. The real, configurable ceiling
#: (``AI_MAX_BRIEF_CHARS``) is enforced by the service, which is where a
#: ``422`` with a useful message is produced.
_MAX_BRIEF_LENGTH = 20_000

#: Bound on a rewrite's source text, matching the composer's caption ceiling.
_MAX_DRAFT_LENGTH = 10_000


# ---------------------------------------------------------------------------
# Capability
# ---------------------------------------------------------------------------
class AICapabilityPublic(BaseModel):
    """What the assistant can do on this server, and whether it is configured."""

    provider: str
    model: str
    configured: bool
    kinds: list[AIContentKind]
    tones: list[str]
    styles: list[str]
    max_brief_chars: int
    max_output_tokens: int

    @classmethod
    def from_capability(cls, capability: ai_service.AICapability) -> "AICapabilityPublic":
        return cls(
            provider=capability.provider,
            model=capability.model,
            configured=capability.configured,
            kinds=list(capability.kinds),
            tones=list(capability.tones),
            styles=list(capability.styles),
            max_brief_chars=capability.max_brief_chars,
            max_output_tokens=capability.max_output_tokens,
        )


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------
class AIContentPublic(BaseModel):
    """One stored suggestion, kept so the user can review it before publishing."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    post_id: uuid.UUID | None = None
    created_by_id: uuid.UUID | None = None
    kind: AIContentKind
    model: str
    prompt: str
    output: str
    tone: str | None = None
    is_accepted: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_content(cls, content: AIGeneratedContent) -> "AIContentPublic":
        return cls(
            id=content.id,
            workspace_id=content.workspace_id,
            post_id=content.post_id,
            created_by_id=content.created_by_id,
            kind=content.kind,
            model=content.model,
            prompt=content.prompt,
            output=content.output,
            tone=content.tone,
            is_accepted=content.is_accepted,
            created_at=content.created_at,
            updated_at=content.updated_at,
        )


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
class AIGenerateRequest(BaseModel):
    """Ask the assistant for one piece of copy.

    ``brief`` is what the video is about; ``draft`` is only meaningful for
    :attr:`~app.db.enums.AIContentKind.REWRITE`, where it is the text to rewrite.
    """

    kind: AIContentKind
    brief: str = Field(default="", max_length=_MAX_BRIEF_LENGTH)
    tone: str | None = Field(default=None, max_length=40)
    style: str | None = Field(default=None, max_length=40)
    #: Tailor the output to one platform when the user has one in mind.
    platform: SocialPlatform | None = None
    #: Optionally record which post this suggestion was generated for.
    post_id: uuid.UUID | None = None
    #: The text to rewrite (required when ``kind`` is ``rewrite``).
    draft: str | None = Field(default=None, max_length=_MAX_DRAFT_LENGTH)


__all__ = [
    "AICapabilityPublic",
    "AIContentPublic",
    "AIGenerateRequest",
]
