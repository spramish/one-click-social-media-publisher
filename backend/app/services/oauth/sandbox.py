"""Sandbox OAuth providers — a credential-free demonstration of the connect flow.

Real platform connections need a developer app registered with Meta, Google and
TikTok; without one, ``is_configured()`` is ``False`` and the connect button in
the dashboard is (correctly) disabled. That is the right behaviour for a real
deployment, but it makes the connection journey impossible to show on a machine
that has no such apps.

This module supplies a drop-in alternative for :data:`DEMO_MODE`. Each class
inherits its real counterpart — so it keeps the platform's identity, display
name and scopes — and overrides only the four steps that would otherwise leave
the process:

* :meth:`authorization_url` skips the platform and sends the browser straight to
  this server's own OAuth callback, carrying the same single-use ``state`` a real
  consent screen would echo back.
* :meth:`exchange_code` mints a local access/refresh token instead of calling the
  platform's token endpoint.
* :meth:`fetch_profiles` invents exactly one publishing destination.
* :meth:`revoke` reports the grant as gone.

Nothing here is special-cased in the account service: ``begin_authorization`` and
``complete_connection`` run unmodified, so the state is still validated under a
row lock and a genuine :class:`~app.models.social_account.SocialAccount` row with
an encrypted credential is still written. The only thing that is simulated is the
conversation with a remote platform.

Tokens carry the :data:`SANDBOX_TOKEN_PREFIX` so a stored credential is
recognisable as simulated without decrypting it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from app.services.oauth.base import AccountProfile, OAuthTokens
from app.services.oauth.facebook import FacebookProvider
from app.services.oauth.instagram import InstagramProvider
from app.services.oauth.tiktok import TikTokProvider
from app.services.oauth.youtube import YouTubeProvider

#: Embedded in every sandbox token and account id so a simulated credential is
#: identifiable at a glance.
SANDBOX_TOKEN_PREFIX = "sandbox-"

#: How far in the future a sandbox token expires. Long enough that nothing tries
#: to refresh during a demonstration.
_SANDBOX_TOKEN_TTL_DAYS = 60


class _SandboxProvider:
    """Mixin that turns a real provider into a self-contained sandbox.

    Mixed in *before* the real provider so its overrides win, while the real
    class still supplies ``platform``, ``display_name``, the scope list and the
    (unused) endpoint URLs.
    """

    def client_id(self) -> str:
        return "sandbox-client-id"

    def client_secret(self) -> str:
        return "sandbox-client-secret"

    def is_configured(self) -> bool:
        """A sandbox provider is always usable — that is its entire purpose."""
        return True

    def authorization_url(self, *, state: str, code_challenge: str | None = None) -> str:
        """Return a URL that lands on *this* server's callback, not a platform.

        The ``state`` is the value :func:`app.services.accounts.begin_authorization`
        just persisted, so :func:`app.services.accounts.complete_connection`
        validates it exactly as it would a real platform's redirect.
        """
        params = {"state": state, "code": f"{SANDBOX_TOKEN_PREFIX}code-{uuid.uuid4().hex}"}
        return f"{self.callback_url()}?{urlencode(params)}"

    def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str | None,
        redirect_uri: str,
    ) -> OAuthTokens:
        return self._issue_tokens()

    def refresh(self, *, refresh_token: str) -> OAuthTokens:
        return self._issue_tokens(refresh_token=refresh_token)

    def fetch_profiles(self, tokens: OAuthTokens) -> list[AccountProfile]:
        suffix = uuid.uuid4().hex[:8]
        return [
            AccountProfile(
                platform_account_id=f"{SANDBOX_TOKEN_PREFIX}{self.platform.value}-{suffix}",
                display_name=f"{self.display_name} (sandbox)",
                username=f"@{self.platform.value}_sandbox",
            )
        ]

    def revoke(self, tokens: OAuthTokens) -> bool:
        return True

    def _issue_tokens(self, *, refresh_token: str | None = None) -> OAuthTokens:
        return OAuthTokens(
            access_token=f"{SANDBOX_TOKEN_PREFIX}access-{uuid.uuid4().hex}",
            refresh_token=refresh_token or f"{SANDBOX_TOKEN_PREFIX}refresh-{uuid.uuid4().hex}",
            token_type="Bearer",
            scopes=list(self.scopes()),
            expires_at=datetime.now(timezone.utc) + timedelta(days=_SANDBOX_TOKEN_TTL_DAYS),
            raw={"sandbox": True},
        )


class SandboxFacebookProvider(_SandboxProvider, FacebookProvider):
    """Facebook connect flow that never leaves this process."""


class SandboxInstagramProvider(_SandboxProvider, InstagramProvider):
    """Instagram connect flow that never leaves this process."""


class SandboxYouTubeProvider(_SandboxProvider, YouTubeProvider):
    """YouTube connect flow that never leaves this process."""


class SandboxTikTokProvider(_SandboxProvider, TikTokProvider):
    """TikTok connect flow that never leaves this process."""


__all__ = [
    "SANDBOX_TOKEN_PREFIX",
    "SandboxFacebookProvider",
    "SandboxInstagramProvider",
    "SandboxTikTokProvider",
    "SandboxYouTubeProvider",
]
