"""Append-only audit trail of security-relevant events (Phase 13)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import UUIDPrimaryKeyMixin


class AuditLog(UUIDPrimaryKeyMixin, Base):
    """One recorded action, written next to the change it describes.

    A row is **append-only**: it is never updated or deleted by the application,
    so the trail cannot be rewritten after the fact. It records *who* did *what*
    to *which* object, from *where* — the four questions an incident review asks.

    ``workspace_id`` and ``user_id`` are nullable with ``ON DELETE SET NULL``:
    the audit trail must outlive the workspace or account it refers to, because
    "the workspace was deleted" is itself a fact worth keeping. ``action`` is a
    plain string rather than an enum: the set of auditable actions grows with
    every feature, and a widening of the catalogue should not require a schema
    migration. Conventions are documented in :mod:`app.services.audit`.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_workspace_created", "workspace_id", "created_at"),
        Index("ix_audit_logs_user_created", "user_id", "created_at"),
        Index("ix_audit_logs_action_created", "action", "created_at"),
    )

    #: Dotted action code, e.g. ``auth.login.succeeded`` or ``ai_model.created``.
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
    )
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    #: The kind and id of the object acted on, when there is one (e.g. ``ai_model``).
    target_type: Mapped[str | None] = mapped_column(String(60))
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    #: Free-form context. Must never contain a secret, token, or password.
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AuditLog action={self.action} user_id={self.user_id}>"
