"""AI content assistant endpoints: describe, generate, review, accept.

Two rules mirror the rest of the API:

* **The workspace always comes from the URL.** :func:`require_permission`
  resolves it through ``TenantMixin.scoped`` and answers ``404`` for a workspace
  the caller is not a member of; a ``post_id`` in the body is a *reference* that
  the service re-resolves inside that workspace.
* **Nothing generated is published.** ``POST .../generate`` returns ``201`` with
  the stored suggestion; ``POST .../history/{id}/accept`` only records that the
  user chose it. Publishing still requires the ordinary, explicit composer
  action.

Error translation is centralised in :func:`_translate` so an unconfigured
provider is a documented ``503`` and a policy refusal a ``422``, rather than a
generic ``500``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext, require_permission
from app.core.config import settings
from app.core.logging import get_logger
from app.core.permissions import AI_GENERATE
from app.core.rate_limit import rate_limit
from app.db.enums import AIContentKind
from app.db.session import get_db
from app.schemas import ai as schemas
from app.services import ai_content as ai_service
from app.services import publishing as publishing_service
from app.services.ai import (
    AIContentRejectedError,
    AIError,
    AINotConfiguredError,
    AIProviderError,
    AITransientError,
)

router = APIRouter()
logger = get_logger(__name__)

#: Generation costs money or quota on every call, so it is capped per caller.
_ai_rate_limit = rate_limit("ai-generate", limit=settings.rate_limit_ai_per_minute)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _translate(exc: Exception) -> HTTPException:
    """Map an AI failure onto the documented status code."""
    if isinstance(
        exc,
        (ai_service.AIPostNotFoundError, ai_service.AIContentNotFoundError),
    ):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ai_service.InvalidAIRequestError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    if isinstance(exc, AINotConfiguredError):
        # A capability gap: the feature is unavailable, not the request broken.
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, AIContentRejectedError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    if isinstance(exc, AITransientError):
        # The provider is busy or unreachable; the user may simply retry.
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, (AIProviderError, AIError)):
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="The assistant could not complete this request.",
    )


# ---------------------------------------------------------------------------
# Capability
# ---------------------------------------------------------------------------
@router.get(
    "/{workspace_id}/capability",
    response_model=schemas.AICapabilityPublic,
    summary="What the AI assistant can do, and whether it is configured",
)
def get_capability(
    context: WorkspaceContext = Depends(require_permission(AI_GENERATE)),
    db: Session = Depends(get_db),
) -> schemas.AICapabilityPublic:
    """Read first: ``configured: false`` means the server has no model set up.

    The reported provider/model is the one the router would actually use, so an
    operator who registered models sees them here rather than the env default.
    """
    return schemas.AICapabilityPublic.from_capability(ai_service.capability(db))


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------
@router.post(
    "/{workspace_id}/generate",
    response_model=schemas.AIContentPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a caption, hashtag set, description, rewrite, or idea",
    dependencies=[Depends(_ai_rate_limit)],
)
def generate_content(
    payload: schemas.AIGenerateRequest,
    context: WorkspaceContext = Depends(require_permission(AI_GENERATE)),
    db: Session = Depends(get_db),
) -> schemas.AIContentPublic:
    """Produce one suggestion and store it for review.

    ``404`` if ``post_id`` is not in this workspace, ``422`` for an unknown
    tone/style or a rewrite without a draft, ``503`` when no model is
    configured, ``502`` when the provider rejects the call.
    """
    try:
        content = ai_service.generate(
            db,
            workspace_id=context.workspace.id,
            user_id=context.member.user_id,
            kind=payload.kind,
            brief=payload.brief,
            tone=payload.tone,
            style=payload.style,
            platform=payload.platform,
            post_id=payload.post_id,
            draft=payload.draft,
        )
    except (
        ai_service.AIContentError,
        AIError,
        publishing_service.PublishingError,
    ) as exc:
        raise _translate(exc) from exc

    db.commit()
    return schemas.AIContentPublic.from_content(content)


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
@router.get(
    "/{workspace_id}/history",
    response_model=list[schemas.AIContentPublic],
    summary="List past suggestions, newest first",
)
def list_history(
    kind: AIContentKind | None = Query(default=None, description="Filter by kind of copy."),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: WorkspaceContext = Depends(require_permission(AI_GENERATE)),
    db: Session = Depends(get_db),
) -> list[schemas.AIContentPublic]:
    """Every generation is kept, so a user can revisit a past idea."""
    rows = ai_service.list_history(
        db, context.workspace.id, kind=kind, limit=limit, offset=offset
    )
    return [schemas.AIContentPublic.from_content(row) for row in rows]


@router.post(
    "/{workspace_id}/history/{content_id}/accept",
    response_model=schemas.AIContentPublic,
    summary="Record that the user accepted a suggestion",
)
def accept_content(
    content_id: uuid.UUID,
    context: WorkspaceContext = Depends(require_permission(AI_GENERATE)),
    db: Session = Depends(get_db),
) -> schemas.AIContentPublic:
    """Marks the suggestion as used. It still publishes nothing on its own."""
    try:
        content = ai_service.accept(
            db, workspace_id=context.workspace.id, content_id=content_id
        )
    except ai_service.AIContentError as exc:
        raise _translate(exc) from exc

    db.commit()
    return schemas.AIContentPublic.from_content(content)


__all__ = ["router"]
