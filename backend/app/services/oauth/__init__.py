"""OAuth provider registry.

The account service is written against :class:`~app.services.oauth.base.OAuthProvider`
and never names a concrete platform, so the only place that maps a
:class:`~app.db.enums.SocialPlatform` to an implementation is
:func:`get_provider`. Adding a platform means adding one class and one line
here — nothing else in the codebase needs to change.
"""

from __future__ import annotations

from app.core.config import settings
from app.db.enums import SocialPlatform
from app.services.oauth.base import (
    AccountProfile,
    OAuthApiError,
    OAuthAuthorizationError,
    OAuthError,
    OAuthNotConfiguredError,
    OAuthProvider,
    OAuthStateError,
    OAuthTokenError,
    OAuthTokens,
    code_challenge_for,
    generate_code_verifier,
    generate_state,
)
from app.services.oauth.facebook import FacebookProvider
from app.services.oauth.instagram import InstagramProvider
from app.services.oauth.sandbox import (
    SandboxFacebookProvider,
    SandboxInstagramProvider,
    SandboxTikTokProvider,
    SandboxYouTubeProvider,
)
from app.services.oauth.tiktok import TikTokProvider
from app.services.oauth.youtube import YouTubeProvider

#: One provider instance per platform. Providers are stateless — they read
#: their configuration from ``settings`` on every call — so a single instance
#: per platform is safe to share across requests and workers.
_PROVIDERS: dict[SocialPlatform, OAuthProvider] = {
    SocialPlatform.FACEBOOK: FacebookProvider(),
    SocialPlatform.INSTAGRAM: InstagramProvider(),
    SocialPlatform.YOUTUBE: YouTubeProvider(),
    SocialPlatform.TIKTOK: TikTokProvider(),
}

#: The ``DEMO_MODE`` alternative: same platforms, no developer credentials
#: needed. See :mod:`app.services.oauth.sandbox`.
_SANDBOX_PROVIDERS: dict[SocialPlatform, OAuthProvider] = {
    SocialPlatform.FACEBOOK: SandboxFacebookProvider(),
    SocialPlatform.INSTAGRAM: SandboxInstagramProvider(),
    SocialPlatform.YOUTUBE: SandboxYouTubeProvider(),
    SocialPlatform.TIKTOK: SandboxTikTokProvider(),
}


def get_provider(platform: SocialPlatform) -> OAuthProvider:
    """Return the provider that implements ``platform``.

    In ``DEMO_MODE`` the sandbox implementation is returned, which reports
    itself as configured without any client credentials.

    Raises:
        OAuthError: the platform has no registered implementation. This is a
            programming error rather than a user error, so it fails loudly.
    """
    registry = _SANDBOX_PROVIDERS if settings.demo_mode else _PROVIDERS
    try:
        return registry[platform]
    except KeyError as exc:  # pragma: no cover - guarded by the enum
        raise OAuthError(f"No OAuth provider is registered for '{platform.value}'.") from exc


def supported_platforms() -> tuple[SocialPlatform, ...]:
    """Every platform the connection layer can authenticate against."""
    return tuple(_PROVIDERS)


__all__ = [
    "AccountProfile",
    "OAuthApiError",
    "OAuthAuthorizationError",
    "OAuthError",
    "OAuthNotConfiguredError",
    "OAuthProvider",
    "OAuthStateError",
    "OAuthTokenError",
    "OAuthTokens",
    "code_challenge_for",
    "generate_code_verifier",
    "generate_state",
    "get_provider",
    "supported_platforms",
]
