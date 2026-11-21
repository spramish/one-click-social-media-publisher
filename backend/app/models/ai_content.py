"""AI-generated content drafts."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, Uuid, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.enums import AIContentKind
from app.db.mixins import TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column

if TYPE_CHECKING:
    from app.models.post import Post


class AIGeneratedContent(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """A single AI suggestion, kept so the user can review before publishing.

    Nothing here is ever published automatically: ``is_accepted`` flips to
    ``True`` only when the user explicitly applies the suggestion to a post.
    ``post_id`` is nullable because a user may generate ideas before choosing a
    post.
    """

    __tablename__ = "ai_generated_content"
    __table_args__ = (
        Index("ix_ai_generated_content_workspace_created", "workspace_id", "created_at"),
    )

    post_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("posts.id", ondelete="SET NULL"),
        index=True,
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    kind: Mapped[AIContentKind] = mapped_column(
        enum_column(AIContentKind, "ai_content_kind"),
        nullable=False,
    )
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    output: Mapped[str] = mapped_column(Text, nullable=False)
    tone: Mapped[str | None] = mapped_column(String(40))
    is_accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())

    post: Mapped["Post | None"] = relationship("Post")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AIGeneratedContent id={self.id} kind={self.kind}>"
