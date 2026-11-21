"""Instagram professional-account media + audience metrics (Instagram Graph API).

Two documented Graph API reads back this provider:

* ``GET /{ig-media-id}?fields=like_count,comments_count`` for one published reel
  or video.
* ``GET /{ig-user-id}?fields=followers_count,follows_count,media_count`` for the
  account's audience.

Instagram exposes views, reach and shares through *media insights* rather than
on the media node, and those insights are only available to a professional
account that owns the media. Where they are not returned, the metric is
``None`` — unavailable, never zero.
"""

from __future__ import annotations

from app.core.security import utcnow
from app.db.enums import SocialPlatform
from app.services.metrics.base import AccountMetrics, PostMetrics, as_int
from app.services.metrics.meta import MetaMetricsProvider

#: Fields read for a single media item.
_POST_FIELDS = "like_count,comments_count"
#: Fields read for the account's audience.
_ACCOUNT_FIELDS = "followers_count,follows_count,media_count"


class InstagramMetricsProvider(MetaMetricsProvider):
    """Reads Instagram media and account metrics from the Graph API."""

    platform = SocialPlatform.INSTAGRAM
    display_name = "Instagram"

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
            action="read the media's metrics",
        )
        self._ensure_ok(payload, action="read the media's metrics")

        return PostMetrics(
            platform=self.platform,
            retrieved_at=utcnow(),
            likes=as_int(payload.get("like_count")),
            comments=as_int(payload.get("comments_count")),
            # Provided via media insights (professional accounts only), which
            # this read does not request; unavailable rather than zero.
            views=None,
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
                self.graph_url(platform_account_id),
                params={"fields": _ACCOUNT_FIELDS, "access_token": access_token},
            ),
            action="read the account's audience metrics",
        )
        self._ensure_ok(payload, action="read the account's audience metrics")

        return AccountMetrics(
            platform=self.platform,
            retrieved_at=utcnow(),
            followers=as_int(payload.get("followers_count")),
            follows=as_int(payload.get("follows_count")),
            posts_count=as_int(payload.get("media_count")),
            raw=payload,
        )


__all__ = ["InstagramMetricsProvider"]
