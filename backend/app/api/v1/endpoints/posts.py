"""Publishing endpoints: compose a post, publish it everywhere, watch the jobs.

Three rules shape this module:

* **The workspace always comes from the URL, never from the body.** Every route
  starts at :func:`app.api.deps.require_permission`, which resolves
  ``{workspace_id}`` through ``TenantMixin.scoped`` and answers ``404`` for a
  workspace the caller is not a member of. A ``social_account_id`` or
  ``media_id`` in a request body is a *reference*, not an authority: the
  service re-resolves it inside the caller's workspace.
* **Nothing is reported published until the platform confirms it.** The
  ``POST .../publish`` route only *dispatches*; it returns ``202`` with every
  target ``queued``. A target turns ``published`` only once the worker's job
  records the platform's own terminal status, which the client reads back from
  the per-target ``job``.
* **One platform's failure never cancels another.** Because each destination is
  its own target and its own job, a partial success is visible here as a
  ``partially_published`` post with an explanation per failed target.

Error translation is centralised in :func:`_translate` so every publishing
failure maps to one documented status code.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext, require_permission
from app.core.logging import get_logger
from app.core.permissions import POST_CREATE, POST_PUBLISH, POST_READ
from app.db.enums import PostStatus
from app.db.session import get_db
from app.schemas import post as schemas
from app.services import media as media_service
from app.services import publishing as publishing_service

router = APIRouter()
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _translate(exc: Exception) -> HTTPException:
    """Map a publishing/media failure onto the documented status code."""
    if isinstance(
        exc,
        (publishing_service.PostNotFoundError, media_service.MediaNotFoundError),
    ):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, publishing_service.MediaNotReadyError):
        # The referenced video exists but is not publishable *yet*.
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, publishing_service.NoTargetsSelectedError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    if isinstance(exc, publishing_service.NoPublishableTargetError):
        # Nothing was dispatched; the message lists every rejection reason.
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, publishing_service.PublishingError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="The publishing operation could not be completed.",
    )


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
@router.get(
    "/{workspace_id}/capabilities",
    response_model=list[schemas.PublisherCapabilityPublic],
    summary="Every platform's publishing limits",
)
def get_capabilities(
    context: WorkspaceContext = Depends(require_permission(POST_READ)),
) -> list[schemas.PublisherCapabilityPublic]:
    """The composer reads this before validating, so it can guide the user.

    Reporting the limits here keeps the publishers the single source of truth:
    the frontend never hard-codes a size or caption ceiling.
    """
    return [
        schemas.PublisherCapabilityPublic.from_capability(capability)
        for capability in publishing_service.capabilities()
    ]


# ---------------------------------------------------------------------------
# Job monitoring
# ---------------------------------------------------------------------------
@router.get(
    "/{workspace_id}/jobs/{job_id}",
    response_model=schemas.PublishingJobDetail,
    summary="Get one publishing job with its log trail",
)
def get_job(
    job_id: uuid.UUID,
    context: WorkspaceContext = Depends(require_permission(POST_READ)),
    db: Session = Depends(get_db),
) -> schemas.PublishingJobDetail:
    """Per-attempt detail — status, error class, and the redacted raw responses.

    Another workspace's job id answers ``404``, never ``403``.
    """
    try:
        job = publishing_service.get_job(db, context.workspace.id, job_id)
    except publishing_service.PublishingError as exc:
        raise _translate(exc) from exc
    return schemas.PublishingJobDetail.from_job_with_logs(job)


# ---------------------------------------------------------------------------
# Post reads
# ---------------------------------------------------------------------------
@router.get(
    "/{workspace_id}",
    response_model=list[schemas.PostPublic],
    summary="List the workspace's posts",
)
def list_posts(
    post_status: PostStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: WorkspaceContext = Depends(require_permission(POST_READ)),
    db: Session = Depends(get_db),
) -> list[schemas.PostPublic]:
    """Newest first, each with its targets and their jobs."""
    rows = publishing_service.list_posts(
        db, context.workspace.id, status=post_status, limit=limit, offset=offset
    )
    return [schemas.PostPublic.from_post(post) for post in rows]


@router.get(
    "/{workspace_id}/{post_id}",
    response_model=schemas.PostPublic,
    summary="Get one post with its targets and jobs",
)
def get_post(
    post_id: uuid.UUID,
    context: WorkspaceContext = Depends(require_permission(POST_READ)),
    db: Session = Depends(get_db),
) -> schemas.PostPublic:
    """Another workspace's post id answers ``404``, never ``403``."""
    try:
        post = publishing_service.get_post(db, context.workspace.id, post_id)
    except publishing_service.PublishingError as exc:
        raise _translate(exc) from exc
    return schemas.PostPublic.from_post(post)


# ---------------------------------------------------------------------------
# Compose
# ---------------------------------------------------------------------------
@router.post(
    "/{workspace_id}",
    response_model=schemas.PostPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Compose a draft post",
)
def create_post(
    payload: schemas.PostCreateRequest,
    context: WorkspaceContext = Depends(require_permission(POST_CREATE)),
    db: Session = Depends(get_db),
) -> schemas.PostPublic:
    """Records the video plus the base caption.

    ``409`` means the video is still processing; ``404`` means it is not in this
    workspace. A draft publishes nothing until ``POST .../{post_id}/publish``.
    """
    try:
        post = publishing_service.create_post(
            db,
            workspace_id=context.workspace.id,
            user_id=context.member.user_id,
            media_id=payload.media_id,
            title=payload.title,
            base_caption=payload.base_caption,
        )
    except (
        publishing_service.PublishingError,
        media_service.MediaError,
    ) as exc:
        raise _translate(exc) from exc

    db.commit()
    return schemas.PostPublic.from_post(post)


# ---------------------------------------------------------------------------
# Publish everywhere
# ---------------------------------------------------------------------------
@router.post(
    "/{workspace_id}/{post_id}/publish",
    response_model=schemas.PostPublic,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Publish this post to every selected destination",
)
def publish_everywhere(
    post_id: uuid.UUID,
    payload: schemas.PublishEverywhereRequest,
    context: WorkspaceContext = Depends(require_permission(POST_PUBLISH)),
    db: Session = Depends(get_db),
) -> schemas.PostPublic:
    """Validates every destination, then dispatches one independent job each.

    Each target that fails validation is recorded as ``skipped`` with its
    reason and does not affect the others. ``409`` with the reasons listed means
    *every* destination was rejected, so nothing was dispatched; ``422`` means
    no destination was selected at all.
    """
    selections = [
        publishing_service.TargetSelection(
            social_account_id=target.social_account_id,
            caption=target.caption,
            title=target.title,
        )
        for target in payload.targets
    ]
    try:
        post = publishing_service.publish_everywhere(
            db,
            workspace_id=context.workspace.id,
            post_id=post_id,
            selections=selections,
        )
    except (
        publishing_service.PublishingError,
        media_service.MediaError,
    ) as exc:
        raise _translate(exc) from exc

    db.commit()
    return schemas.PostPublic.from_post(post)


__all__ = ["router"]
