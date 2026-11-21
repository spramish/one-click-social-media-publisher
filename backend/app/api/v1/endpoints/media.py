"""Media library endpoints: resumable upload, inspection, and signed playback.

Three rules shape this module:

* **The workspace always comes from the URL, never from the body.** Every route
  starts from :func:`app.api.deps.require_permission`, which resolves
  ``{workspace_id}`` through ``TenantMixin.scoped`` and answers ``404`` for a
  workspace the caller is not a member of. A ``replace_media_id`` in a request
  body is a *reference*, not an authority: the service re-resolves it inside the
  caller's workspace.
* **Uploads are declared before they are sent.** ``POST /uploads`` validates the
  MIME type, the size, and the filename up front, so an unacceptable file is
  refused before a single byte reaches storage.
* **Clients never see paths.** Files are served through signed, expiring URLs
  minted by :mod:`app.services.storage`; the serving route needs no session
  because the HMAC *is* the authorization.

Error translation is centralised in :func:`_translate` so every media failure
maps to one documented status code.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext, require_permission
from app.core.config import settings
from app.core.logging import get_logger
from app.core.permissions import MEDIA_DELETE, MEDIA_READ, MEDIA_UPLOAD
from app.db.enums import MediaStatus
from app.db.session import get_db
from app.models.media import MediaFile
from app.schemas import auth as auth_schemas
from app.schemas import media as schemas
from app.services import media as media_service
from app.services import storage as storage_service
from app.services import video as video_service
from app.services.storage import LocalStorage, ObjectNotFoundError, get_storage

router = APIRouter()
logger = get_logger(__name__)

#: Largest single request body accepted by ``PUT /uploads/{id}``.
#:
#: The declared size already bounds the *total*, but a chunk is buffered in
#: memory while it is written, so this bounds one request. Four advertised
#: chunks gives a client room to coalesce while keeping the ceiling predictable.
_MAX_CHUNK_BYTES = settings.media_chunk_size_bytes * 4


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _translate(exc: Exception) -> HTTPException:
    """Map a media/FFmpeg/storage failure onto the documented status code."""
    if isinstance(
        exc,
        (
            media_service.MediaNotFoundError,
            media_service.UploadSessionNotFoundError,
            ObjectNotFoundError,
        ),
    ):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, media_service.UnsupportedMimeTypeError):
        return HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc))
    if isinstance(exc, media_service.FileTooLargeError):
        return HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        )
    if isinstance(exc, media_service.UploadOffsetMismatchError):
        # The caller must resume from ``expected``; echoing it in a header keeps
        # the value machine-readable without changing the error envelope.
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
            headers={"X-Upload-Expected-Offset": str(exc.expected)},
        )
    if isinstance(
        exc,
        (
            media_service.UploadIncompleteError,
            media_service.UploadSizeExceededError,
            media_service.MediaInUseError,
        ),
    ):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, media_service.UploadSessionExpiredError):
        return HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc))
    if isinstance(exc, video_service.FFmpegUnavailableError):
        # The request is valid; this server cannot process it right now.
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    if isinstance(exc, video_service.VideoInspectionError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="The media operation could not be completed.",
    )


def _files_path(request: Request, media_id: uuid.UUID, workspace_id: uuid.UUID, variant: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}{settings.api_v1_prefix}/media/{workspace_id}/{media_id}/files/{variant}"


def _media_public(request: Request, media: MediaFile) -> schemas.MediaFilePublic:
    """Shape a media row, minting fresh signed URLs for its bytes."""
    expires_at, signature = storage_service.sign_media_url(media.id, "original")
    video_url = (
        f"{_files_path(request, media.id, media.workspace_id, 'original')}"
        f"?expires={expires_at}&signature={signature}"
    )

    thumbnail_url: str | None = None
    if media.thumbnail_key:
        thumb_expires, thumb_signature = storage_service.sign_media_url(media.id, "thumbnail")
        thumbnail_url = (
            f"{_files_path(request, media.id, media.workspace_id, 'thumbnail')}"
            f"?expires={thumb_expires}&signature={thumb_signature}"
        )

    return schemas.MediaFilePublic.from_media_file(
        media,
        video_url=video_url,
        thumbnail_url=thumbnail_url,
        urls_expire_at=datetime.fromtimestamp(expires_at, tz=UTC),
    )


def _session_public(session: media_service.UploadSession) -> schemas.UploadSessionPublic:
    return schemas.UploadSessionPublic(
        id=session.id,
        workspace_id=session.workspace_id,
        original_filename=session.original_filename,
        mime_type=session.mime_type,
        declared_size_bytes=session.declared_size_bytes,
        received_bytes=session.received_bytes,
        offset=session.received_bytes,
        chunk_size_bytes=settings.media_chunk_size_bytes,
        replace_media_id=session.replace_media_id,
        created_at=session.created_at,
        expires_at=session.expires_at,
    )


# ---------------------------------------------------------------------------
# Capabilities and statistics
# ---------------------------------------------------------------------------
@router.get(
    "/{workspace_id}/capabilities",
    response_model=schemas.MediaCapabilitiesPublic,
    summary="What the upload pipeline on this server can do",
)
def get_capabilities(
    context: WorkspaceContext = Depends(require_permission(MEDIA_READ)),
) -> schemas.MediaCapabilitiesPublic:
    """Tells the client the limits and whether FFmpeg is usable.

    The frontend reads this *before* offering an upload, so a server without
    FFmpeg says so immediately rather than after a multi-gigabyte transfer.
    """
    return schemas.MediaCapabilitiesPublic(
        ffmpeg_available=video_service.pipeline_available(),
        max_upload_bytes=settings.media_max_upload_bytes,
        chunk_size_bytes=settings.media_chunk_size_bytes,
        allowed_mime_types=settings.media_allowed_mime_types_list,
        signed_url_ttl_seconds=settings.media_signed_url_ttl_seconds,
        thumbnail_max_width=settings.media_thumbnail_max_width,
    )


@router.get(
    "/{workspace_id}/stats",
    response_model=schemas.MediaStatsPublic,
    summary="Storage usage for this workspace",
)
def get_stats(
    context: WorkspaceContext = Depends(require_permission(MEDIA_READ)),
    db: Session = Depends(get_db),
) -> schemas.MediaStatsPublic:
    """Counts and total bytes across the workspace's library."""
    file_count, total_bytes, ready_count, failed_count = media_service.media_stats(
        db, context.workspace.id
    )
    return schemas.MediaStatsPublic(
        file_count=file_count,
        total_bytes=total_bytes,
        ready_count=ready_count,
        failed_count=failed_count,
    )


# ---------------------------------------------------------------------------
# Library reads
# ---------------------------------------------------------------------------
@router.get(
    "/{workspace_id}",
    response_model=list[schemas.MediaFilePublic],
    summary="List the workspace's media library",
)
def list_media(
    request: Request,
    media_status: MediaStatus | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: WorkspaceContext = Depends(require_permission(MEDIA_READ)),
    db: Session = Depends(get_db),
) -> list[schemas.MediaFilePublic]:
    """Newest first. Soft-deleted files are never listed."""
    rows = media_service.list_media(
        db,
        context.workspace.id,
        status=media_status,
        search=search,
        limit=limit,
        offset=offset,
    )
    return [_media_public(request, media) for media in rows]


@router.get(
    "/{workspace_id}/{media_id}",
    response_model=schemas.MediaFilePublic,
    summary="Get one media file",
)
def get_media(
    request: Request,
    media_id: uuid.UUID,
    context: WorkspaceContext = Depends(require_permission(MEDIA_READ)),
    db: Session = Depends(get_db),
) -> schemas.MediaFilePublic:
    """Metadata plus freshly signed URLs. Another workspace's id answers 404."""
    try:
        media = media_service.get_media(db, context.workspace.id, media_id)
    except media_service.MediaError as exc:
        raise _translate(exc) from exc
    return _media_public(request, media)


# ---------------------------------------------------------------------------
# Resumable uploads
# ---------------------------------------------------------------------------
@router.post(
    "/{workspace_id}/uploads",
    response_model=schemas.UploadSessionPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Declare an upload before sending any bytes",
)
def create_upload(
    payload: schemas.MediaUploadRequest,
    context: WorkspaceContext = Depends(require_permission(MEDIA_UPLOAD)),
    db: Session = Depends(get_db),
    storage: LocalStorage = Depends(get_storage),
) -> schemas.UploadSessionPublic:
    """Validates type, size, and filename, then opens a resumable session.

    A ``replace_media_id`` is resolved inside the caller's workspace here, so an
    id belonging to another tenant fails with ``404`` before any work is done.
    """
    if payload.replace_media_id is not None:
        try:
            media_service.get_media(db, context.workspace.id, payload.replace_media_id)
        except media_service.MediaError as exc:
            raise _translate(exc) from exc

    try:
        session = media_service.create_upload_session(
            storage,
            workspace_id=context.workspace.id,
            user_id=context.member.user_id,
            filename=payload.filename,
            mime_type=payload.mime_type,
            size_bytes=payload.size_bytes,
            replace_media_id=payload.replace_media_id,
        )
    except media_service.MediaError as exc:
        raise _translate(exc) from exc
    return _session_public(session)


@router.get(
    "/{workspace_id}/uploads/{upload_id}",
    response_model=schemas.UploadSessionPublic,
    summary="Inspect an upload so it can be resumed",
)
def get_upload(
    upload_id: uuid.UUID,
    context: WorkspaceContext = Depends(require_permission(MEDIA_UPLOAD)),
    storage: LocalStorage = Depends(get_storage),
) -> schemas.UploadSessionPublic:
    """Returns how many bytes are durably stored and where to continue."""
    try:
        session = media_service.load_upload_session(
            storage, upload_id, workspace_id=context.workspace.id
        )
    except media_service.MediaError as exc:
        raise _translate(exc) from exc
    return _session_public(session)


@router.put(
    "/{workspace_id}/uploads/{upload_id}",
    response_model=schemas.UploadSessionPublic,
    summary="Append one chunk at an explicit offset",
)
async def append_upload_chunk(
    request: Request,
    upload_id: uuid.UUID,
    offset: int = Query(ge=0),
    context: WorkspaceContext = Depends(require_permission(MEDIA_UPLOAD)),
    storage: LocalStorage = Depends(get_storage),
) -> schemas.UploadSessionPublic:
    """Writes the request body at ``offset``.

    A chunk that does not begin exactly where the previous one ended is refused
    with ``409`` and an ``X-Upload-Expected-Offset`` header, which is what makes
    the protocol resumable: a dropped connection costs a retry, not the upload.

    The body is read into memory, so it is bounded by roughly four advertised
    chunks — an oversized single request is rejected with ``413``.
    """
    chunk = await request.body()
    if not chunk:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The request body is empty; send at least one byte.",
        )
    if len(chunk) > _MAX_CHUNK_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"This chunk is {len(chunk)} bytes; the largest accepted request is "
                f"{_MAX_CHUNK_BYTES} bytes. Split the transfer into smaller chunks."
            ),
        )

    try:
        session = media_service.load_upload_session(
            storage, upload_id, workspace_id=context.workspace.id
        )
        session = media_service.append_chunk(storage, session, offset=offset, chunk=chunk)
    except media_service.MediaError as exc:
        raise _translate(exc) from exc
    return _session_public(session)


@router.post(
    "/{workspace_id}/uploads/{upload_id}/complete",
    response_model=schemas.MediaFilePublic,
    status_code=status.HTTP_201_CREATED,
    summary="Finish an upload and inspect the stored video",
)
def complete_upload(
    request: Request,
    upload_id: uuid.UUID,
    context: WorkspaceContext = Depends(require_permission(MEDIA_UPLOAD)),
    db: Session = Depends(get_db),
    storage: LocalStorage = Depends(get_storage),
) -> schemas.MediaFilePublic:
    """Verifies the byte count, hashes and probes the file, and stores it.

    ``422`` means the bytes are not a readable video container — a renamed
    archive that passed the declared-MIME check is rejected here, because
    FFprobe reads the real file. ``503`` means FFmpeg is not installed.
    """
    try:
        session = media_service.load_upload_session(
            storage, upload_id, workspace_id=context.workspace.id
        )
        media = media_service.finalize_upload(db, storage, session)
    except media_service.MediaError as exc:
        raise _translate(exc) from exc
    except video_service.FFmpegError as exc:
        raise _translate(exc) from exc

    db.commit()
    return _media_public(request, media)


@router.delete(
    "/{workspace_id}/uploads/{upload_id}",
    response_model=auth_schemas.MessageResponse,
    summary="Abort an upload in progress",
)
def abort_upload(
    upload_id: uuid.UUID,
    context: WorkspaceContext = Depends(require_permission(MEDIA_UPLOAD)),
    storage: LocalStorage = Depends(get_storage),
) -> auth_schemas.MessageResponse:
    """Discards the partial file and its session record."""
    try:
        session = media_service.load_upload_session(
            storage, upload_id, workspace_id=context.workspace.id
        )
        media_service.discard_upload_session(storage, session)
    except media_service.MediaError as exc:
        raise _translate(exc) from exc
    return auth_schemas.MessageResponse(message="Upload aborted.")


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------
@router.delete(
    "/{workspace_id}/{media_id}",
    response_model=auth_schemas.MessageResponse,
    summary="Delete a media file",
)
def delete_media(
    media_id: uuid.UUID,
    context: WorkspaceContext = Depends(require_permission(MEDIA_DELETE)),
    db: Session = Depends(get_db),
    storage: LocalStorage = Depends(get_storage),
) -> auth_schemas.MessageResponse:
    """Soft-deletes the row and frees its bytes.

    Refused with ``409`` while a live post still references the file, so a
    published post never loses its video.
    """
    try:
        media = media_service.get_media(db, context.workspace.id, media_id)
        media_service.delete_media(db, storage, media)
    except media_service.MediaError as exc:
        raise _translate(exc) from exc

    db.commit()
    return auth_schemas.MessageResponse(message="Media file deleted.")


# ---------------------------------------------------------------------------
# Signed file serving
# ---------------------------------------------------------------------------
@router.get(
    "/{workspace_id}/{media_id}/files/{variant}",
    summary="Serve a media object through a signed URL",
    response_class=FileResponse,
    include_in_schema=False,
)
def serve_media_file(
    workspace_id: uuid.UUID,
    media_id: uuid.UUID,
    variant: storage_service.MediaVariant,
    expires: int = Query(description="Unix timestamp the signature stops being valid"),
    signature: str = Query(description="HMAC-SHA256 signature minted by the API"),
    db: Session = Depends(get_db),
    storage: LocalStorage = Depends(get_storage),
) -> FileResponse:
    """Stream the master file or its thumbnail.

    Deliberately unauthenticated: the signature binds the media id, the variant,
    and the expiry, so possession of a valid URL *is* the authorization and the
    URL stops working when it expires. The tenant lookup still runs, which means
    a URL whose workspace segment was tampered with finds nothing.
    """
    if not storage_service.verify_media_signature(media_id, variant, expires, signature):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This media link is invalid or has expired.",
        )

    try:
        media = media_service.get_media(db, workspace_id, media_id)
    except media_service.MediaError as exc:
        raise _translate(exc) from exc

    if variant == "thumbnail":
        if not media.thumbnail_key:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="This media file has no thumbnail.",
            )
        key, media_type = media.thumbnail_key, "image/jpeg"
    else:
        key, media_type = media.storage_key, media.mime_type

    try:
        path = storage.path_for(key)
    except storage_service.StorageError as exc:
        raise _translate(exc) from exc
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The stored media object is no longer available.",
        )

    return FileResponse(
        path,
        media_type=media_type,
        # Signed links are short-lived and a replacement reuses the media id, so
        # a cached copy would be stale rather than merely old.
        headers={"Cache-Control": "private, no-store"},
    )


__all__ = ["router"]
