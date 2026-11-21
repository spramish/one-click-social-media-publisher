"""YouTube video + channel metrics (YouTube Data API v3).

Two documented Data API reads back this provider:

* ``GET /youtube/v3/videos?part=statistics&id={video-id}`` for one published
  video.
* ``GET /youtube/v3/channels?part=statistics&id={channel-id}`` for the channel's
  audience.

YouTube omits a statistic from the ``statistics`` object rather than reporting a
zero when the creator hid the count (``likeCount`` with likes disabled,
``commentCount`` with comments off, ``subscriberCount`` with a hidden
subscriber count), so a missing key becomes ``None`` — unavailable, never zero.
YouTube exposes no share or reach count through the Data API, so those are
``None``.
"""

from __future__ import annotations

from typing import Any

from app.core.security import utcnow
from app.db.enums import SocialPlatform
from app.services.metrics.base import (
    AccountMetrics,
    MetricsError,
    MetricsProvider,
    PostMetrics,
    as_int,
)

VIDEOS_ENDPOINT = "https://www.googleapis.com/youtube/v3/videos"
CHANNELS_ENDPOINT = "https://www.googleapis.com/youtube/v3/channels"


class YouTubeMetricsProvider(MetricsProvider):
    """Reads YouTube video and channel metrics from the Data API v3."""

    platform = SocialPlatform.YOUTUBE
    display_name = "YouTube"

    def fetch_post_metrics(
        self,
        *,
        access_token: str,
        remote_post_id: str,
        platform_account_id: str,
    ) -> PostMetrics:
        payload = self._json(
            self._request(
                "GET",
                VIDEOS_ENDPOINT,
                params={"part": "statistics", "id": remote_post_id},
                headers={"Authorization": f"Bearer {access_token}"},
            ),
            action="read the video's metrics",
        )
        statistics = self._first_statistics(payload, subject="video")

        return PostMetrics(
            platform=self.platform,
            retrieved_at=utcnow(),
            views=as_int(statistics.get("viewCount")),
            likes=as_int(statistics.get("likeCount")),
            comments=as_int(statistics.get("commentCount")),
            # Not exposed by the Data API for a video.
            shares=None,
            reach=None,
            impressions=None,
            raw=payload,
        )

    def fetch_account_metrics(
        self,
        *,
        access_token: str,
        platform_account_id: str,
    ) -> AccountMetrics:
        payload = self._json(
            self._request(
                "GET",
                CHANNELS_ENDPOINT,
                params={"part": "statistics", "id": platform_account_id},
                headers={"Authorization": f"Bearer {access_token}"},
            ),
            action="read the channel's audience metrics",
        )
        statistics = self._first_statistics(payload, subject="channel")

        return AccountMetrics(
            platform=self.platform,
            retrieved_at=utcnow(),
            followers=as_int(statistics.get("subscriberCount")),
            follows=None,
            posts_count=as_int(statistics.get("videoCount")),
            raw=payload,
        )

    @staticmethod
    def _first_statistics(payload: dict[str, Any], *, subject: str) -> dict[str, Any]:
        """The ``statistics`` object of the first resource, or raise.

        An empty ``items`` list means the id is unknown to YouTube (deleted, or
        never existed); that is a permanent failure, not an unavailable metric.
        """
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            raise MetricsError(
                f"YouTube returned no {subject} for that id. It may have been deleted."
            )
        first = items[0]
        if not isinstance(first, dict):  # pragma: no cover - defensive
            raise MetricsError(f"YouTube returned an unexpected {subject} payload.")
        statistics = first.get("statistics")
        return statistics if isinstance(statistics, dict) else {}


__all__ = ["YouTubeMetricsProvider"]
