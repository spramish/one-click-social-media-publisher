"""AI model registry administration: register, prioritise, monitor, review.

These routes configure the router in :mod:`app.services.ai_router`. Four things
are worth stating up front:

* **The registry is instance-wide; the guard is a workspace role.** Model
  configuration governs the whole server — it is not per-tenant data — but the
  API's only authorization primitive is a workspace membership, so the routes
  are workspace-scoped and gated by ``ai:manage`` (granted to the ``owner`` and
  ``admin`` system roles). The workspace in the path therefore authorizes the
  caller; it does not partition the registry.
* **No route ever returns or accepts a credential.** A model names the
  environment variable that holds its key; the key is read from the process
  environment when the router calls the model.
* **Every write is explicit.** ``PATCH`` applies exactly the fields present, so
  reordering priorities and toggling ``enabled`` are ordinary partial updates.
* **Reads are the audit surface.** ``GET .../models`` shows the health the
  router observed, ``.../models/performance`` aggregates the attempt log, and
  ``.../tasks`` plus ``.../tasks/{id}`` show what ran and which model answered.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext, require_permission
from app.core.logging import get_logger
from app.core.permissions import AI_MANAGE
from app.core.security import utcnow
from app.db.session import get_db
from app.schemas import ai_model as schemas
from app.services import ai_router
from app.services import audit as audit_service

router = APIRouter()
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _translate(exc: Exception) -> HTTPException:
    """Map a registry failure onto the documented status code."""
    if isinstance(exc, ai_router.AIModelNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ai_router.AIModelConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="The AI model registry could not complete this request.",
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
@router.get(
    "/{workspace_id}/models",
    response_model=list[schemas.AIModelPublic],
    summary="List the AI models in priority order",
)
def list_models(
    enabled_only: bool = Query(default=False, description="Only models the router may call."),
    context: WorkspaceContext = Depends(require_permission(AI_MANAGE)),
    db: Session = Depends(get_db),
) -> list[schemas.AIModelPublic]:
    """The registry as the router walks it: lowest priority first."""
    rows = ai_router.list_models(db, enabled_only=enabled_only)
    return [schemas.AIModelPublic.from_model(row) for row in rows]


@router.post(
    "/{workspace_id}/models",
    response_model=schemas.AIModelPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Register an AI model",
)
def create_model(
    payload: schemas.AIModelCreate,
    request: Request,
    context: WorkspaceContext = Depends(require_permission(AI_MANAGE)),
    db: Session = Depends(get_db),
) -> schemas.AIModelPublic:
    """Add a model to the registry.

    ``409`` when the slug is taken, ``422`` for a malformed body. ``base_url``
    may be blank: the service fills in the documented default for the kind.
    """
    try:
        model = ai_router.create_model(
            db,
            name=payload.name,
            display_name=payload.display_name,
            kind=payload.kind,
            model=payload.model,
            base_url=payload.base_url,
            credential_env=payload.credential_env,
            priority=payload.priority,
            enabled=payload.enabled,
            supports=(
                [kind.value for kind in payload.supports]
                if payload.supports is not None
                else None
            ),
            timeout_seconds=payload.timeout_seconds,
            max_output_tokens=payload.max_output_tokens,
            temperature=payload.temperature,
        )
    except ai_router.AIModelError as exc:
        raise _translate(exc) from exc

    audit_service.record(
        db,
        action=audit_service.AI_MODEL_CREATED,
        user_id=context.member.user_id,
        workspace_id=context.workspace.id,
        request=request,
        target_type="ai_model",
        target_id=model.id,
        detail={"name": model.name, "kind": model.kind.value, "priority": model.priority},
    )
    db.commit()
    return schemas.AIModelPublic.from_model(model)


@router.patch(
    "/{workspace_id}/models/{model_id}",
    response_model=schemas.AIModelPublic,
    summary="Update a model's configuration, priority or enabled flag",
)
def update_model(
    model_id: uuid.UUID,
    payload: schemas.AIModelUpdate,
    request: Request,
    context: WorkspaceContext = Depends(require_permission(AI_MANAGE)),
    db: Session = Depends(get_db),
) -> schemas.AIModelPublic:
    """Change only the fields present in the body.

    Reordering the failover list is a sequence of priority updates; taking a
    model out of service without deleting it is ``{"enabled": false}``.
    """
    fields = payload.model_dump(exclude_unset=True)
    if fields.get("supports") is not None:
        fields["supports"] = [kind.value for kind in fields["supports"]]
    try:
        model = ai_router.update_model(db, model_id, **fields)
    except ai_router.AIModelError as exc:
        raise _translate(exc) from exc

    audit_service.record(
        db,
        action=audit_service.AI_MODEL_UPDATED,
        user_id=context.member.user_id,
        workspace_id=context.workspace.id,
        request=request,
        target_type="ai_model",
        target_id=model.id,
        detail={"name": model.name, "changed": sorted(fields.keys())},
    )
    db.commit()
    return schemas.AIModelPublic.from_model(model)


@router.delete(
    "/{workspace_id}/models/{model_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a model from the registry",
)
def delete_model(
    model_id: uuid.UUID,
    request: Request,
    context: WorkspaceContext = Depends(require_permission(AI_MANAGE)),
    db: Session = Depends(get_db),
) -> Response:
    """Delete a model. Its past attempts are kept and still name it."""
    try:
        removed = ai_router.get_model(db, model_id)
        ai_router.delete_model(db, model_id)
    except ai_router.AIModelError as exc:
        raise _translate(exc) from exc
    audit_service.record(
        db,
        action=audit_service.AI_MODEL_DELETED,
        user_id=context.member.user_id,
        workspace_id=context.workspace.id,
        request=request,
        target_type="ai_model",
        target_id=model_id,
        detail={"name": removed.name},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Availability monitoring
# ---------------------------------------------------------------------------
@router.post(
    "/{workspace_id}/models/health-check",
    response_model=schemas.AIHealthSweepPublic,
    summary="Probe every enabled model now",
)
def health_check(
    context: WorkspaceContext = Depends(require_permission(AI_MANAGE)),
    db: Session = Depends(get_db),
) -> schemas.AIHealthSweepPublic:
    """Run the availability sweep on demand, refreshing every model's status."""
    result = ai_router.health_sweep(db, force=True)
    db.commit()
    return schemas.AIHealthSweepPublic.from_result(result)


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------
@router.get(
    "/{workspace_id}/models/performance",
    response_model=list[schemas.AIModelPerformancePublic],
    summary="Success rate and average latency per model",
)
def model_performance(
    window_days: int = Query(default=30, ge=1, le=365),
    context: WorkspaceContext = Depends(require_permission(AI_MANAGE)),
    db: Session = Depends(get_db),
) -> list[schemas.AIModelPerformancePublic]:
    """Aggregate the attempt log over the last ``window_days`` days."""
    since = utcnow() - timedelta(days=window_days)
    performance = ai_router.performance_by_model(db, since=since)
    return [
        schemas.AIModelPerformancePublic.from_performance(row)
        for row in performance.values()
    ]


# ---------------------------------------------------------------------------
# Task history / execution tracking
# ---------------------------------------------------------------------------
@router.get(
    "/{workspace_id}/tasks",
    response_model=schemas.AITaskListPublic,
    summary="Recent AI tasks, newest first",
)
def list_tasks(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: WorkspaceContext = Depends(require_permission(AI_MANAGE)),
    db: Session = Depends(get_db),
) -> schemas.AITaskListPublic:
    """Each task is collapsed from its attempts, naming the model that answered."""
    items, total = ai_router.list_recent_tasks(db, limit=limit, offset=offset)
    return schemas.AITaskListPublic(
        items=[schemas.AITaskPublic.from_summary(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{workspace_id}/tasks/{task_id}",
    response_model=schemas.AITaskAttemptListPublic,
    summary="Every attempt made while routing one task",
)
def list_task_attempts(
    task_id: uuid.UUID,
    context: WorkspaceContext = Depends(require_permission(AI_MANAGE)),
    db: Session = Depends(get_db),
) -> schemas.AITaskAttemptListPublic:
    """The failover chain for one task: which models were tried, in what order."""
    items, total = ai_router.list_attempts(db, task_id=task_id, limit=100, offset=0)
    return schemas.AITaskAttemptListPublic(
        items=[schemas.AITaskAttemptPublic.from_attempt(item) for item in items],
        total=total,
        limit=100,
        offset=0,
    )


__all__ = ["router"]
