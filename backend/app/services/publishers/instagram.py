"""Instagram Reels publisher (Instagram Graph API via Facebook Login).

Instagram publishing is a three-step dance and, crucially, an **accepted
container is not a published Reel**:

1. ``POST /{ig_user_id}/media`` with ``media_type=REELS`` and the video's public
   URL creates a *container* and returns its id;
2. the container is polled with ``status_code`` until it reports ``FINISHED``
   (Instagram has finished downloading and transcoding the video);
3. ``POST /{ig_user_id}/media_publish`` turns the finished container into a
   published Reel and returns the media id.

Only after step 3 — and after reading the media's ``permalink`` — does this
publisher report success.

Publishing requires a public video URL and an Instagram **professional**
(Business/Creator) account linked to a Facebook Page.
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

#: Container statuses. ``EXPIRED`` means the container timed out before publish.
_FAILED = {"ERROR", "EXPIRED"}


class InstagramPublisher(MetaPublisher):
    """Publishes a Reel to an Instagram professional account."""

    platform = SocialPlatform.INSTAGRAM
    display_name = "Instagram"

    def capability(self) -> PublisherCapability:
        return PublisherCapability(
            platform=self.platform,
            display_name=self.display_name,
            max_size_bytes=1 * 1024**3,  # 1 GB
            max_duration_seconds=15 * 60,  # 15 minutes
            min_width=320,
            min_height=320,
            max_caption_length=2200,
            requires_title=False,
            supports_title=False,
            allowed_mime_types=("video/mp4", "video/quicktime"),
            notes="Reels look best at 9:16 (1080x1920). A professional account is required.",
        )

    def build_caption(
        self,
        *,
        base_caption: str | None,
        target_caption: str | None,
        title: str | None,
    ) -> str:
        """Instagram captions read as one block; hashtags are kept on a line."""
        text = super().build_caption(
            base_caption=base_caption, target_caption=target_caption, title=title
        )
        return text

    def publish(self, request: PublishRequest) -> PublishResult:
        account_id = request.platform_account_id
        container = self._json(
            self._request(
                "POST",
                self.graph_url(f"{account_id}/media"),
                data={
                    "media_type": "REELS",
                    "video_url": request.asset.public_url,
                    "caption": request.caption,
                    "access_token": request.access_token,
                },
            ),
            action="create the Reel container",
        )
        failure = self._meta_failure(container)
        if failure:
            raise PublishPermanentError(f"Instagram could not publish the Reel: {failure}")

        creation_id = container.get("id")
        if not creation_id:
            raise PublishPermanentError("Instagram did not return a container id.")

        self._await_container(str(creation_id), request.access_token)

        published = self._json(
            self._request(
                "POST",
                self.graph_url(f"{account_id}/media_publish"),
                data={"creation_id": str(creation_id), "access_token": request.access_token},
            ),
            action="publish the Reel",
        )
        failure = self._meta_failure(published)
        if failure:
            raise PublishPermanentError(f"Instagram could not publish the Reel: {failure}")

        media_id = published.get("id")
        if not media_id:
            raise PublishPermanentError("Instagram did not return a media id for the Reel.")

        permalink = self._read_permalink(str(media_id), request.access_token)
        return PublishResult(
            remote_post_id=str(media_id),
            remote_url=permalink or f"https://www.instagram.com/reel/{media_id}",
            status="FINISHED",
            raw=published,
        )

    def _await_container(self, creation_id: str, access_token: str) -> dict[str, Any]:
        """Poll the container until Instagram reports it is finished."""

        def fetch() -> dict[str, Any]:
            payload = self._json(
                self._request(
                    "GET",
                    self.graph_url(creation_id),
                    params={"fields": "status_code,status", "access_token": access_token},
                ),
                action="read the Reel container status",
            )
            failure = self._meta_failure(payload)
            if failure:
                raise PublishPermanentError(
                    f"Instagram could not publish the Reel: {failure}"
                )
            return payload

        return self._poll(
            fetch,
            describe=lambda payload: str(payload.get("status_code") or "IN_PROGRESS"),
            is_done=lambda state: state == "FINISHED",
            is_failed=lambda state: state in _FAILED,
            action="confirm the Reel is processed",
        )

    def _read_permalink(self, media_id: str, access_token: str) -> str | None:
        """The public permalink, when Instagram returns one.

        Reading it is best-effort: the Reel is already published by this point,
        so a failure here must not be reported as a failed publish.
        """
        try:
            payload = self._json(
                self._request(
                    "GET",
                    self.graph_url(media_id),
                    params={"fields": "permalink", "access_token": access_token},
                ),
                action="read the Reel permalink",
            )
        except Exception:  # noqa: BLE001 - publishing already succeeded
            return None
        return payload.get("permalink") or None


__all__ = ["InstagramPublisher"]
