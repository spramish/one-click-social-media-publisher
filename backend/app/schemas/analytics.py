"""Request/response schemas for the analytics dashboard.

The one rule this module exists to preserve is the phase's: **unavailable is
not zero**. Every metric is typed ``int | None`` and every metric set carries a
companion ``available`` count, so the UI can render a figure it does not have as
"not available" instead of a misleading ``0``.

Nothing here invents a value: a field is exactly what the platform returned
(``0`` included) or ``None`` when the platform did not provide it, and
``retrieved_at`` records when the snapshot was read.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.db.enums import SocialAccountStatus, SocialPlatform
from app.services import analytics as analytics_service

#: The metric fields counted for ``MetricSetPublic.available``.
_METRIC_FIELDS = ("views", "likes", "comments", "shares", "reach", "impressions", "engagement")
#: The audience fields counted for ``AudienceTotalsPublic.available``.
_AUDIENCE_FIELDS = ("followers", "follows", "posts_count")


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------
class MetricSetPublic(BaseModel):
    """One set of performance figures, each ``null`` when unavailable."""

    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    reach: int | None = None
    impressions: int | None = None
    engagement: int | None = None
    #: How many of the fields above a platform actually provided. ``0`` means
    #: every metric is unavailable; it never means "no engagement".
    available: int = 0

    @classmethod
    def from_metrics(cls, metrics: analytics_service.MetricSet) -> "MetricSetPublic":
        return cls(
            views=metrics.views,
            likes=metrics.likes,
            comments=metrics.comments,
            shares=metrics.shares,
            reach=metrics.reach,
            impressions=metrics.impressions,
            engagement=metrics.engagement,
            available=sum(
                1 for name in _METRIC_FIELDS if getattr(metrics, name) is not None
            ),
        )


class AudienceTotalsPublic(BaseModel):
    """Audience figures, each ``null`` when unavailable."""

    followers: int | None = None
    follows: int | None = None
    posts_count: int | None = None
    available: int = 0

    @classmethod
    def from_totals(cls, totals: analytics_service.AudienceTotals) -> "AudienceTotalsPublic":
        return cls(
            followers=totals.followers,
            follows=totals.follows,
            posts_count=totals.posts_count,
            available=sum(
                1 for name in _AUDIENCE_FIELDS if getattr(totals, name) is not None
            ),
        )


class MetricSnapshotPublic(BaseModel):
    """One destination's latest stored metrics and when they were retrieved."""

    metrics: MetricSetPublic
    retrieved_at: datetime

    @classmethod
    def from_snapshot(
        cls, snapshot: analytics_service.MetricSnapshot
    ) -> "MetricSnapshotPublic":
        return cls(
            metrics=MetricSetPublic.from_metrics(snapshot.metrics),
            retrieved_at=snapshot.retrieved_at,
        )


# ---------------------------------------------------------------------------
# Dashboard reads
# ---------------------------------------------------------------------------
class OverviewPublic(BaseModel):
    """The dashboard's headline numbers."""

    published_post_count: int
    published_target_count: int
    tracked_target_count: int
    totals: MetricSetPublic
    audience: AudienceTotalsPublic
    last_refreshed_at: datetime | None = None

    @classmethod
    def from_overview(cls, overview: analytics_service.Overview) -> "OverviewPublic":
        return cls(
            published_post_count=overview.published_post_count,
            published_target_count=overview.published_target_count,
            tracked_target_count=overview.tracked_target_count,
            totals=MetricSetPublic.from_metrics(overview.totals),
            audience=AudienceTotalsPublic.from_totals(overview.audience),
            last_refreshed_at=overview.last_refreshed_at,
        )


class PlatformPerformancePublic(BaseModel):
    """One platform's published count and summed latest metrics."""

    platform: SocialPlatform
    display_name: str
    metrics_available: bool
    published_target_count: int
    tracked_target_count: int
    totals: MetricSetPublic
    last_refreshed_at: datetime | None = None

    @classmethod
    def from_performance(
        cls, performance: analytics_service.PlatformPerformance
    ) -> "PlatformPerformancePublic":
        return cls(
            platform=performance.platform,
            display_name=performance.display_name,
            metrics_available=performance.metrics_available,
            published_target_count=performance.published_target_count,
            tracked_target_count=performance.tracked_target_count,
            totals=MetricSetPublic.from_metrics(performance.totals),
            last_refreshed_at=performance.last_refreshed_at,
        )


class AccountPerformancePublic(BaseModel):
    """One connected account's audience and the performance of its targets."""

    social_account_id: uuid.UUID
    platform: SocialPlatform
    display_name: str
    username: str | None = None
    status: SocialAccountStatus
    published_target_count: int
    audience: AudienceTotalsPublic
    audience_retrieved_at: datetime | None = None
    totals: MetricSetPublic

    @classmethod
    def from_performance(
        cls, performance: analytics_service.AccountPerformance
    ) -> "AccountPerformancePublic":
        return cls(
            social_account_id=performance.social_account_id,
            platform=performance.platform,
            display_name=performance.display_name,
            username=performance.username,
            status=performance.status,
            published_target_count=performance.published_target_count,
            audience=AudienceTotalsPublic.from_totals(performance.audience),
            audience_retrieved_at=performance.audience_retrieved_at,
            totals=MetricSetPublic.from_metrics(performance.totals),
        )


class TargetPerformancePublic(BaseModel):
    """One published target's latest stored metrics."""

    post_target_id: uuid.UUID
    post_id: uuid.UUID
    platform: SocialPlatform
    remote_post_id: str | None = None
    remote_url: str | None = None
    published_at: datetime | None = None
    #: ``null`` when the target has never been refreshed or a provider could not
    #: read it — the destination is still listed, its metrics simply unavailable.
    snapshot: MetricSnapshotPublic | None = None

    @classmethod
    def from_performance(
        cls, performance: analytics_service.TargetPerformance
    ) -> "TargetPerformancePublic":
        return cls(
            post_target_id=performance.post_target_id,
            post_id=performance.post_id,
            platform=performance.platform,
            remote_post_id=performance.remote_post_id,
            remote_url=performance.remote_url,
            published_at=performance.published_at,
            snapshot=(
                MetricSnapshotPublic.from_snapshot(performance.snapshot)
                if performance.snapshot is not None
                else None
            ),
        )


class HistoryPointPublic(BaseModel):
    """One day of the historical performance chart."""

    day: date
    samples: int
    metrics: MetricSetPublic

    @classmethod
    def from_point(cls, point: analytics_service.HistoryPoint) -> "HistoryPointPublic":
        return cls(
            day=point.day,
            samples=point.samples,
            metrics=MetricSetPublic.from_metrics(point.metrics),
        )


# ---------------------------------------------------------------------------
# Capability
# ---------------------------------------------------------------------------
class PlatformCapabilityPublic(BaseModel):
    """Whether one platform's metrics can be read, and how many accounts it has."""

    platform: SocialPlatform
    display_name: str
    metrics_available: bool
    connected_accounts: int

    @classmethod
    def from_capability(
        cls, capability: analytics_service.PlatformCapability
    ) -> "PlatformCapabilityPublic":
        return cls(
            platform=capability.platform,
            display_name=capability.display_name,
            metrics_available=capability.metrics_available,
            connected_accounts=capability.connected_accounts,
        )


class AnalyticsCapabilityPublic(BaseModel):
    """What this server can report on."""

    platforms: list[PlatformCapabilityPublic]
    default_window_days: int
    max_history_points: int
    max_refresh_targets: int

    @classmethod
    def from_capability(
        cls, capability: analytics_service.AnalyticsCapability
    ) -> "AnalyticsCapabilityPublic":
        return cls(
            platforms=[
                PlatformCapabilityPublic.from_capability(platform)
                for platform in capability.platforms
            ],
            default_window_days=capability.default_window_days,
            max_history_points=capability.max_history_points,
            max_refresh_targets=capability.max_refresh_targets,
        )


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------
class RefreshResultPublic(BaseModel):
    """The tallies from one refresh sweep.

    ``errors`` holds one line per destination that could not be read. It is
    informational: a failure on one platform never prevents the others' numbers
    from updating.
    """

    post_targets_refreshed: int
    post_targets_skipped: int
    accounts_refreshed: int
    accounts_skipped: int
    snapshots_written: int
    errors: list[str]

    @classmethod
    def from_result(cls, result: analytics_service.RefreshResult) -> "RefreshResultPublic":
        return cls(
            post_targets_refreshed=result.post_targets_refreshed,
            post_targets_skipped=result.post_targets_skipped,
            accounts_refreshed=result.accounts_refreshed,
            accounts_skipped=result.accounts_skipped,
            snapshots_written=result.snapshots_written,
            errors=result.errors,
        )


__all__ = [
    "AccountPerformancePublic",
    "AnalyticsCapabilityPublic",
    "AudienceTotalsPublic",
    "HistoryPointPublic",
    "MetricSetPublic",
    "MetricSnapshotPublic",
    "OverviewPublic",
    "PlatformCapabilityPublic",
    "PlatformPerformancePublic",
    "RefreshResultPublic",
    "TargetPerformancePublic",
]
