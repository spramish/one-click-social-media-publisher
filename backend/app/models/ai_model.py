"""AI model registry and per-attempt execution log (Phase 12).

Two tables back the free, self-managing AI routing:

* :class:`AIModel` — the ordered list of models the router may use. An operator
  (a superuser) registers each model, gives it a ``priority`` (lower is tried
  first) and an optional ``credential_env`` naming the environment variable
  that holds its key. The **key itself is never stored** here — only the name of
  the variable that carries it — so a database dump cannot leak a credential.
* :class:`AITaskAttempt` — one row per model attempt made while routing a single
  task. It is the usage log, the execution trail, the task history and the
  performance record in one append-only table: grouping by ``task_id`` shows the
  failover chain, and aggregating by ``model_name`` shows each model's latency
  and success rate.

Neither table uses ``TenantMixin``: model configuration is application-wide (it
governs the whole server), and an attempt may occur before a workspace is even
relevant. ``workspace_id``/``user_id`` are therefore nullable *references* kept
for audit, not tenant discriminators.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    false,
    func,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.enums import AIContentKind, AIModelKind, AIModelStatus, AITaskStatus
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column


class AIModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One model the router may call, with its place in the priority order.

    ``credential_env`` names an environment variable (for example
    ``GROQ_API_KEY``) rather than holding the secret, so the credential stays in
    the process environment and out of the database. A model whose provider
    needs no key (Ollama) leaves it ``None``.
    """

    __tablename__ = "ai_models"
    __table_args__ = (
        # The router's working set: enabled models in priority order.
        Index("ix_ai_models_enabled_priority", "enabled", "priority"),
    )

    #: Stable slug used in logs and by the API (``groq-llama``).
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[AIModelKind] = mapped_column(
        enum_column(AIModelKind, "ai_model_kind"),
        nullable=False,
    )
    #: The provider's own model identifier (``llama-3.1-8b-instant``).
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    #: API origin for the OpenAI-compatible kind; Ollama's host otherwise.
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Name of the environment variable holding the key. ``None`` = no key.
    credential_env: Mapped[str | None] = mapped_column(String(80))
    #: Lower runs first. Ties break on ``created_at`` so the order is stable.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    #: Content kinds this model may serve; ``NULL`` means "every kind".
    supports: Mapped[list | None] = mapped_column(JSONB)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="60")
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="800")
    temperature: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.8")

    # --- health / availability monitoring --------------------------------
    status: Mapped[AIModelStatus] = mapped_column(
        enum_column(AIModelStatus, "ai_model_status"),
        nullable=False,
        server_default=AIModelStatus.UNKNOWN.value,
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(Text)

    attempts: Mapped[list["AITaskAttempt"]] = relationship(
        "AITaskAttempt", back_populates="model"
    )

    def serves(self, kind: AIContentKind) -> bool:
        """Whether this model may be used for ``kind`` (an empty list = all)."""
        if not self.supports:
            return True
        return kind.value in self.supports

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AIModel name={self.name} priority={self.priority} enabled={self.enabled}>"


class AITaskAttempt(UUIDPrimaryKeyMixin, Base):
    """One attempt to complete an AI task with one model.

    Rows are append-only and never updated, so they act as both an execution
    trail and a performance sample. ``task_id`` is a plain UUID shared by every
    attempt made for the same user request — it is the id the API echoes back
    and the key the history endpoint groups on. The remote keys are
    ``SET NULL`` so a deleted workspace/user/model never erases the audit trail.
    """

    __tablename__ = "ai_task_attempts"
    __table_args__ = (
        Index("ix_ai_task_attempts_task", "task_id", "created_at"),
        Index("ix_ai_task_attempts_model", "model_name", "created_at"),
    )

    #: Groups the attempts of one routed task. Not a foreign key: the row must
    #: survive even if the originating request is later cleaned up.
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    model_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_models.id", ondelete="SET NULL")
    )
    #: Denormalised so the log still names the model after the row is deleted.
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[AIContentKind] = mapped_column(
        enum_column(AIContentKind, "ai_content_kind"),
        nullable=False,
    )
    status: Mapped[AITaskStatus] = mapped_column(
        enum_column(AITaskStatus, "ai_task_status"),
        nullable=False,
    )
    #: 1-based position of this attempt in the task's overall attempt sequence.
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Wall-clock time the attempt took, when it got as far as a call.
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    #: The exception class name, kept as a short queryable label.
    error_class: Mapped[str | None] = mapped_column(String(80))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    model: Mapped["AIModel | None"] = relationship("AIModel", back_populates="attempts")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AITaskAttempt task={self.task_id} model={self.model_name} status={self.status}>"


__all__ = ["AIModel", "AITaskAttempt"]
