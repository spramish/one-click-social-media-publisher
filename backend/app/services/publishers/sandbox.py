"""Sandbox publishers — a platform-free demonstration of the publishing engine.

A real publish uploads bytes to Meta/Google/TikTok and waits for the platform to
confirm processing. In ``DEMO_MODE`` there is no platform to call, so each class
here inherits its real counterpart (keeping that platform's capability limits,
caption and title rules) and overrides only :meth:`publish`, returning a
synthetic :class:`~app.services.publishers.base.PublishResult` with a terminal,
successful status.

Validation is *not* stubbed: :meth:`publish` still runs the platform's real
``validate_media`` / ``validate_caption``, so an oversized file or a
too-long caption is refused exactly as it would be in production.
"""

from __future__ import annotations

import uuid

from app.services.publishers.base import PublishRequest, PublishResult
from app.services.publishers.facebook import FacebookPublisher
from app.services.publishers.instagram import InstagramPublisher
from app.services.publishers.tiktok import TikTokPublisher
from app.services.publishers.youtube import YouTubePublisher

#: Prefix on every simulated remote post id, so a sandbox publish is
#: distinguishable in the job log and the post history.
SANDBOX_POST_PREFIX = "sandbox-"


class _SandboxPublisher:
    """Mixin that replaces a real publisher's network upload with a local result.

    Mixed in *before* the real publisher so only :meth:`publish` is overridden;
    the real class still supplies ``platform``, ``display_name`` and
    :meth:`capability`.
    """

    def publish(self, request: PublishRequest) -> PublishResult:
        """Validate as the real platform would, then report a simulated success."""
        self.validate_media(request.asset)
        self.validate_caption(caption=request.caption, title=request.title)
        return PublishResult(
            remote_post_id=f"{SANDBOX_POST_PREFIX}{self.platform.value}-{uuid.uuid4().hex[:12]}",
            remote_url=None,
            status="published",
            raw={
                "sandbox": True,
                "privacy": request.privacy,
                "caption": request.caption,
                "title": request.title,
            },
        )


class SandboxFacebookPublisher(_SandboxPublisher, FacebookPublisher):
    """Facebook publishing, simulated in-process."""


class SandboxInstagramPublisher(_SandboxPublisher, InstagramPublisher):
    """Instagram publishing, simulated in-process."""


class SandboxYouTubePublisher(_SandboxPublisher, YouTubePublisher):
    """YouTube publishing, simulated in-process."""


class SandboxTikTokPublisher(_SandboxPublisher, TikTokPublisher):
    """TikTok publishing, simulated in-process."""


__all__ = [
    "SANDBOX_POST_PREFIX",
    "SandboxFacebookPublisher",
    "SandboxInstagramPublisher",
    "SandboxTikTokPublisher",
    "SandboxYouTubePublisher",
]
