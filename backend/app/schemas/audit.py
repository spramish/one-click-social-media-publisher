"""Response schemas for the workspace audit trail (Phase 13).

Read-only by construction: nothing in this module accepts a write. An audit
entry is produced by the action it describes, never by the API consumer.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.models.audit import AuditLog
from app.services.audit import MAX_PAGE_SIZE


class AuditLogPublic(BaseModel):
    """One recorded event."""

    id: uuid.UUID
    action: str
    user_id: uuid.UUID | None = None
    workspace_id: uuid.UUID | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    target_type: str | None = None
    target_id: uuid.UUID | None = None
    detail: dict[str, Any] | None = None
    created_at: datetime

    @classmethod
    def from_entry(cls, entry: AuditLog) -> "AuditLogPublic":
        return cls(
            id=entry.id,
            action=entry.action,
            user_id=entry.user_id,
            workspace_id=entry.workspace_id,
            ip_address=entry.ip_address,
            user_agent=entry.user_agent,
            target_type=entry.target_type,
            target_id=entry.target_id,
            detail=entry.detail,
            created_at=entry.created_at,
        )


class AuditLogListPublic(BaseModel):
    """A page of audit entries, newest first."""

    items: list[AuditLogPublic]
    count: int
    limit: int
    #: Maximum the API will ever return for one request.
    max_limit: int = MAX_PAGE_SIZE


__all__ = ["AuditLogListPublic", "AuditLogPublic"]
