"""Media storage backend plus signed, expiring URLs.

Phase 5 keeps uploads on the local filesystem (``StorageBackend.LOCAL``). The
interface is deliberately narrow — the whole surface a caller needs — so an
S3/MinIO implementation can be dropped in later without touching the media
service or the routes.

Two rules are enforced here rather than left to callers:

* **Object keys are server-side only.** A key is built by
  :func:`original_key`/:func:`thumbnail_key`, never accepted from a client, and
  :meth:`LocalStorage.path_for` refuses anything that would escape the media
  root.
* **Clients get signed URLs, never paths.** :func:`sign_media_url` produces an
  HMAC-SHA256 signature over ``media_id`` + variant + expiry, and
  :func:`verify_media_signature` checks it in constant time. The file-serving
  route therefore needs no session, and a URL stops working when it expires.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import shutil
import time
import uuid
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Which object of a media file a signed URL points at.
MediaVariant = Literal["original", "thumbnail"]

#: Domain separator, so a signature minted for one purpose can never validate
#: for another even if the payloads ever collide.
_SIGNATURE_CONTEXT = "ocp-media-url-v1"


class StorageError(Exception):
    """Base class for storage failures."""


class UnsafeKeyError(StorageError):
    """A storage key was empty, absolute, or tried to escape the media root."""


class ObjectNotFoundError(StorageError):
    """The requested object does not exist in storage."""


# ---------------------------------------------------------------------------
# Key construction
# ---------------------------------------------------------------------------
def original_key(workspace_id: uuid.UUID, media_id: uuid.UUID, extension: str) -> str:
    """Key of the master video file.

    The user-supplied filename is deliberately **not** part of the key: the
    original name is kept in the database for display, while the object path
    stays a server-controlled value that cannot be used for traversal.
    """
    return f"{workspace_id}/{media_id}/source{extension}"


def thumbnail_key(workspace_id: uuid.UUID, media_id: uuid.UUID) -> str:
    """Key of the generated JPEG thumbnail."""
    return f"{workspace_id}/{media_id}/thumbnail.jpg"


def _normalise_key(key: str) -> PurePosixPath:
    """Validate a key and return it as a POSIX path, or raise."""
    if not key or not key.strip():
        raise UnsafeKeyError("storage key must not be empty")
    if "\\" in key:
        raise UnsafeKeyError("storage key must use forward slashes")
    path = PurePosixPath(key)
    if path.is_absolute() or ".." in path.parts:
        raise UnsafeKeyError(f"unsafe storage key: {key!r}")
    return path


# ---------------------------------------------------------------------------
# Local filesystem backend
# ---------------------------------------------------------------------------
class LocalStorage:
    """Stores objects as files under a single root directory."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    # -- paths ---------------------------------------------------------------
    def path_for(self, key: str) -> Path:
        """Absolute path of ``key``, refusing anything outside the root."""
        relative = _normalise_key(key)
        root = self._root.resolve()
        candidate = (root / relative).resolve()
        if candidate != root and root not in candidate.parents:
            raise UnsafeKeyError(f"storage key escapes the media root: {key!r}")
        return candidate

    def ensure_root(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)

    # -- reads ---------------------------------------------------------------
    def exists(self, key: str) -> bool:
        return self.path_for(key).is_file()

    def size(self, key: str) -> int:
        path = self.path_for(key)
        if not path.is_file():
            raise ObjectNotFoundError(f"object not found: {key!r}")
        return path.stat().st_size

    def open(self, key: str) -> BinaryIO:
        """Open an object for reading (used by the file-serving route)."""
        path = self.path_for(key)
        if not path.is_file():
            raise ObjectNotFoundError(f"object not found: {key!r}")
        return path.open("rb")

    # -- writes --------------------------------------------------------------
    def write_bytes(self, key: str, data: bytes) -> int:
        """Replace an object's contents with ``data``. Returns the byte count."""
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return len(data)

    def read_bytes(self, key: str) -> bytes:
        """Read a whole object. Only for small, server-owned files."""
        path = self.path_for(key)
        if not path.is_file():
            raise ObjectNotFoundError(f"object not found: {key!r}")
        return path.read_bytes()

    def append_chunk(self, key: str, data: bytes) -> int:
        """Append ``data`` to an object, creating it (and its parents) if needed.

        Returns the object's size after the write, so the resumable-upload
        protocol always reports the authoritative offset.
        """
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            return handle.tell()

    def adopt(self, source: Path, key: str) -> int:
        """Move a finished temporary file into its final location.

        Both paths live under the media root, so this is an atomic rename on
        the same volume — a reader never observes a half-written object.
        """
        destination = self.path_for(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
        return destination.stat().st_size

    def delete(self, key: str) -> bool:
        """Delete one object. Returns ``False`` when it was already gone."""
        path = self.path_for(key)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def delete_media_directory(self, workspace_id: uuid.UUID, media_id: uuid.UUID) -> None:
        """Remove every object belonging to one media file."""
        directory = self.path_for(f"{workspace_id}/{media_id}")
        shutil.rmtree(directory, ignore_errors=True)

    def delete_tree(self, prefix: str) -> None:
        """Remove a whole subtree (used to clean up abandoned uploads)."""
        shutil.rmtree(self.path_for(prefix), ignore_errors=True)

    def iter_keys(self, prefix: str) -> Iterator[tuple[str, int]]:
        """Yield ``(key, size)`` for every file under ``prefix``."""
        base = self.path_for(prefix)
        root = self._root.resolve()
        if not base.is_dir():
            return
        for path in sorted(base.rglob("*")):
            if path.is_file():
                yield path.relative_to(root).as_posix(), path.stat().st_size


@lru_cache
def get_storage() -> LocalStorage:
    """The process-wide storage backend.

    Exposed as a FastAPI dependency so tests can swap in a temporary root with
    ``app.dependency_overrides`` and never touch the developer's real media.
    """
    return LocalStorage(settings.media_root_path)


# ---------------------------------------------------------------------------
# Signed URLs
# ---------------------------------------------------------------------------
def _signing_payload(media_id: uuid.UUID, variant: MediaVariant, expires_at: int) -> bytes:
    return f"{_SIGNATURE_CONTEXT}\n{media_id}\n{variant}\n{expires_at}".encode()


def _digest(payload: bytes) -> str:
    return hmac.new(settings.secret_key.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def sign_media_url(
    media_id: uuid.UUID,
    variant: MediaVariant = "original",
    *,
    ttl_seconds: int | None = None,
) -> tuple[int, str]:
    """Mint a signature for one media object.

    Returns:
        ``(expires_at, signature)`` where ``expires_at`` is a Unix timestamp.
    """
    ttl = settings.media_signed_url_ttl_seconds if ttl_seconds is None else ttl_seconds
    expires_at = int(time.time()) + max(1, ttl)
    return expires_at, _digest(_signing_payload(media_id, variant, expires_at))


def verify_media_signature(
    media_id: uuid.UUID,
    variant: MediaVariant,
    expires_at: int,
    signature: str,
) -> bool:
    """Constant-time check of a URL signature and its expiry."""
    if expires_at < int(time.time()):
        return False
    expected = _digest(_signing_payload(media_id, variant, expires_at))
    return hmac.compare_digest(expected, signature or "")


def public_media_url(
    media_id: uuid.UUID,
    workspace_id: uuid.UUID,
    variant: MediaVariant = "original",
    *,
    ttl_seconds: int | None = None,
) -> str:
    """Absolute, signed URL a platform can fetch a media object from.

    Built from ``BACKEND_PUBLIC_URL`` (not a request object) so a Celery worker
    — which has no HTTP request — can mint the same URL a browser would receive.
    Pull-based platforms (Instagram, Facebook) download the video from this URL,
    so its TTL is deliberately longer than the browser one.
    """
    expires_at, signature = sign_media_url(media_id, variant, ttl_seconds=ttl_seconds)
    base = settings.backend_public_url.rstrip("/")
    return (
        f"{base}{settings.api_v1_prefix}/media/{workspace_id}/{media_id}/files/{variant}"
        f"?expires={expires_at}&signature={signature}"
    )


__all__ = [
    "LocalStorage",
    "MediaVariant",
    "ObjectNotFoundError",
    "StorageError",
    "UnsafeKeyError",
    "get_storage",
    "original_key",
    "public_media_url",
    "sign_media_url",
    "thumbnail_key",
    "verify_media_signature",
]
