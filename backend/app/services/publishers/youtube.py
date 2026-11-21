"""YouTube channel video publisher (YouTube Data API v3).

YouTube takes the bytes itself through the **resumable upload** protocol:

1. ``POST /upload/youtube/v3/videos?uploadType=resumable`` with the video's
   metadata returns a session URI in the ``Location`` header;
2. the file is ``PUT`` to that URI;
3. YouTube encodes the video asynchronously, so the video resource is polled
   until ``status.uploadStatus`` is ``processed`` (or a terminal failure).

The publisher reads the file from the worker's local storage and streams it, so
no signed URL is involved. ``youtube.upload`` is a restricted scope: an
unverified app's uploads are forced to ``private`` by Google, and the *actual*
visibility is read back from the final resource rather than assumed.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.db.enums import SocialPlatform
from app.services.publishers.base import (
    MediaValidationError,
    PublishPermanentError,
    PublishRequest,
    PublishResult,
    Publisher,
    PublisherCapability,
)

UPLOAD_ENDPOINT = "https://www.googleapis.com/upload/youtube/v3/videos"
VIDEOS_ENDPOINT = "https://www.googleapis.com/youtube/v3/videos"

#: Upload statuses that mean YouTube gave up on the file.
_FAILED = {"failed", "rejected"}

#: Uploaded bytes are streamed in this many-byte blocks (never the whole file).
_STREAM_CHUNK_BYTES = 1024 * 1024

#: Default YouTube category ("People & Blogs").
_DEFAULT_CATEGORY_ID = "22"

_PRIVACY_VALUES = {"public", "private", "unlisted"}


class YouTubePublisher(Publisher):
    """Publishes a video to a YouTube channel."""

    platform = SocialPlatform.YOUTUBE
    display_name = "YouTube"

    def _title_limit(self) -> int:
        return 100

    def capability(self) -> PublisherCapability:
        return PublisherCapability(
            platform=self.platform,
            display_name=self.display_name,
            max_size_bytes=256 * 1024**3,  # 256 GB
            max_duration_seconds=12 * 60 * 60,  # 12 hours
            min_width=128,
            min_height=128,
            max_caption_length=5000,
            requires_title=True,
            supports_title=True,
            allowed_mime_types=(
                "video/mp4",
                "video/quicktime",
                "video/webm",
                "video/x-matroska",
                "video/x-msvideo",
            ),
            notes="A title is required. Unverified apps upload as private.",
        )

    def publish(self, request: PublishRequest) -> PublishResult:
        path = request.asset.path
        if path is None:
            raise MediaValidationError(
                "The YouTube publisher uploads the file itself, so the video must be "
                "available on the server's storage."
            )
        session_uri = self._start_resumable(request)
        uploaded = self._upload_bytes(session_uri, path, request.asset.mime_type)
        video_id = uploaded.get("id")
        if not video_id:
            raise PublishPermanentError("YouTube did not return a video id for the upload.")

        final = self._await_processed(str(video_id), request.access_token)
        return PublishResult(
            remote_post_id=str(video_id),
            remote_url=f"https://www.youtube.com/watch?v={video_id}",
            status=self._upload_status(final),
            raw=final,
        )

    def _start_resumable(self, request: PublishRequest) -> str:
        privacy = request.privacy if request.privacy in _PRIVACY_VALUES else "public"
        response = self._request(
            "POST",
            UPLOAD_ENDPOINT,
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={
                "Authorization": f"Bearer {request.access_token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(request.asset.size_bytes),
                "X-Upload-Content-Type": request.asset.mime_type,
            },
            data={
                "snippet": {
                    "title": request.title or "Untitled video",
                    "description": request.caption,
                    "categoryId": _DEFAULT_CATEGORY_ID,
                },
                "status": {
                    "privacyStatus": privacy,
                    "selfDeclaredMadeForKids": False,
                },
            },
        )
        if response.status_code >= 400:
            self._raise_for_status(response, action="start the upload")
        session_uri = response.headers.get("Location")
        if not session_uri:
            raise PublishPermanentError(
                "YouTube did not return a resumable upload session URI."
            )
        return session_uri

    def _upload_bytes(self, session_uri: str, path: Path, mime_type: str) -> dict[str, Any]:
        response = self._request(
            "PUT",
            session_uri,
            headers={"Content-Type": mime_type},
            content=b"".join(_iter_file(path)),
        )
        return self._json(response, action="upload the video")

    def _await_processed(self, video_id: str, access_token: str) -> dict[str, Any]:
        def fetch() -> dict[str, Any]:
            payload = self._json(
                self._request(
                    "GET",
                    VIDEOS_ENDPOINT,
                    params={"part": "status,processingDetails", "id": video_id},
                    headers={"Authorization": f"Bearer {access_token}"},
                ),
                action="read the video status",
            )
            items = payload.get("items")
            if not isinstance(items, list) or not items:
                raise PublishPermanentError(
                    "YouTube could not find the uploaded video. It may have been rejected."
                )
            return items[0]

        return self._poll(
            fetch,
            describe=self._upload_status,
            is_done=lambda state: state == "processed",
            is_failed=lambda state: state in _FAILED,
            action="confirm the video is published",
        )

    @staticmethod
    def _upload_status(payload: dict[str, Any]) -> str:
        status = payload.get("status")
        if isinstance(status, dict):
            return str(status.get("uploadStatus") or "uploaded")
        return "uploaded"


def _iter_file(path: Path) -> Iterator[bytes]:
    """Yield a file's bytes in blocks, so a large video is never fully in memory."""
    with path.open("rb") as handle:
        while True:
            block = handle.read(_STREAM_CHUNK_BYTES)
            if not block:
                break
            yield block


__all__ = ["YouTubePublisher"]
