"""Facebook Page video publisher (Meta Graph API).

A Facebook **Page** is the publishing destination. The upload is a single
``POST /{page_id}/videos`` with the video's public URL; Facebook then downloads
and encodes it asynchronously, reporting progress through the video object's
``status`` field. Publication is only reported once that status reaches
``ready`` — an accepted upload is not a published video.

Required app configuration (see ``docs/OAUTH_SETUP.md``): ``pages_manage_posts``
(the scope that authorizes posting to a Page) plus a Page access token, both
granted during the Phase 6 connection.
"""

from __future__ import annotations

from typing import Any

from app.db.enums import SocialPlatform
from app.services.publishers.base import (
    PublishPermanentError,
    PublishRequest,
    PublishResult,
    PublisherCapability,
)
from app.services.publishers.meta import MetaPublisher

#: Statuses the video object can report while it is being encoded.
_FAILED = {"error", "failed"}


class FacebookPublisher(MetaPublisher):
    """Publishes a video to a Facebook Page."""

    platform = SocialPlatform.FACEBOOK
    display_name = "Facebook"

    def capability(self) -> PublisherCapability:
        return PublisherCapability(
            platform=self.platform,
            display_name=self.display_name,
            max_size_bytes=10 * 1024**3,  # 10 GB
            max_duration_seconds=4 * 60 * 60,  # 4 hours
            min_width=128,
            min_height=128,
            max_caption_length=63206,
            requires_title=False,
            supports_title=True,
            allowed_mime_types=("video/mp4", "video/quicktime"),
            notes="Best with MP4/H.264. Facebook pulls the video from a signed URL.",
        )

    def publish(self, request: PublishRequest) -> PublishResult:
        asset = request.asset
        created = self._json(
            self._request(
                "POST",
                self.graph_url(f"{request.platform_account_id}/videos"),
                data={
                    "file_url": asset.public_url,
                    "description": request.caption,
                    "title": request.title or "",
                    "access_token": request.access_token,
                },
            ),
            action="start the video upload",
        )
        # Meta reports a failure inside the 200 envelope rather than the status.
        failure = self._meta_failure(created)
        if failure:
            raise PublishPermanentError(f"Facebook could not publish the video: {failure}")

        video_id = created.get("id")
        if not video_id:
            raise PublishPermanentError("Facebook did not return a video id for the upload.")

        final = self._await_ready(str(video_id), request.access_token)
        return PublishResult(
            remote_post_id=str(video_id),
            remote_url=final.get("permalink_url") or f"https://facebook.com/{video_id}",
            status=self._video_status(final),
            raw=final,
        )

    def _await_ready(self, video_id: str, access_token: str) -> dict[str, Any]:
        """Poll the video object until Facebook finishes encoding it."""

        def fetch() -> dict[str, Any]:
            payload = self._json(
                self._request(
                    "GET",
                    self.graph_url(video_id),
                    params={
                        "fields": "status,permalink_url",
                        "access_token": access_token,
                    },
                ),
                action="read the video status",
            )
            failure = self._meta_failure(payload)
            if failure:
                raise PublishPermanentError(
                    f"Facebook could not publish the video: {failure}"
                )
            return payload

        return self._poll(
            fetch,
            describe=self._video_status,
            is_done=lambda state: state == "ready",
            is_failed=lambda state: state in _FAILED,
            action="confirm the video is published",
        )

    @staticmethod
    def _video_status(payload: dict[str, Any]) -> str:
        status = payload.get("status")
        if isinstance(status, dict):
            return str(status.get("video_status") or "processing")
        return "processing"


__all__ = ["FacebookPublisher"]
