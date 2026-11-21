"""Request/response schemas for uploaded media and resumable uploads.

``MediaFilePublic`` intentionally has no ``storage_key`` field. Clients receive
**signed, expiring URLs** instead: raw object keys and filesystem paths never
leave the server.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.db.enums import MediaStatus, StorageBackend
from app.models.media import MediaFile

#: Path separators and control characters that must never appear in a filename.
_FORBIDDEN_IN_FILENAME = ("/", "\\", "\x00")


class MediaFilePublic(BaseModel):
    """A stored video plus freshly signed URLs for its bytes and thumbnail."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    original_filename: str
    mime_type: str
    size_bytes: int
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    status: MediaStatus
    storage_backend: StorageBackend
    checksum_sha256: str | None = None
    uploaded_by_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    #: Signed URL for the master video file.
    video_url: str
    #: Signed URL for the JPEG thumbnail, when one was generated.
    thumbnail_url: str | None = None
    #: When ``video_url``/``thumbnail_url`` stop working.
    urls_expire_at: datetime

    @classmethod
    def from_media_file(
        cls,
        media: MediaFile,
        *,
        video_url: str,
        thumbnail_url: str | None,
        urls_expire_at: datetime,
    ) -> "MediaFilePublic":
        return cls(
            id=media.id,
            workspace_id=media.workspace_id,
            original_filename=media.original_filename,
            mime_type=media.mime_type,
            size_bytes=media.size_bytes,
            duration_seconds=media.duration_seconds,
            width=media.width,
            height=media.height,
            status=media.status,
            storage_backend=media.storage_backend,
            checksum_sha256=media.checksum_sha256,
            uploaded_by_id=media.uploaded_by_id,
            created_at=media.created_at,
            updated_at=media.updated_at,
            video_url=video_url,
            thumbnail_url=thumbnail_url,
            urls_expire_at=urls_expire_at,
        )


class MediaUploadRequest(BaseModel):
    """Declare an upload before any bytes are sent.

    Sending the size and type up front lets the server refuse an oversized or
    unsupported file *before* a single byte reaches storage.
    """

    filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=3, max_length=127)
    size_bytes: int = Field(gt=0)
    #: When set, the completed upload replaces this media file's bytes in place
    #: instead of creating a new library item.
    replace_media_id: uuid.UUID | None = None

    @field_validator("filename")
    @classmethod
    def _filename_is_bare(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("filename must not be blank")
        if any(token in cleaned for token in _FORBIDDEN_IN_FILENAME):
            raise ValueError("filename must not contain path separators")
        return cleaned

    @field_validator("mime_type")
    @classmethod
    def _mime_is_normalised(cls, value: str) -> str:
        return value.strip().lower()


class UploadSessionPublic(BaseModel):
    """State of an in-progress (resumable) upload.

    A session only exists while it is unfinished: completing or aborting one
    removes its record, and the next ``GET`` answers ``404``.
    """

    id: uuid.UUID
    workspace_id: uuid.UUID
    original_filename: str
    mime_type: str
    declared_size_bytes: int
    #: Bytes durably written so far.
    received_bytes: int
    #: Offset the next chunk must start at — identical to ``received_bytes``.
    offset: int
    chunk_size_bytes: int
    replace_media_id: uuid.UUID | None = None
    created_at: datetime
    expires_at: datetime


class MediaStatsPublic(BaseModel):
    """Aggregate storage usage for one workspace."""

    file_count: int
    total_bytes: int
    ready_count: int
    failed_count: int


class MediaCapabilitiesPublic(BaseModel):
    """What the upload pipeline on this server can do right now.

    The frontend reads this before offering an upload so that a missing FFmpeg
    install is reported up front instead of after a long transfer.
    """

    ffmpeg_available: bool
    max_upload_bytes: int
    chunk_size_bytes: int
    allowed_mime_types: list[str]
    signed_url_ttl_seconds: int
    thumbnail_max_width: int


__all__ = [
    "MediaCapabilitiesPublic",
    "MediaFilePublic",
    "MediaStatsPublic",
    "MediaUploadRequest",
    "UploadSessionPublic",
]
