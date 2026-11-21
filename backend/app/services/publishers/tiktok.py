"""TikTok video publisher (Content Posting API v2, direct post).

TikTok's direct-post flow is three calls, and — as with every other platform
here — an accepted upload is not a published video:

1. ``POST /v2/post/publish/video/init/`` with the caption and the file's size
   returns a ``publish_id`` and a short-lived ``upload_url``;
2. the file is ``PUT`` to that URL, in chunks whose size TikTok dictates;
3. ``POST /v2/post/publish/status/fetch/`` is polled with the ``publish_id``
   until it reports ``PUBLISH_COMPLETE`` (or a terminal ``FAILED``).

TikTok is **push-based** — the worker streams the bytes itself — so the video
must exist on local storage. Its envelope quirk (shared with the Login Kit) is
that *every* response carries an ``error`` object, and ``code == "ok"`` means
success; :meth:`TikTokPublisher._check_envelope` encodes that.

Two documented restrictions matter to users:

* an **unaudited** app can only publish ``SELF_ONLY`` (the video lands in the
  creator's private drafts), which is why ``privacy`` is mapped rather than
  passed through verbatim;
* ``video.publish`` must be granted during the Phase 6 connection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.db.enums import SocialPlatform
from app.services.publishers.base import (
    MediaAsset,
    MediaValidationError,
    PublishAuthError,
    PublishPermanentError,
    PublishRequest,
    PublishResult,
    PublishTransientError,
    Publisher,
    PublisherCapability,
)

INIT_ENDPOINT = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_ENDPOINT = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

#: Terminal statuses. Anything else means TikTok is still working.
_DONE = "PUBLISH_COMPLETE"
_FAILED = "FAILED"

#: TikTok requires the upload to be split into 5–64 MB chunks.
_MIN_CHUNK_BYTES = 5 * 1024**2
_MAX_CHUNK_BYTES = 64 * 1024**2

#: ``privacy`` values the composer may send, mapped to TikTok's enum.
_PRIVACY_LEVELS = {
    "public": "PUBLIC_TO_EVERYONE",
    "followers": "FOLLOWER_OF_CREATOR",
    "friends": "MUTUAL_FOLLOW_FRIENDS",
    "private": "SELF_ONLY",
}

#: The one status an unaudited app is allowed to post with.
_SAFE_PRIVACY = "SELF_ONLY"

#: TikTok error codes that mean "this could work on a later attempt".
_RETRYABLE_CODES = {
    "rate_limit_exceeded",
    "internal_error",
    "server_error",
    "service_unavailable",
}

#: TikTok error codes that mean the user has to reconnect the account.
_AUTH_CODES = {
    "access_token_invalid",
    "scope_not_authorized",
    "unauthorized",
    "permission_denied",
}


class TikTokPublisher(Publisher):
    """Publishes a video to a TikTok account via direct post."""

    platform = SocialPlatform.TIKTOK
    display_name = "TikTok"

    def capability(self) -> PublisherCapability:
        return PublisherCapability(
            platform=self.platform,
            display_name=self.display_name,
            max_size_bytes=4 * 1024**3,  # 4 GB
            max_duration_seconds=60 * 60,  # 60 minutes
            min_width=360,
            min_height=360,
            max_caption_length=2200,
            requires_title=False,
            # TikTok's caption *is* the post title, so there is no separate field.
            supports_title=False,
            allowed_mime_types=("video/mp4", "video/quicktime", "video/webm"),
            notes=(
                "Vertical 9:16 (1080x1920) is recommended. An unaudited app can "
                "only post privately to the creator's drafts."
            ),
        )

    def publish(self, request: PublishRequest) -> PublishResult:
        path = request.asset.path
        if path is None:
            raise MediaValidationError(
                "The TikTok publisher uploads the file itself, so the video must be "
                "available on the server's storage."
            )

        chunk_size, total_chunks = self._chunk_plan(request.asset.size_bytes)
        init = self._json(
            self._request(
                "POST",
                INIT_ENDPOINT,
                headers=self._auth_headers(request.access_token),
                data={
                    "post_info": {
                        "title": request.caption,
                        "privacy_level": self._privacy_level(request.privacy),
                    },
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "video_size": request.asset.size_bytes,
                        "chunk_size": chunk_size,
                        "total_chunk_count": total_chunks,
                    },
                },
            ),
            action="start the upload",
        )
        self._check_envelope(init, action="start the upload")

        data = init.get("data") or {}
        publish_id = data.get("publish_id")
        upload_url = data.get("upload_url")
        if not publish_id or not upload_url:
            raise PublishPermanentError(
                "TikTok did not return an upload URL for the video."
            )

        self._upload_bytes(str(upload_url), path, request.asset)
        final = self._await_published(str(publish_id), request.access_token)
        return PublishResult(
            remote_post_id=str(publish_id),
            # TikTok exposes no permalink before the post is fully processed.
            remote_url=None,
            status=str(final.get("status") or _DONE),
            raw=final,
        )

    # --- upload ---------------------------------------------------------
    def _upload_bytes(self, upload_url: str, path: Path, asset: MediaAsset) -> None:
        """Stream the file to TikTok in the chunk sizes the init call declared."""
        size_bytes = asset.size_bytes
        chunk_size, total_chunks = self._chunk_plan(size_bytes)
        with path.open("rb") as handle:
            offset = 0
            for _ in range(total_chunks):
                block = handle.read(chunk_size)
                if not block:
                    break
                end = offset + len(block) - 1
                response = self._request(
                    "PUT",
                    upload_url,
                    headers={
                        "Content-Type": asset.mime_type,
                        "Content-Length": str(len(block)),
                        "Content-Range": f"bytes {offset}-{end}/{size_bytes}",
                    },
                    content=block,
                )
                if response.status_code >= 400:
                    self._raise_for_status(response, action="upload the video")
                offset = end + 1

    def _await_published(self, publish_id: str, access_token: str) -> dict[str, Any]:
        """Poll TikTok until it confirms the video is published."""

        def fetch() -> dict[str, Any]:
            payload = self._json(
                self._request(
                    "POST",
                    STATUS_ENDPOINT,
                    headers=self._auth_headers(access_token),
                    data={"publish_id": publish_id},
                ),
                action="read the publish status",
            )
            self._check_envelope(payload, action="read the publish status")
            return payload.get("data") or {}

        return self._poll(
            fetch,
            describe=lambda data: str(data.get("status") or "PROCESSING_UPLOAD"),
            is_done=lambda state: state == _DONE,
            is_failed=lambda state: state == _FAILED,
            action="confirm the video is published",
        )

    # --- TikTok specifics ------------------------------------------------
    def _auth_headers(self, access_token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    def _check_envelope(self, payload: dict[str, Any], *, action: str) -> None:
        """Classify TikTok's always-present ``error`` envelope.

        Unlike other platforms a ``200`` body still contains an ``error`` object;
        only ``code == "ok"`` (or ``"success"``) means the call worked.
        """
        error = payload.get("error")
        if not isinstance(error, dict):
            return
        code = str(error.get("code") or "").strip().lower()
        if code in ("", "ok", "success"):
            return
        message = str(error.get("message") or code)
        if code in _AUTH_CODES:
            raise PublishAuthError(
                f"TikTok rejected the credentials while trying to {action}. "
                f"{message} Reconnect the account."
            )
        if code in _RETRYABLE_CODES:
            raise PublishTransientError(f"TikTok could not {action}: {message}")
        raise PublishPermanentError(f"TikTok could not {action}: {message}")

    @staticmethod
    def _privacy_level(privacy: str) -> str:
        """Map a composer visibility onto TikTok's enum, defaulting to private.

        ``SELF_ONLY`` is the safe fallback: an unaudited app cannot publish
        anything more public, and a wrong ``PUBLIC_TO_EVERYONE`` would be
        rejected outright.
        """
        return _PRIVACY_LEVELS.get((privacy or "").lower(), _SAFE_PRIVACY)

    @staticmethod
    def _chunk_plan(size_bytes: int) -> tuple[int, int]:
        """Chunk size and count that satisfy TikTok's 5–64 MB rule."""
        if size_bytes <= _MIN_CHUNK_BYTES:
            return max(size_bytes, 1), 1
        chunk_size = min(_MAX_CHUNK_BYTES, size_bytes)
        total = (size_bytes + chunk_size - 1) // chunk_size
        return chunk_size, total


__all__ = ["TikTokPublisher"]
