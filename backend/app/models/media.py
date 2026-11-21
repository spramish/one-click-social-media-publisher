"""Uploaded media (video) files."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.enums import MediaStatus, StorageBackend
from app.db.mixins import SoftDeleteMixin, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column

if TYPE_CHECKING:
    from app.models.post import Post
    from app.models.workspace import Workspace


class MediaFile(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A single uploaded video and the metadata FFmpeg extracted from it.

    ``storage_key`` is unique across the whole table (not per workspace) because
    it is the physical object key — either a path on the local volume or an S3
    object key.
    """

    __tablename__ = "media_files"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="size_bytes_non_negative"),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="duration_non_negative",
        ),
    )

    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    storage_backend: Mapped[StorageBackend] = mapped_column(
        enum_column(StorageBackend, "storage_backend"),
        nullable=False,
        server_default=StorageBackend.LOCAL.value,
    )
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(127), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    duration_seconds: Mapped[float | None] = mapped_column()
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    thumbnail_key: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[MediaStatus] = mapped_column(
        enum_column(MediaStatus, "media_status"),
        nullable=False,
        server_default=MediaStatus.PENDING.value,
    )

    workspace: Mapped["Workspace"] = relationship(
        "Workspace",
    )
    posts: Mapped[list["Post"]] = relationship("Post", back_populates="media_file")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<MediaFile id={self.id} filename={self.original_filename!r}>"
