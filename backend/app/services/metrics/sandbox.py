"""Sandbox metrics providers — simulated analytics for ``DEMO_MODE``.

The analytics dashboard reads numbers straight from each platform. In
``DEMO_MODE`` there is no platform to read, so this module returns a
**deterministic** simulation: the values are derived from a hash of the post id
and account id, so refreshing repeatedly shows a stable history rather than
numbers that change on every request. Every payload is tagged
``{"sandbox": True}``.

The metrics are internally consistent (impressions ≥ reach ≥ views, and likes /
comments / shares are fractions of views), and every field is populated — a
simulated platform that reports a metric is different from one that does not.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from app.services.metrics.base import AccountMetrics, PostMetrics
from app.services.metrics.facebook import FacebookMetricsProvider
from app.services.metrics.instagram import InstagramMetricsProvider
from app.services.metrics.tiktok import TikTokMetricsProvider
from app.services.metrics.youtube import YouTubeMetricsProvider


def _seed(*parts: str) -> int:
    """A stable integer derived from ``parts``, so a metric never jitters."""
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


class _SandboxMetricsProvider:
    """Mixin that answers both metrics reads without a network call.

    Mixed in *before* the real provider so only the two read methods are
    overridden; the real class still supplies ``platform`` and ``display_name``.
    """

    def fetch_post_metrics(
        self,
        *,
        access_token: str,
        remote_post_id: str,
        platform_account_id: str,
    ) -> PostMetrics:
        base = _seed(self.platform.value, remote_post_id, platform_account_id)
        # A funnel that always narrows: more impressions than reach, more reach
        # than views — what a real dashboard would show.
        impressions = 2_000 + base % 48_000
        reach = max(1, impressions * 70 // 100)
        views = max(1, reach * 60 // 100)
        return PostMetrics(
            platform=self.platform,
            retrieved_at=datetime.now(timezone.utc),
            views=views,
            likes=max(0, views * 8 // 100),
            comments=max(0, views * 2 // 100),
            shares=max(0, views * 1 // 100),
            reach=reach,
            impressions=impressions,
            raw={"sandbox": True},
        )

    def fetch_account_metrics(
        self,
        *,
        access_token: str,
        platform_account_id: str,
    ) -> AccountMetrics:
        base = _seed(self.platform.value, platform_account_id)
        return AccountMetrics(
            platform=self.platform,
            retrieved_at=datetime.now(timezone.utc),
            followers=500 + base % 25_000,
            follows=50 + (base // 7) % 900,
            posts_count=5 + (base // 13) % 400,
            raw={"sandbox": True},
        )


class SandboxFacebookMetricsProvider(_SandboxMetricsProvider, FacebookMetricsProvider):
    """Facebook metrics, simulated in-process."""


class SandboxInstagramMetricsProvider(_SandboxMetricsProvider, InstagramMetricsProvider):
    """Instagram metrics, simulated in-process."""


class SandboxYouTubeMetricsProvider(_SandboxMetricsProvider, YouTubeMetricsProvider):
    """YouTube metrics, simulated in-process."""


class SandboxTikTokMetricsProvider(_SandboxMetricsProvider, TikTokMetricsProvider):
    """TikTok metrics, simulated in-process."""


__all__ = [
    "SandboxFacebookMetricsProvider",
    "SandboxInstagramMetricsProvider",
    "SandboxTikTokMetricsProvider",
    "SandboxYouTubeMetricsProvider",
]
