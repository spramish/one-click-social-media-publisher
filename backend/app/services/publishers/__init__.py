"""Publisher registry.

The orchestration service is written against
:class:`~app.services.publishers.base.Publisher` and never names a concrete
platform, so the only place that maps a
:class:`~app.db.enums.SocialPlatform` to an implementation is
:func:`get_publisher`. Adding a platform means adding one class and one line
here — nothing else in the codebase needs to change.

This mirrors ``app.services.oauth``: the same stateless-instance-per-platform
shape, and the same "fail loudly on an unregistered platform" contract.
"""

from __future__ import annotations

from app.db.enums import SocialPlatform
from app.services.publishers.base import (
    CaptionValidationError,
    MediaAsset,
    MediaValidationError,
    PublishAuthError,
    PublishError,
    PublishPermanentError,
    PublishRateLimitError,
    PublishRequest,
    PublishResult,
    PublishTransientError,
    Publisher,
    PublisherCapability,
)
from app.services.publishers.facebook import FacebookPublisher
from app.services.publishers.instagram import InstagramPublisher
from app.services.publishers.tiktok import TikTokPublisher
from app.services.publishers.youtube import YouTubePublisher

#: One publisher instance per platform. Publishers are stateless — they read
#: their limits from ``settings`` on every call, and the only mutable attribute
#: is the test-only ``transport`` — so a single instance per platform is safe to
#: share across requests and workers.
_PUBLISHERS: dict[SocialPlatform, Publisher] = {
    SocialPlatform.FACEBOOK: FacebookPublisher(),
    SocialPlatform.INSTAGRAM: InstagramPublisher(),
    SocialPlatform.YOUTUBE: YouTubePublisher(),
    SocialPlatform.TIKTOK: TikTokPublisher(),
}


def get_publisher(platform: SocialPlatform) -> Publisher:
    """Return the publisher that implements ``platform``.

    Raises:
        PublishError: the platform has no registered implementation. This is a
            programming error rather than a user error, so it fails loudly.
    """
    try:
        return _PUBLISHERS[platform]
    except KeyError as exc:  # pragma: no cover - guarded by the enum
        raise PublishError(f"No publisher is registered for '{platform.value}'.") from exc


def supported_platforms() -> tuple[SocialPlatform, ...]:
    """Every platform the publishing engine can post to."""
    return tuple(_PUBLISHERS)


__all__ = [
    "CaptionValidationError",
    "MediaAsset",
    "MediaValidationError",
    "PublishAuthError",
    "PublishError",
    "PublishPermanentError",
    "PublishRateLimitError",
    "PublishRequest",
    "PublishResult",
    "PublishTransientError",
    "Publisher",
    "PublisherCapability",
    "get_publisher",
    "supported_platforms",
]
