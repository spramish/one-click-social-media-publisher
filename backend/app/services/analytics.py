"""Analytics: read performance metrics back from the platforms.

This module is the single place the rest of the application talks to for
performance data. It has two jobs:

* **Read** — :func:`overview`, :func:`platform_performance`,
  :func:`account_performance`, :func:`target_performance` and :func:`history`
  answer the dashboard's questions from the Phase 10 snapshot tables
  (``post_metrics`` / ``account_metrics``). These are pure reads: no network.
* **Refresh** — :func:`refresh` calls each platform's official metrics API for
  the workspace's published targets and connected accounts and appends a new
  snapshot per destination. This is the only function that touches the network.

Three rules shape everything here:

* **Unavailable is not zero.** A metric is ``int | None`` throughout. A
  platform that does not expose a figure leaves the column ``NULL``, and the
  aggregates in this module report such a figure as ``None`` (rendered as
  "not available") rather than ``0``. The phase brief forbids inventing data, so
  nothing here estimates, interpolates or back-fills a missing value.
* **Tenant isolation is explicit.** An aggregate cannot reuse
  :meth:`TenantMixin.scoped` (it returns ``select(cls)``), so every query here
  filters ``workspace_id`` by hand — with the id that came from the caller's
  resolved workspace context, never from request input.
* **One platform's failure never aborts a refresh.** Each destination is read
  independently; a failure is recorded in the result's ``errors`` and the sweep
  continues, so a revoked TikTok grant cannot stop Facebook's numbers updating.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import utcnow
from app.db.enums import PostTargetStatus, SocialAccountStatus, SocialPlatform
from app.models.analytics import AccountMetric, PostMetric
from app.models.post import PostTarget
from app.models.social_account import SocialAccount
from app.services import accounts as accounts_service
from app.services.metrics import (
    AccountMetrics,
    MetricsError,
    MetricsUnavailableError,
    PostMetrics,
    get_metrics_provider,
    supported_metrics_platforms,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class AnalyticsError(Exception):
    """Base class for analytics-domain failures."""


# ---------------------------------------------------------------------------
# Read models
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class MetricSet:
    """A set of metric values where ``None`` means "not provided".

    ``engagement`` is derived from the three figures the platforms report
    interactively; it is ``None`` only when all three are unavailable.
    """

    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    reach: int | None = None
    impressions: int | None = None
    engagement: int | None = None


@dataclass(frozen=True, slots=True)
class AudienceTotals:
    """Audience figures summed over a set of accounts."""

    followers: int | None = None
    follows: int | None = None
    posts_count: int | None = None


@dataclass(frozen=True, slots=True)
class MetricSnapshot:
    """One destination's latest stored metrics, plus when they were retrieved."""

    metrics: MetricSet
    retrieved_at: datetime


@dataclass(frozen=True, slots=True)
class Overview:
    """The dashboard's headline numbers."""

    published_post_count: int
    published_target_count: int
    tracked_target_count: int
    totals: MetricSet
    audience: AudienceTotals
    last_refreshed_at: datetime | None


@dataclass(frozen=True, slots=True)
class PlatformPerformance:
    """One platform's published-target count and summed latest metrics."""

    platform: SocialPlatform
    display_name: str
    metrics_available: bool
    published_target_count: int
    tracked_target_count: int
    totals: MetricSet
    last_refreshed_at: datetime | None


@dataclass(frozen=True, slots=True)
class AccountPerformance:
    """One connected account's audience plus the performance of its targets."""

    social_account_id: uuid.UUID
    platform: SocialPlatform
    display_name: str
    username: str | None
    status: SocialAccountStatus
    published_target_count: int
    audience: AudienceTotals
    audience_retrieved_at: datetime | None
    totals: MetricSet


@dataclass(frozen=True, slots=True)
class TargetPerformance:
    """One published target's latest stored metrics."""

    post_target_id: uuid.UUID
    post_id: uuid.UUID
    platform: SocialPlatform
    remote_post_id: str | None
    remote_url: str | None
    published_at: datetime | None
    snapshot: MetricSnapshot | None


@dataclass(frozen=True, slots=True)
class HistoryPoint:
    """One day of the historical performance chart."""

    day: date
    samples: int
    metrics: MetricSet


@dataclass(frozen=True, slots=True)
class PlatformCapability:
    """Whether metrics can be read for one platform, and how many accounts it has."""

    platform: SocialPlatform
    display_name: str
    metrics_available: bool
    connected_accounts: int


@dataclass(frozen=True, slots=True)
class AnalyticsCapability:
    """What this server can report on."""

    platforms: tuple[PlatformCapability, ...]
    default_window_days: int
    max_history_points: int
    max_refresh_targets: int


@dataclass(frozen=True, slots=True)
class RefreshResult:
    """The tallies from one refresh sweep.

    ``errors`` holds one human-readable line per destination that could not be
    read. It never contains a token or a secret.
    """

    post_targets_refreshed: int
    post_targets_skipped: int
    accounts_refreshed: int
    accounts_skipped: int
    snapshots_written: int
    errors: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _platform_name(platform: SocialPlatform) -> str:
    """A display name for a platform, falling back to its title-cased value."""
    try:
        return get_metrics_provider(platform).display_name
    except MetricsUnavailableError:  # pragma: no cover - every platform is covered
        return platform.value.title()


def _sum_optional(values: list[int | None]) -> int | None:
    """Sum the available values, or return ``None`` when none were provided."""
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _engagement(likes: int | None, comments: int | None, shares: int | None) -> int | None:
    return _sum_optional([likes, comments, shares])


def _metric_set(
    views: int | None,
    likes: int | None,
    comments: int | None,
    shares: int | None,
    reach: int | None = None,
    impressions: int | None = None,
) -> MetricSet:
    return MetricSet(
        views=views,
        likes=likes,
        comments=comments,
        shares=shares,
        reach=reach,
        impressions=impressions,
        engagement=_engagement(likes, comments, shares),
    )


def _latest_post_metrics(workspace_id: uuid.UUID):
    """A subquery holding the newest snapshot per published target.

    ``DISTINCT ON`` keeps exactly one row per ``post_target_id`` — the one with
    the greatest ``retrieved_at`` — which is the destination's current
    performance. The workspace filter is written out because a subquery cannot
    use :meth:`TenantMixin.scoped`.
    """
    return (
        select(PostMetric)
        .where(PostMetric.workspace_id == workspace_id)
        .distinct(PostMetric.post_target_id)
        .order_by(PostMetric.post_target_id, PostMetric.retrieved_at.desc())
        .subquery()
    )


def _latest_account_metrics(workspace_id: uuid.UUID):
    """A subquery holding the newest audience snapshot per connected account."""
    return (
        select(AccountMetric)
        .where(AccountMetric.workspace_id == workspace_id)
        .distinct(AccountMetric.social_account_id)
        .order_by(AccountMetric.social_account_id, AccountMetric.retrieved_at.desc())
        .subquery()
    )


# ---------------------------------------------------------------------------
# Capability
# ---------------------------------------------------------------------------
def capability(db: Session, workspace_id: uuid.UUID) -> AnalyticsCapability:
    """Which platforms can be reported on, and how many accounts each has."""
    counts = {
        platform: count
        for platform, count in db.execute(
            select(SocialAccount.platform, func.count(SocialAccount.id))
            .where(
                SocialAccount.workspace_id == workspace_id,
                SocialAccount.deleted_at.is_(None),
                SocialAccount.status == SocialAccountStatus.CONNECTED,
            )
            .group_by(SocialAccount.platform)
        ).all()
    }
    available = set(supported_metrics_platforms())
    return AnalyticsCapability(
        platforms=tuple(
            PlatformCapability(
                platform=platform,
                display_name=_platform_name(platform),
                metrics_available=platform in available,
                connected_accounts=counts.get(platform, 0),
            )
            for platform in SocialPlatform
        ),
        default_window_days=settings.analytics_default_window_days,
        max_history_points=settings.analytics_max_history_points,
        max_refresh_targets=settings.analytics_max_refresh_targets,
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def _latest_post_totals_by_platform(
    db: Session, workspace_id: uuid.UUID
) -> dict[SocialPlatform, tuple[int, MetricSet]]:
    """``{platform: (tracked_target_count, totals)}`` over published targets."""
    latest = _latest_post_metrics(workspace_id)
    rows = db.execute(
        select(
            latest.c.platform,
            func.count(),
            func.sum(latest.c.views),
            func.sum(latest.c.likes),
            func.sum(latest.c.comments),
            func.sum(latest.c.shares),
            func.sum(latest.c.reach),
            func.sum(latest.c.impressions),
        )
        .join(PostTarget, PostTarget.id == latest.c.post_target_id)
        .where(
            PostTarget.workspace_id == workspace_id,
            PostTarget.status == PostTargetStatus.PUBLISHED,
        )
        .group_by(latest.c.platform)
    ).all()

    result: dict[SocialPlatform, tuple[int, MetricSet]] = {}
    for platform, count, views, likes, comments, shares, reach, impressions in rows:
        result[platform] = (
            int(count),
            _metric_set(
                _as_int(views),
                _as_int(likes),
                _as_int(comments),
                _as_int(shares),
                _as_int(reach),
                _as_int(impressions),
            ),
        )
    return result


def _latest_post_totals_by_account(
    db: Session, workspace_id: uuid.UUID
) -> dict[uuid.UUID, tuple[int, MetricSet]]:
    """``{social_account_id: (tracked_target_count, totals)}`` over published targets."""
    latest = _latest_post_metrics(workspace_id)
    rows = db.execute(
        select(
            PostTarget.social_account_id,
            func.count(),
            func.sum(latest.c.views),
            func.sum(latest.c.likes),
            func.sum(latest.c.comments),
            func.sum(latest.c.shares),
            func.sum(latest.c.reach),
            func.sum(latest.c.impressions),
        )
        .join(PostTarget, PostTarget.id == latest.c.post_target_id)
        .where(
            PostTarget.workspace_id == workspace_id,
            PostTarget.status == PostTargetStatus.PUBLISHED,
        )
        .group_by(PostTarget.social_account_id)
    ).all()

    result: dict[uuid.UUID, tuple[int, MetricSet]] = {}
    for account_id, count, views, likes, comments, shares, reach, impressions in rows:
        result[account_id] = (
            int(count),
            _metric_set(
                _as_int(views),
                _as_int(likes),
                _as_int(comments),
                _as_int(shares),
                _as_int(reach),
                _as_int(impressions),
            ),
        )
    return result


def _audience_totals(db: Session, workspace_id: uuid.UUID) -> tuple[AudienceTotals, datetime | None]:
    """Summed audience figures across the workspace's connected accounts."""
    latest = _latest_account_metrics(workspace_id)
    row = db.execute(
        select(
            func.sum(latest.c.followers),
            func.sum(latest.c.follows),
            func.sum(latest.c.posts_count),
            func.max(latest.c.retrieved_at),
        )
        .join(SocialAccount, SocialAccount.id == latest.c.social_account_id)
        .where(
            SocialAccount.workspace_id == workspace_id,
            SocialAccount.deleted_at.is_(None),
        )
    ).one()
    return (
        AudienceTotals(
            followers=_as_int(row[0]),
            follows=_as_int(row[1]),
            posts_count=_as_int(row[2]),
        ),
        row[3],
    )


def overview(db: Session, workspace_id: uuid.UUID) -> Overview:
    """The headline figures: published counts plus summed latest metrics."""
    published_post_count = db.scalar(
        select(func.count(PostTarget.post_id.distinct())).where(
            PostTarget.workspace_id == workspace_id,
            PostTarget.status == PostTargetStatus.PUBLISHED,
        )
    )
    published_target_count = db.scalar(
        select(func.count()).select_from(PostTarget).where(
            PostTarget.workspace_id == workspace_id,
            PostTarget.status == PostTargetStatus.PUBLISHED,
        )
    )

    by_platform = _latest_post_totals_by_platform(db, workspace_id)
    tracked = sum(count for count, _ in by_platform.values())
    totals = MetricSet(
        views=_sum_optional([m.views for _, m in by_platform.values()]),
        likes=_sum_optional([m.likes for _, m in by_platform.values()]),
        comments=_sum_optional([m.comments for _, m in by_platform.values()]),
        shares=_sum_optional([m.shares for _, m in by_platform.values()]),
        reach=_sum_optional([m.reach for _, m in by_platform.values()]),
        impressions=_sum_optional([m.impressions for _, m in by_platform.values()]),
        engagement=_sum_optional([m.engagement for _, m in by_platform.values()]),
    )
    audience, audience_at = _audience_totals(db, workspace_id)
    last_post_at = db.scalar(
        select(func.max(PostMetric.retrieved_at)).where(PostMetric.workspace_id == workspace_id)
    )
    last_refreshed = max(
        [value for value in (last_post_at, audience_at) if value is not None],
        default=None,
    )
    return Overview(
        published_post_count=int(published_post_count or 0),
        published_target_count=int(published_target_count or 0),
        tracked_target_count=tracked,
        totals=totals,
        audience=audience,
        last_refreshed_at=last_refreshed,
    )


def platform_performance(db: Session, workspace_id: uuid.UUID) -> list[PlatformPerformance]:
    """A per-platform breakdown, so the dashboard can compare destinations."""
    published_counts = dict(
        db.execute(
            select(PostTarget.platform, func.count())
            .where(
                PostTarget.workspace_id == workspace_id,
                PostTarget.status == PostTargetStatus.PUBLISHED,
            )
            .group_by(PostTarget.platform)
        ).all()
    )
    last_refreshed = dict(
        db.execute(
            select(PostMetric.platform, func.max(PostMetric.retrieved_at))
            .where(PostMetric.workspace_id == workspace_id)
            .group_by(PostMetric.platform)
        ).all()
    )
    totals_by_platform = _latest_post_totals_by_platform(db, workspace_id)
    available = set(supported_metrics_platforms())

    result: list[PlatformPerformance] = []
    for platform in SocialPlatform:
        if platform not in published_counts and platform not in totals_by_platform:
            # Only show platforms that actually have content to report on.
            continue
        tracked, totals = totals_by_platform.get(platform, (0, MetricSet()))
        result.append(
            PlatformPerformance(
                platform=platform,
                display_name=_platform_name(platform),
                metrics_available=platform in available,
                published_target_count=int(published_counts.get(platform, 0)),
                tracked_target_count=tracked,
                totals=totals,
                last_refreshed_at=last_refreshed.get(platform),
            )
        )
    return result


def account_performance(db: Session, workspace_id: uuid.UUID) -> list[AccountPerformance]:
    """Per-account audience and performance, newest platform order first."""
    accounts = list(
        db.scalars(
            SocialAccount.scoped(workspace_id).order_by(
                SocialAccount.platform, SocialAccount.display_name
            )
        )
    )
    latest_audience = _latest_account_metrics(workspace_id)
    audience_rows = {
        row[0]: row[1:]
        for row in db.execute(
            select(
                latest_audience.c.social_account_id,
                latest_audience.c.followers,
                latest_audience.c.follows,
                latest_audience.c.posts_count,
                latest_audience.c.retrieved_at,
            )
        ).all()
    }
    published_counts = dict(
        db.execute(
            select(PostTarget.social_account_id, func.count())
            .where(
                PostTarget.workspace_id == workspace_id,
                PostTarget.status == PostTargetStatus.PUBLISHED,
            )
            .group_by(PostTarget.social_account_id)
        ).all()
    )
    totals_by_account = _latest_post_totals_by_account(db, workspace_id)

    result: list[AccountPerformance] = []
    for account in accounts:
        audience_data = audience_rows.get(account.id)
        audience = AudienceTotals(
            followers=_as_int(audience_data[0]) if audience_data else None,
            follows=_as_int(audience_data[1]) if audience_data else None,
            posts_count=_as_int(audience_data[2]) if audience_data else None,
        )
        _, totals = totals_by_account.get(account.id, (0, MetricSet()))
        result.append(
            AccountPerformance(
                social_account_id=account.id,
                platform=account.platform,
                display_name=account.display_name,
                username=account.username,
                status=account.status,
                published_target_count=int(published_counts.get(account.id, 0)),
                audience=audience,
                audience_retrieved_at=audience_data[3] if audience_data else None,
                totals=totals,
            )
        )
    return result


def target_performance(
    db: Session,
    workspace_id: uuid.UUID,
    *,
    platform: SocialPlatform | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[TargetPerformance]:
    """Published targets with their latest stored metrics, newest publish first."""
    latest = _latest_post_metrics(workspace_id)
    stmt = (
        select(
            PostTarget,
            latest.c.views,
            latest.c.likes,
            latest.c.comments,
            latest.c.shares,
            latest.c.reach,
            latest.c.impressions,
            latest.c.retrieved_at,
        )
        .outerjoin(latest, latest.c.post_target_id == PostTarget.id)
        .where(
            PostTarget.workspace_id == workspace_id,
            PostTarget.status == PostTargetStatus.PUBLISHED,
        )
    )
    if platform is not None:
        stmt = stmt.where(PostTarget.platform == platform)
    stmt = (
        stmt.order_by(PostTarget.published_at.desc().nulls_last(), PostTarget.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    result: list[TargetPerformance] = []
    for row in db.execute(stmt).all():
        target = row[0]
        retrieved_at = row[7]
        snapshot = (
            MetricSnapshot(
                metrics=_metric_set(
                    _as_int(row[1]),
                    _as_int(row[2]),
                    _as_int(row[3]),
                    _as_int(row[4]),
                    _as_int(row[5]),
                    _as_int(row[6]),
                ),
                retrieved_at=retrieved_at,
            )
            if retrieved_at is not None
            else None
        )
        result.append(
            TargetPerformance(
                post_target_id=target.id,
                post_id=target.post_id,
                platform=target.platform,
                remote_post_id=target.remote_post_id,
                remote_url=target.remote_url,
                published_at=target.published_at,
                snapshot=snapshot,
            )
        )
    return result


def history(
    db: Session,
    workspace_id: uuid.UUID,
    *,
    days: int | None = None,
    platform: SocialPlatform | None = None,
) -> list[HistoryPoint]:
    """A daily performance series for the charts, oldest day first.

    For each day a destination contributes only its *latest* snapshot taken that
    day, so several refreshes in one day do not multiply its numbers. Values the
    platform did not provide stay ``NULL`` and are summed as "unavailable".
    """
    window = days or settings.analytics_default_window_days
    window = max(1, min(window, settings.analytics_max_history_points))
    since = utcnow() - timedelta(days=window)

    day = func.date_trunc("day", PostMetric.retrieved_at).label("day")
    filters = [PostMetric.workspace_id == workspace_id, PostMetric.retrieved_at >= since]
    if platform is not None:
        filters.append(PostMetric.platform == platform)

    daily = (
        select(
            PostMetric.post_target_id.label("target_id"),
            day,
            PostMetric.views,
            PostMetric.likes,
            PostMetric.comments,
            PostMetric.shares,
            PostMetric.reach,
            PostMetric.impressions,
        )
        .where(*filters)
        .distinct(PostMetric.post_target_id, day)
        .order_by(PostMetric.post_target_id, day, PostMetric.retrieved_at.desc())
        .subquery()
    )

    rows = db.execute(
        select(
            daily.c.day,
            func.count(),
            func.sum(daily.c.views),
            func.sum(daily.c.likes),
            func.sum(daily.c.comments),
            func.sum(daily.c.shares),
            func.sum(daily.c.reach),
            func.sum(daily.c.impressions),
        )
        .group_by(daily.c.day)
        .order_by(daily.c.day)
    ).all()

    return [
        HistoryPoint(
            day=row[0].date() if isinstance(row[0], datetime) else row[0],
            samples=int(row[1]),
            metrics=_metric_set(
                _as_int(row[2]),
                _as_int(row[3]),
                _as_int(row[4]),
                _as_int(row[5]),
                _as_int(row[6]),
                _as_int(row[7]),
            ),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Refresh (the only network path)
# ---------------------------------------------------------------------------
def refresh(db: Session, workspace_id: uuid.UUID) -> RefreshResult:
    """Pull fresh metrics for the workspace's published targets and accounts.

    Every destination is read independently: a failure on one is collected in
    the result's ``errors`` and the sweep continues, so a single revoked grant
    never freezes the whole dashboard. A platform with no metrics provider is
    counted as skipped, not failed.
    """
    limit = settings.analytics_max_refresh_targets

    accounts = accounts_service.list_accounts(
        db, workspace_id, status=SocialAccountStatus.CONNECTED
    )
    accounts_by_id = {account.id: account for account in accounts}
    tokens_cache: dict[uuid.UUID, object | None] = {}
    errors: list[str] = []
    snapshots = 0

    def tokens_for(account: SocialAccount):
        """A usable access token for one account, cached within the sweep."""
        if account.id in tokens_cache:
            return tokens_cache[account.id]
        try:
            tokens = accounts_service.ensure_fresh_tokens(db, account)
        except accounts_service.AccountError as exc:
            errors.append(f"{account.display_name}: {exc}")
            tokens = None
        tokens_cache[account.id] = tokens
        return tokens

    # --- audience metrics -------------------------------------------------
    accounts_refreshed = 0
    accounts_skipped = 0
    for account in accounts[:limit]:
        tokens = tokens_for(account)
        if tokens is None:
            accounts_skipped += 1
            continue
        try:
            provider = get_metrics_provider(account.platform)
            metrics: AccountMetrics = provider.fetch_account_metrics(
                access_token=tokens.access_token,
                platform_account_id=account.platform_account_id,
            )
        except MetricsError as exc:
            errors.append(f"{account.display_name}: {exc}")
            accounts_skipped += 1
            continue
        db.add(_account_metric_row(workspace_id, account, metrics))
        accounts_refreshed += 1
        snapshots += 1

    # --- video metrics ----------------------------------------------------
    target_limit = limit
    targets = list(
        db.scalars(
            select(PostTarget)
            .where(
                PostTarget.workspace_id == workspace_id,
                PostTarget.status == PostTargetStatus.PUBLISHED,
                PostTarget.remote_post_id.is_not(None),
            )
            .order_by(PostTarget.published_at.desc().nulls_last())
            .limit(target_limit)
        )
    )
    post_refreshed = 0
    post_skipped = 0
    for target in targets:
        account = accounts_by_id.get(target.social_account_id)
        if account is None or not target.remote_post_id:
            post_skipped += 1
            continue
        tokens = tokens_for(account)
        if tokens is None:
            post_skipped += 1
            continue
        try:
            provider = get_metrics_provider(target.platform)
            metrics: PostMetrics = provider.fetch_post_metrics(
                access_token=tokens.access_token,
                remote_post_id=target.remote_post_id,
                platform_account_id=account.platform_account_id,
            )
        except MetricsError as exc:
            errors.append(f"{_platform_name(target.platform)} ({target.remote_post_id}): {exc}")
            post_skipped += 1
            continue
        db.add(_post_metric_row(workspace_id, target, metrics))
        post_refreshed += 1
        snapshots += 1

    db.commit()
    logger.info(
        "analytics refreshed",
        extra={
            "workspace_id": str(workspace_id),
            "accounts_refreshed": accounts_refreshed,
            "post_targets_refreshed": post_refreshed,
            "snapshots_written": snapshots,
            "errors": len(errors),
        },
    )
    return RefreshResult(
        post_targets_refreshed=post_refreshed,
        post_targets_skipped=post_skipped,
        accounts_refreshed=accounts_refreshed,
        accounts_skipped=accounts_skipped,
        snapshots_written=snapshots,
        errors=errors,
    )


def _account_metric_row(
    workspace_id: uuid.UUID, account: SocialAccount, metrics: AccountMetrics
) -> AccountMetric:
    return AccountMetric(
        workspace_id=workspace_id,
        social_account_id=account.id,
        platform=account.platform,
        retrieved_at=metrics.retrieved_at,
        followers=metrics.followers,
        follows=metrics.follows,
        posts_count=metrics.posts_count,
        raw=metrics.raw,
    )


def _post_metric_row(
    workspace_id: uuid.UUID, target: PostTarget, metrics: PostMetrics
) -> PostMetric:
    return PostMetric(
        workspace_id=workspace_id,
        post_target_id=target.id,
        platform=target.platform,
        retrieved_at=metrics.retrieved_at,
        views=metrics.views,
        likes=metrics.likes,
        comments=metrics.comments,
        shares=metrics.shares,
        reach=metrics.reach,
        impressions=metrics.impressions,
        raw=metrics.raw,
    )


def latest_snapshot_for_target(
    db: Session, workspace_id: uuid.UUID, post_target_id: uuid.UUID
) -> MetricSnapshot | None:
    """One target's newest stored metrics, or ``None`` when it has none."""
    metric = db.scalar(
        select(PostMetric)
        .where(
            PostMetric.workspace_id == workspace_id,
            PostMetric.post_target_id == post_target_id,
        )
        .order_by(PostMetric.retrieved_at.desc())
        .limit(1)
    )
    if metric is None:
        return None
    return MetricSnapshot(
        metrics=_metric_set(
            metric.views,
            metric.likes,
            metric.comments,
            metric.shares,
            metric.reach,
            metric.impressions,
        ),
        retrieved_at=metric.retrieved_at,
    )


def _as_int(value: object) -> int | None:
    """Coerce a SQL aggregate result to ``int`` while preserving ``None``."""
    if value is None:
        return None
    if isinstance(value, bool):  # pragma: no cover - a bool never comes from SQL
        return int(value)
    if isinstance(value, int):
        return value
    return int(value)  # Decimal / str from the driver


__all__ = [
    "AccountPerformance",
    "AnalyticsCapability",
    "AnalyticsError",
    "AudienceTotals",
    "HistoryPoint",
    "MetricSet",
    "MetricSnapshot",
    "Overview",
    "PlatformCapability",
    "PlatformPerformance",
    "RefreshResult",
    "TargetPerformance",
    "account_performance",
    "capability",
    "history",
    "latest_snapshot_for_target",
    "overview",
    "platform_performance",
    "refresh",
    "target_performance",
]
