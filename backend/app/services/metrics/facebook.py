"""Facebook Page video + audience metrics (Facebook Graph API).

Two documented Graph API reads back this provider:

* ``GET /{video-id}?fields=views,likes.summary(true),comments.summary(true),shares``
  for one published video.
* ``GET /{page-id}?fields=followers_count,fan_count`` for the Page's audience.

Facebook does not report ``reach`` or ``impressions`` on a video node through
these fields, so they are returned as ``None`` — unavailable, never zero.
"""

from __future__ import annotations

from app.core.security import utcnow
from app.db.enums import SocialPlatform
from app.services.metrics.base import AccountMetrics, PostMetrics, as_int
from app.services.metrics.meta import MetaMetricsProvider

#: Fields read for a single video's performance.
_POST_FIELDS = "views,likes.summary(true),comments.summary(true),shares"
#: Fields read for a Page's audience.
_ACCOUNT_FIELDS = "followers_count,fan_count"


class FacebookMetricsProvider(MetaMetricsProvider):
    """Reads Facebook video and Page metrics from the Graph API."""

    platform = SocialPlatform.FACEBOOK
    display_name = "Facebook"

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
                self.graph_url(remote_post_id),
                params={"fields": _POST_FIELDS, "access_token": access_token},
            ),
            action="read the video's metrics",
        )
        self._ensure_ok(payload, action="read the video's metrics")

        return PostMetrics(
            platform=self.platform,
            retrieved_at=utcnow(),
            views=as_int(payload.get("views")),
            likes=self._total(payload.get("likes")),
            comments=self._total(payload.get("comments")),
            shares=self._total(payload.get("shares")),
            # Not exposed on the video node by these fields.
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
                self.graph_url(platform_account_id),
                params={"fields": _ACCOUNT_FIELDS, "access_token": access_token},
            ),
            action="read the Page's audience metrics",
        )
        self._ensure_ok(payload, action="read the Page's audience metrics")

        followers = as_int(payload.get("followers_count"))
        if followers is None:
            # Older Pages report their audience as ``fan_count``.
            followers = as_int(payload.get("fan_count"))

        return AccountMetrics(
            platform=self.platform,
            retrieved_at=utcnow(),
            followers=followers,
            follows=None,
            posts_count=None,
            raw=payload,
        )


__all__ = ["FacebookMetricsProvider"]
