"""Audit trail — the write path and the read path (Phase 13).

Action codes are dotted, ``<area>.<object>.<verb>``, so a filter or a report can
match on a prefix. The catalogue below is what the application currently emits;
:class:`app.models.audit.AuditLog` stores the code as a plain string, so a new
event needs no migration.

:func:`record` only *stages* a row on the caller's session — it deliberately does
not commit, so an audit entry is written in the same transaction as the change it
describes (they either both land or neither does). A route that must audit a
*rejected* action (a failed login) commits before it raises, exactly as the
refresh endpoint does when it revokes a stolen family.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.rate_limit import client_identity
from app.models.audit import AuditLog

logger = get_logger(__name__)

# --- Action catalogue -------------------------------------------------------
REGISTERED = "auth.registered"
LOGIN_SUCCEEDED = "auth.login.succeeded"
LOGIN_FAILED = "auth.login.failed"
LOGOUT = "auth.logout"
LOGOUT_ALL = "auth.logout_all"
PASSWORD_CHANGED = "auth.password.changed"
PASSWORD_RESET = "auth.password.reset"

AI_MODEL_CREATED = "ai_model.created"
AI_MODEL_UPDATED = "ai_model.updated"
AI_MODEL_DELETED = "ai_model.deleted"

#: Default page size for :func:`list_events`; the API caps callers at this.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def record(
    db: Session,
    *,
    action: str,
    user_id: uuid.UUID | None = None,
    workspace_id: uuid.UUID | None = None,
    request: Request | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
) -> AuditLog | None:
    """Stage one audit entry on ``db`` (the caller commits).

    Never raises: auditing must not be able to fail the action it is recording.
    ``detail`` must never carry a secret — no passwords, tokens, or API keys.
    """
    try:
        entry = AuditLog(
            action=action,
            user_id=user_id,
            workspace_id=workspace_id,
            ip_address=client_identity(request) if request is not None else None,
            user_agent=(request.headers.get("user-agent") if request is not None else None),
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        )
        db.add(entry)
        return entry
    except Exception as exc:  # noqa: BLE001 - an audit failure must never break the request
        logger.warning("could not stage audit entry %s: %s", action, exc)
        return None


def list_events(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    limit: int = DEFAULT_PAGE_SIZE,
    before: datetime | None = None,
    action_prefix: str | None = None,
) -> list[AuditLog]:
    """Most recent audit entries for one workspace, newest first."""
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    stmt = (
        select(AuditLog)
        .where(AuditLog.workspace_id == workspace_id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
    )
    if before is not None:
        stmt = stmt.where(AuditLog.created_at < before)
    if action_prefix:
        stmt = stmt.where(AuditLog.action.startswith(action_prefix))
    return list(db.scalars(stmt).all())


__all__ = [
    "AI_MODEL_CREATED",
    "AI_MODEL_DELETED",
    "AI_MODEL_UPDATED",
    "DEFAULT_PAGE_SIZE",
    "LOGIN_FAILED",
    "LOGIN_SUCCEEDED",
    "LOGOUT",
    "LOGOUT_ALL",
    "MAX_PAGE_SIZE",
    "PASSWORD_CHANGED",
    "PASSWORD_RESET",
    "REGISTERED",
    "list_events",
    "record",
]
