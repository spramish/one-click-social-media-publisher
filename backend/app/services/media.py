"""Media library: validation, resumable uploads, and FFmpeg processing.

The upload pipeline has three stages, all driven from here:

1. **declare** — :func:`create_upload_session` validates the declared MIME type
   and size *before* any bytes move, and writes a small session record next to
   the partially uploaded file.
2. **transfer** — :func:`append_chunk` appends at an explicit offset. A chunk
   that does not start exactly where the previous one ended is refused with the
   offset the server expects, which is what makes the protocol resumable: a
   dropped connection costs one chunk, not the whole upload.
3. **finalise** — :func:`finalize_upload` verifies the received byte count,
   hashes the file, inspects it with FFprobe, moves it to its permanent key, and
   writes the :class:`~app.models.media.MediaFile` row.

Nothing is recorded as ``ready`` on the strength of a client's claims: the size
is the number of bytes actually written, and duration/resolution come from
FFmpeg reading the stored file.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import utcnow
from app.db.enums import MediaStatus, StorageBackend
from app.models.media import MediaFile
from app.models.post import Post
from app.services import storage as storage_service
from app.services import video as video_service
from app.services.storage import LocalStorage, ObjectNotFoundError, StorageError

logger = get_logger(__name__)

#: Directory (relative to the media root) holding in-flight uploads.
_SESSION_PREFIX = ".uploads"

#: Container MIME type → file extension for the stored master file.
_MIME_EXTENSIONS: dict[str, str] = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
    "video/x-msvideo": ".avi",
}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class MediaError(Exception):
    """Base class for media domain failures."""


class MediaNotFoundError(MediaError):
    """No such media file in this workspace."""


class MediaInUseError(MediaError):
    """The file is referenced by a post and must not be removed."""


class UnsupportedMimeTypeError(MediaError):
    """The declared container type is not on the configured allow-list."""


class FileTooLargeError(MediaError):
    """The file exceeds ``MEDIA_MAX_UPLOAD_BYTES``."""


class UploadSessionNotFoundError(MediaError):
    """No such upload session in this workspace (or it already finished)."""


class UploadSessionExpiredError(MediaError):
    """The upload session outlived ``MEDIA_UPLOAD_SESSION_TTL_HOURS``."""


class UploadOffsetMismatchError(MediaError):
    """A chunk arrived out of order; the client must resume from ``expected``."""

    def __init__(self, expected: int) -> None:
        self.expected = expected
        super().__init__(
            f"Chunk does not continue the upload. Send the next chunk starting at byte {expected}."
        )


class UploadIncompleteError(MediaError):
    """Finalisation was requested before every declared byte had arrived."""

    def __init__(self, expected: int, received: int) -> None:
        self.expected = expected
        self.received = received
        super().__init__(
            f"Upload is incomplete: {received} of {expected} bytes were received."
        )


class UploadSizeExceededError(MediaError):
    """A chunk would push the upload past the size it declared."""

    def __init__(self, declared: int, projected: int) -> None:
        self.declared = declared
        self.projected = projected
        super().__init__(
            f"This upload declared {declared} bytes; the received data would reach {projected}."
        )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def extension_for(mime_type: str) -> str:
    """File extension for the stored master file."""
    return _MIME_EXTENSIONS.get(mime_type, ".bin")


def validate_mime_type(mime_type: str) -> str:
    """Return the normalised MIME type, or raise if it is not allowed."""
    normalised = mime_type.strip().lower()
    allowed = settings.media_allowed_mime_types_list
    if normalised not in allowed:
        raise UnsupportedMimeTypeError(
            f"{mime_type!r} is not an accepted video type. Allowed types: {', '.join(allowed)}."
        )
    return normalised


def validate_size(size_bytes: int) -> int:
    """Return the size, or raise if it exceeds the configured ceiling."""
    if size_bytes > settings.media_max_upload_bytes:
        raise FileTooLargeError(
            f"This file is {size_bytes} bytes; the limit is "
            f"{settings.media_max_upload_bytes} bytes."
        )
    return size_bytes


# ---------------------------------------------------------------------------
# Upload sessions
# ---------------------------------------------------------------------------
def session_json_key(upload_id: uuid.UUID) -> str:
    return f"{_SESSION_PREFIX}/{upload_id}.json"


def session_part_key(upload_id: uuid.UUID) -> str:
    return f"{_SESSION_PREFIX}/{upload_id}.part"


@dataclass(slots=True)
class UploadSession:
    """Server-side state of one in-flight upload."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID
    original_filename: str
    mime_type: str
    declared_size_bytes: int
    received_bytes: int
    replace_media_id: uuid.UUID | None
    created_at: datetime
    expires_at: datetime

    @property
    def part_key(self) -> str:
        return session_part_key(self.id)

    @property
    def json_key(self) -> str:
        return session_json_key(self.id)


def _save_session(storage: LocalStorage, session: UploadSession) -> None:
    payload = {
        "id": str(session.id),
        "workspace_id": str(session.workspace_id),
        "user_id": str(session.user_id),
        "original_filename": session.original_filename,
        "mime_type": session.mime_type,
        "declared_size_bytes": session.declared_size_bytes,
        "received_bytes": session.received_bytes,
        "replace_media_id": str(session.replace_media_id) if session.replace_media_id else None,
        "created_at": session.created_at.isoformat(),
        "expires_at": session.expires_at.isoformat(),
    }
    storage.write_bytes(session.json_key, json.dumps(payload).encode("utf-8"))


def _load_payload(storage: LocalStorage, upload_id: uuid.UUID) -> UploadSession:
    try:
        raw = storage.read_bytes(session_json_key(upload_id))
    except ObjectNotFoundError as exc:
        raise UploadSessionNotFoundError("upload session not found") from exc
    data = json.loads(raw.decode("utf-8"))
    replace_media_id = data.get("replace_media_id")
    return UploadSession(
        id=uuid.UUID(data["id"]),
        workspace_id=uuid.UUID(data["workspace_id"]),
        user_id=uuid.UUID(data["user_id"]),
        original_filename=data["original_filename"],
        mime_type=data["mime_type"],
        declared_size_bytes=int(data["declared_size_bytes"]),
        received_bytes=int(data["received_bytes"]),
        replace_media_id=uuid.UUID(replace_media_id) if replace_media_id else None,
        created_at=datetime.fromisoformat(data["created_at"]),
        expires_at=datetime.fromisoformat(data["expires_at"]),
    )


def purge_expired_sessions(storage: LocalStorage) -> int:
    """Delete abandoned uploads. Best effort — never fails the caller."""
    removed = 0
    try:
        keys = [key for key, _size in storage.iter_keys(_SESSION_PREFIX)]
    except StorageError:  # pragma: no cover - unreadable root
        return 0

    for key in keys:
        if not key.endswith(".json"):
            continue
        try:
            session = _load_payload(storage, uuid.UUID(Path(key).stem))
        except (UploadSessionNotFoundError, ValueError, KeyError, json.JSONDecodeError):
            continue
        if session.expires_at <= utcnow():
            discard_upload_session(storage, session)
            removed += 1
    if removed:
        logger.info("expired upload sessions removed", extra={"count": removed})
    return removed


def create_upload_session(
    storage: LocalStorage,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    filename: str,
    mime_type: str,
    size_bytes: int,
    replace_media_id: uuid.UUID | None = None,
) -> UploadSession:
    """Declare an upload. Raises before any data is written if it is invalid."""
    normalised_mime = validate_mime_type(mime_type)
    validate_size(size_bytes)
    purge_expired_sessions(storage)

    now = utcnow()
    session = UploadSession(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        user_id=user_id,
        original_filename=filename,
        mime_type=normalised_mime,
        declared_size_bytes=size_bytes,
        received_bytes=0,
        replace_media_id=replace_media_id,
        created_at=now,
        expires_at=now + timedelta(hours=settings.media_upload_session_ttl_hours),
    )
    storage.write_bytes(session.part_key, b"")
    _save_session(storage, session)
    logger.info(
        "upload session created",
        extra={
            "upload_id": str(session.id),
            "workspace_id": str(workspace_id),
            "declared_size_bytes": size_bytes,
            "mime_type": normalised_mime,
        },
    )
    return session


def load_upload_session(
    storage: LocalStorage,
    upload_id: uuid.UUID,
    *,
    workspace_id: uuid.UUID,
) -> UploadSession:
    """Load a session, hiding other tenants' uploads and expired ones."""
    session = _load_payload(storage, upload_id)
    if session.workspace_id != workspace_id:
        # Same reasoning as the workspace routes: never confirm that another
        # tenant's object exists.
        raise UploadSessionNotFoundError("upload session not found")
    if session.expires_at <= utcnow():
        discard_upload_session(storage, session)
        raise UploadSessionExpiredError(
            "This upload expired before it finished. Start the upload again."
        )
    return session


def append_chunk(
    storage: LocalStorage,
    session: UploadSession,
    *,
    offset: int,
    chunk: bytes,
) -> UploadSession:
    """Append ``chunk`` at ``offset``, or refuse with the expected offset."""
    if offset != session.received_bytes:
        raise UploadOffsetMismatchError(expected=session.received_bytes)

    projected = session.received_bytes + len(chunk)
    if projected > session.declared_size_bytes:
        raise UploadSizeExceededError(declared=session.declared_size_bytes, projected=projected)
    validate_size(projected)

    written = storage.append_chunk(session.part_key, chunk)
    session.received_bytes = written
    _save_session(storage, session)
    return session


def discard_upload_session(storage: LocalStorage, session: UploadSession) -> None:
    """Remove an in-flight upload's temporary file and its session record."""
    storage.delete(session.part_key)
    storage.delete(session.json_key)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def finalize_upload(
    db: Session,
    storage: LocalStorage,
    session: UploadSession,
) -> MediaFile:
    """Turn a fully transferred upload into a stored, inspected media file.

    Raises:
        UploadIncompleteError: fewer bytes arrived than were declared.
        FFmpegUnavailableError: the processing pipeline is unavailable.
        VideoInspectionError: the bytes are not a readable video container.
    """
    if session.received_bytes != session.declared_size_bytes:
        raise UploadIncompleteError(
            expected=session.declared_size_bytes,
            received=session.received_bytes,
        )

    part_path = storage.path_for(session.part_key)
    checksum = _sha256_file(part_path)
    # FFprobe reads the real bytes, so a renamed non-video is rejected here even
    # though it passed the declared-MIME check.
    info = video_service.probe(part_path)

    existing: MediaFile | None = None
    if session.replace_media_id is not None:
        existing = get_media(db, session.workspace_id, session.replace_media_id)

    media_id = existing.id if existing is not None else uuid.uuid4()
    extension = extension_for(session.mime_type)
    key = storage_service.original_key(session.workspace_id, media_id, extension)
    thumb_key = storage_service.thumbnail_key(session.workspace_id, media_id)

    previous_key = existing.storage_key if existing is not None else None
    previous_thumbnail = existing.thumbnail_key if existing is not None else None

    storage.adopt(part_path, key)
    if previous_key is not None and previous_key != key:
        storage.delete(previous_key)
    if previous_thumbnail is not None and previous_thumbnail != thumb_key:
        storage.delete(previous_thumbnail)

    thumbnail_created = video_service.generate_thumbnail(
        storage.path_for(key),
        storage.path_for(thumb_key),
        duration_seconds=info.duration_seconds,
    )
    if not thumbnail_created:
        # A missing thumbnail must not fail an otherwise valid upload.
        storage.delete(thumb_key)

    media = existing or MediaFile(id=media_id, workspace_id=session.workspace_id)
    media.uploaded_by_id = session.user_id
    media.storage_backend = StorageBackend.LOCAL
    media.storage_key = key
    media.original_filename = session.original_filename
    media.mime_type = session.mime_type
    media.size_bytes = session.received_bytes
    media.checksum_sha256 = checksum
    media.duration_seconds = info.duration_seconds
    media.width = info.width
    media.height = info.height
    media.thumbnail_key = thumb_key if thumbnail_created else None
    media.status = MediaStatus.READY
    media.deleted_at = None
    db.add(media)
    db.flush()

    discard_upload_session(storage, session)
    logger.info(
        "media finalized",
        extra={
            "media_id": str(media.id),
            "workspace_id": str(session.workspace_id),
            "replaced": existing is not None,
            "size_bytes": media.size_bytes,
            "duration_seconds": media.duration_seconds,
            "resolution": info.resolution,
        },
    )
    return media


# ---------------------------------------------------------------------------
# Library reads
# ---------------------------------------------------------------------------
def list_media(
    db: Session,
    workspace_id: uuid.UUID,
    *,
    status: MediaStatus | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[MediaFile]:
    """Newest first. The workspace scope always comes from ``MediaFile.scoped``."""
    stmt = MediaFile.scoped(workspace_id)
    if status is not None:
        stmt = stmt.where(MediaFile.status == status)
    if search:
        # ``ilike`` needs the wildcards, and the escape keeps a literal % or _
        # in a filename from turning into a wildcard.
        pattern = f"%{search.replace('%', r'\%').replace('_', r'\_')}%"
        stmt = stmt.where(MediaFile.original_filename.ilike(pattern, escape="\\"))
    # ``created_at`` comes from ``now()``, which is the *transaction* timestamp:
    # rows written in one transaction share it exactly. The id tiebreaker keeps
    # the order stable, without which ``limit``/``offset`` paging could repeat or
    # skip rows.
    stmt = (
        stmt.order_by(MediaFile.created_at.desc(), MediaFile.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(stmt).all())


def get_media(db: Session, workspace_id: uuid.UUID, media_id: uuid.UUID) -> MediaFile:
    """One media file, or raise :class:`MediaNotFoundError`."""
    media = db.scalar(MediaFile.scoped(workspace_id).where(MediaFile.id == media_id))
    if media is None:
        raise MediaNotFoundError("media file not found in this workspace")
    return media


def media_stats(db: Session, workspace_id: uuid.UUID) -> tuple[int, int, int, int]:
    """``(file_count, total_bytes, ready_count, failed_count)`` for a workspace.

    An aggregate cannot reuse :meth:`TenantMixin.scoped`, so the tenant filter is
    written out; the workspace id still comes from the caller's resolved
    context and never from user input.
    """
    row = db.execute(
        select(
            func.count(MediaFile.id),
            func.coalesce(func.sum(MediaFile.size_bytes), 0),
            func.count(MediaFile.id).filter(MediaFile.status == MediaStatus.READY),
            func.count(MediaFile.id).filter(MediaFile.status == MediaStatus.FAILED),
        ).where(
            MediaFile.workspace_id == workspace_id,
            MediaFile.deleted_at.is_(None),
        )
    ).one()
    return int(row[0]), int(row[1]), int(row[2]), int(row[3])


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------
def assert_not_in_use(db: Session, media: MediaFile) -> None:
    """Block deletion while a live post still points at this file.

    ``posts.media_file_id`` is declared ``ON DELETE RESTRICT``; the check makes
    that intent explicit for a *soft* delete, which the FK would not catch.
    """
    referencing = db.scalar(
        select(func.count())
        .select_from(Post)
        .where(
            Post.media_file_id == media.id,
            Post.deleted_at.is_(None),
        )
    )
    if referencing:
        raise MediaInUseError(
            f"This video is used by {referencing} post(s) and cannot be deleted."
        )


def delete_media(db: Session, storage: LocalStorage, media: MediaFile) -> None:
    """Soft-delete the row and free its bytes.

    The row survives so publishing history keeps its references; the stored
    objects do not, which is the point of "storage management".
    """
    assert_not_in_use(db, media)
    media.status = MediaStatus.DELETED
    media.deleted_at = utcnow()
    db.flush()

    try:
        storage.delete_media_directory(media.workspace_id, media.id)
    except StorageError as exc:  # pragma: no cover - defensive
        logger.warning(
            "stored media could not be removed",
            extra={"media_id": str(media.id), "error": str(exc)},
        )
    logger.info(
        "media deleted",
        extra={"media_id": str(media.id), "workspace_id": str(media.workspace_id)},
    )


__all__ = [
    "FileTooLargeError",
    "MediaError",
    "MediaInUseError",
    "MediaNotFoundError",
    "UnsupportedMimeTypeError",
    "UploadIncompleteError",
    "UploadOffsetMismatchError",
    "UploadSession",
    "UploadSessionExpiredError",
    "UploadSessionNotFoundError",
    "UploadSizeExceededError",
    "append_chunk",
    "assert_not_in_use",
    "create_upload_session",
    "delete_media",
    "discard_upload_session",
    "extension_for",
    "finalize_upload",
    "get_media",
    "list_media",
    "load_upload_session",
    "media_stats",
    "purge_expired_sessions",
    "validate_mime_type",
    "validate_size",
]
