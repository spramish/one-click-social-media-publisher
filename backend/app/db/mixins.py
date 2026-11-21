"""Reusable column mixins shared by the ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Select, Uuid, func, select
from sqlalchemy.orm import Mapped, declared_attr, mapped_column


class UUIDPrimaryKeyMixin:
    """UUID v4 primary key, generated in the application with a DB fallback.

    The application generates the UUID so identifiers exist before the row is
    flushed (useful for Celery idempotency keys). ``gen_random_uuid()`` is a
    built-in PostgreSQL 13+ function and covers raw SQL inserts.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )


class TimestampMixin:
    """Timezone-aware ``created_at`` / ``updated_at`` bookkeeping columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SoftDeleteMixin:
    """Adds a nullable ``deleted_at`` marker so business history is preserved."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    @property
    def is_deleted(self) -> bool:
        """True when the row has been soft-deleted."""
        return self.deleted_at is not None


class TenantMixin:
    """Adds the ``workspace_id`` tenant discriminator plus scoped queries.

    Tenant isolation is enforced at two layers:

    1. every tenant-scoped table carries a non-nullable ``workspace_id`` foreign
       key with ``ON DELETE CASCADE``;
    2. :meth:`scoped` is the required entry point for tenant queries — it filters
       on ``workspace_id`` and hides soft-deleted rows unless explicitly asked
       for. Services must never build tenant queries with a bare ``select()``.
    """

    @declared_attr
    def workspace_id(cls) -> Mapped[uuid.UUID]:  # noqa: N805
        return mapped_column(
            Uuid(as_uuid=True),
            ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )

    @classmethod
    def scoped(cls, workspace_id: uuid.UUID, *, include_deleted: bool = False) -> Select:
        """Return a ``SELECT`` limited to a single workspace.

        Args:
            workspace_id: The tenant every returned row must belong to.
            include_deleted: When ``True``, soft-deleted rows are also returned.
        """
        stmt = select(cls).where(cls.workspace_id == workspace_id)
        if not include_deleted and hasattr(cls, "deleted_at"):
            stmt = stmt.where(cls.deleted_at.is_(None))
        return stmt
