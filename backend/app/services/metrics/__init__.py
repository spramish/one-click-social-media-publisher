"""Metrics provider registry.

The analytics service is written against
:class:`~app.services.metrics.base.MetricsProvider` and never names a concrete
platform, so the only place that maps a
:class:`~app.db.enums.SocialPlatform` to an implementation is
:func:`get_metrics_provider`. Adding a platform means adding one class and one
line here — nothing else in the codebase needs to change.

This mirrors ``app.services.publishers`` and ``app.services.oauth``: one
stateless instance per platform, and the same "fail loudly on an unregistered
platform" contract.
"""

from __future__ import annotations

from app.core.config import settings
from app.db.enums import SocialPlatform
from app.services.metrics.base import (
    AccountMetrics,
    MetricsAuthError,
    MetricsError,
    MetricsProvider,
    MetricsRateLimitError,
    MetricsTransientError,
    MetricsUnavailableError,
    PostMetrics,
    as_int,
)
from app.services.metrics.facebook import FacebookMetricsProvider
from app.services.metrics.instagram import InstagramMetricsProvider
from app.services.metrics.sandbox import (
    SandboxFacebookMetricsProvider,
    SandboxInstagramMetricsProvider,
    SandboxTikTokMetricsProvider,
    SandboxYouTubeMetricsProvider,
)
from app.services.metrics.tiktok import TikTokMetricsProvider
from app.services.metrics.youtube import YouTubeMetricsProvider

#: One provider instance per platform. Providers are stateless — they read the
#: Graph API version and timeouts from ``settings`` on every call, and the only
#: mutable attribute is the test-only ``transport`` — so a single instance per
#: platform is safe to share across requests and workers.
_PROVIDERS: dict[SocialPlatform, MetricsProvider] = {
    SocialPlatform.FACEBOOK: FacebookMetricsProvider(),
    SocialPlatform.INSTAGRAM: InstagramMetricsProvider(),
    SocialPlatform.YOUTUBE: YouTubeMetricsProvider(),
    SocialPlatform.TIKTOK: TikTokMetricsProvider(),
}

#: The ``DEMO_MODE`` alternative: deterministic simulated metrics.
#: See :mod:`app.services.metrics.sandbox`.
_SANDBOX_PROVIDERS: dict[SocialPlatform, MetricsProvider] = {
    SocialPlatform.FACEBOOK: SandboxFacebookMetricsProvider(),
    SocialPlatform.INSTAGRAM: SandboxInstagramMetricsProvider(),
    SocialPlatform.YOUTUBE: SandboxYouTubeMetricsProvider(),
    SocialPlatform.TIKTOK: SandboxTikTokMetricsProvider(),
}


def get_metrics_provider(platform: SocialPlatform) -> MetricsProvider:
    """Return the metrics provider that implements ``platform``.

    In ``DEMO_MODE`` the sandbox implementation is returned, which reports
    simulated numbers instead of calling the platform.

    Raises:
        MetricsUnavailableError: the platform has no registered metrics
            implementation. The platform's metrics are reported as unavailable
            rather than crashing the dashboard.
    """
    registry = _SANDBOX_PROVIDERS if settings.demo_mode else _PROVIDERS
    try:
        return registry[platform]
    except KeyError as exc:  # pragma: no cover - guarded by the enum
        raise MetricsUnavailableError(
            f"No metrics provider is registered for '{platform.value}'."
        ) from exc


def supported_metrics_platforms() -> tuple[SocialPlatform, ...]:
    """Every platform the analytics service can read metrics from."""
    return tuple(_PROVIDERS)


__all__ = [
    "AccountMetrics",
    "MetricsAuthError",
    "MetricsError",
    "MetricsProvider",
    "MetricsRateLimitError",
    "MetricsTransientError",
    "MetricsUnavailableError",
    "PostMetrics",
    "as_int",
    "get_metrics_provider",
    "supported_metrics_platforms",
]
