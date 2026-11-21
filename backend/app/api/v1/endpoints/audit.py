"""Workspace audit trail: a read-only view of recorded security events.

One rule mirrors the rest of the API: **the workspace always comes from the
URL.** :func:`require_permission` resolves it through ``TenantMixin.scoped`` and
answers ``404`` for a workspace the caller is not a member of, so one tenant's
trail is never visible to another.

The endpoint is gated by ``audit:read`` (``owner`` and ``admin`` only) because
the trail names users, addresses, and the actions they took. There is no write
path: an entry is produced by the action it describes, via
:mod:`app.services.audit`.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext, require_permission
from app.core.logging import get_logger
from app.core.permissions import AUDIT_READ
from app.db.session import get_db
from app.schemas import audit as schemas
from app.services import audit as audit_service

router = APIRouter()
logger = get_logger(__name__)


@router.get(
    "/{workspace_id}/audit",
    response_model=schemas.AuditLogListPublic,
    summary="Read the workspace audit trail, newest first",
)
def list_audit_logs(
    limit: int = Query(
        default=audit_service.DEFAULT_PAGE_SIZE,
        ge=1,
        le=audit_service.MAX_PAGE_SIZE,
        description="Maximum number of entries to return.",
    ),
    before: datetime | None = Query(
        default=None,
        description="Only return entries created strictly before this timestamp.",
    ),
    action: str | None = Query(
        default=None,
        max_length=80,
        description="Only return entries whose action starts with this prefix (e.g. 'auth.').",
    ),
    context: WorkspaceContext = Depends(require_permission(AUDIT_READ)),
    db: Session = Depends(get_db),
) -> schemas.AuditLogListPublic:
    """Page through the trail, newest first.

    ``before`` is a cursor: pass the ``created_at`` of the last entry you saw to
    fetch the next page without skipping rows written while you were reading.
    """
    entries = audit_service.list_events(
        db,
        workspace_id=context.workspace.id,
        limit=limit,
        before=before,
        action_prefix=action,
    )
    return schemas.AuditLogListPublic(
        items=[schemas.AuditLogPublic.from_entry(entry) for entry in entries],
        count=len(entries),
        limit=limit,
    )
