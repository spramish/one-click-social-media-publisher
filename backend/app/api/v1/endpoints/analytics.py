"""Analytics endpoints: performance reads plus an on-demand refresh.

Two rules mirror the rest of the API:

* **The workspace always comes from the URL.** :func:`require_permission`
  resolves it through ``TenantMixin.scoped`` and answers ``404`` for a workspace
  the caller is not a member of, so another tenant's numbers are never visible.
* **Reading is separate from refreshing.** Every ``GET`` here reads only
  snapshots already stored, so opening the dashboard never calls a platform.
  ``POST .../refresh`` is the single route that reaches out to the platforms, and
  it requires ``analytics:refresh`` — a permission only the roles that can
  publish hold.

``refresh`` returns tallies rather than raising: one platform's failure is
reported in ``errors`` and the sweep continues, so a revoked grant cannot freeze
the whole dashboard.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext, require_permission
from app.core.logging import get_logger
from app.core.permissions import ANALYTICS_READ, ANALYTICS_REFRESH
from app.db.enums import SocialPlatform
from app.db.session import get_db
from app.schemas import analytics as schemas
from app.services import analytics as analytics_service
from app.services.metrics import (
    MetricsAuthError,
    MetricsError,
    MetricsRateLimitError,
    MetricsTransientError,
    MetricsUnavailableError,
)

router = APIRouter()
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _translate(exc: Exception) -> HTTPException:
    """Map an analytics failure onto the documented status code."""
    if isinstance(exc, MetricsUnavailableError):
        # A capability gap: the platform has no public metrics API.
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, MetricsAuthError):
        # The account must be reconnected; retrying will not help.
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    if isinstance(exc, (MetricsRateLimitError, MetricsTransientError)):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, MetricsError):
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    if isinstance(exc, analytics_service.AnalyticsError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="The analytics request could not be completed.",
    )


# ---------------------------------------------------------------------------
# Capability
# ---------------------------------------------------------------------------
@router.get(
    "/{workspace_id}/capability",
    response_model=schemas.AnalyticsCapabilityPublic,
    summary="Which platforms can be reported on, and how many accounts each has",
)
def get_capability(
    context: WorkspaceContext = Depends(require_permission(ANALYTICS_READ)),
    db: Session = Depends(get_db),
) -> schemas.AnalyticsCapabilityPublic:
    """Reports ``metrics_available: false`` for a platform with no public API."""
    return schemas.AnalyticsCapabilityPublic.from_capability(
        analytics_service.capability(db, context.workspace.id)
    )


# ---------------------------------------------------------------------------
# Dashboard reads
# ---------------------------------------------------------------------------
@router.get(
    "/{workspace_id}/overview",
    response_model=schemas.OverviewPublic,
    summary="Headline figures: published counts, summed metrics and audience",
)
def get_overview(
    context: WorkspaceContext = Depends(require_permission(ANALYTICS_READ)),
    db: Session = Depends(get_db),
) -> schemas.OverviewPublic:
    """Reads stored snapshots only; it never contacts a platform."""
    return schemas.OverviewPublic.from_overview(
        analytics_service.overview(db, context.workspace.id)
    )


@router.get(
    "/{workspace_id}/platforms",
    response_model=list[schemas.PlatformPerformancePublic],
    summary="Per-platform performance, for comparing destinations",
)
def get_platforms(
    context: WorkspaceContext = Depends(require_permission(ANALYTICS_READ)),
    db: Session = Depends(get_db),
) -> list[schemas.PlatformPerformancePublic]:
    """A breakdown per platform that has content to report on."""
    return [
        schemas.PlatformPerformancePublic.from_performance(row)
        for row in analytics_service.platform_performance(db, context.workspace.id)
    ]


@router.get(
    "/{workspace_id}/accounts",
    response_model=list[schemas.AccountPerformancePublic],
    summary="Per-account audience and performance",
)
def get_accounts(
    context: WorkspaceContext = Depends(require_permission(ANALYTICS_READ)),
    db: Session = Depends(get_db),
) -> list[schemas.AccountPerformancePublic]:
    """Account-level performance, so a user can see which connected account leads."""
    return [
        schemas.AccountPerformancePublic.from_performance(row)
        for row in analytics_service.account_performance(db, context.workspace.id)
    ]


@router.get(
    "/{workspace_id}/targets",
    response_model=list[schemas.TargetPerformancePublic],
    summary="Per-published-target metrics, newest publish first",
)
def get_targets(
    platform: SocialPlatform | None = Query(default=None, description="Filter by platform."),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: WorkspaceContext = Depends(require_permission(ANALYTICS_READ)),
    db: Session = Depends(get_db),
) -> list[schemas.TargetPerformancePublic]:
    """Each published destination with its latest stored metrics (or ``null``)."""
    return [
        schemas.TargetPerformancePublic.from_performance(row)
        for row in analytics_service.target_performance(
            db, context.workspace.id, platform=platform, limit=limit, offset=offset
        )
    ]


@router.get(
    "/{workspace_id}/history",
    response_model=list[schemas.HistoryPointPublic],
    summary="A daily performance series for the charts",
)
def get_history(
    days: int | None = Query(default=None, ge=1, description="Window in days."),
    platform: SocialPlatform | None = Query(default=None, description="Filter by platform."),
    context: WorkspaceContext = Depends(require_permission(ANALYTICS_READ)),
    db: Session = Depends(get_db),
) -> list[schemas.HistoryPointPublic]:
    """Oldest day first. Each day keeps only that day's newest snapshot per target."""
    return [
        schemas.HistoryPointPublic.from_point(point)
        for point in analytics_service.history(
            db, context.workspace.id, days=days, platform=platform
        )
    ]


# ---------------------------------------------------------------------------
# Refresh (the only network path)
# ---------------------------------------------------------------------------
@router.post(
    "/{workspace_id}/refresh",
    response_model=schemas.RefreshResultPublic,
    summary="Pull fresh metrics from each platform and store a new snapshot",
)
def refresh(
    context: WorkspaceContext = Depends(require_permission(ANALYTICS_REFRESH)),
    db: Session = Depends(get_db),
) -> schemas.RefreshResultPublic:
    """Reach out to the platforms for published targets and connected accounts.

    Never raises for a single destination's failure: those are collected in
    ``errors`` while the sweep continues and the successes are stored.
    """
    try:
        result = analytics_service.refresh(db, context.workspace.id)
    except MetricsError as exc:
        raise _translate(exc) from exc

    return schemas.RefreshResultPublic.from_result(result)


__all__ = ["router"]
