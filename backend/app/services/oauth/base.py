"""Shared OAuth 2.0 machinery for the platform providers.

Every social platform the publisher supports speaks OAuth 2.0 with an
authorization-code grant, but each one differs in its endpoint URLs, scope
names, response shape and revocation story. This module holds the parts that
are genuinely common — PKCE, the ``state`` value, the HTTP client, error
taxonomy, and the token/profile value objects — so a provider module only has
to describe *what is different*.

Design notes:

* **No secret ever reaches a log or an exception message.** Failures report the
  endpoint and the platform's own error text, never the client secret or a
  token.
* **The HTTP layer is injectable.** ``transport`` exists so the test suite can
  drive every provider against a local ``httpx.MockTransport`` instead of the
  real platform — the master prompt requires mocked platform APIs for local
  testing.
* **A missing client id/secret is not an error, it is a capability gap.** An
  unconfigured provider raises :class:`OAuthNotConfiguredError`, which the API
  turns into ``503`` so an operator knows to fill in ``.env``.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.db.enums import SocialPlatform

logger = get_logger(__name__)

#: Length of the PKCE code verifier. RFC 7636 allows 43–128 characters;
#: ``token_urlsafe(64)`` yields 86 characters of high-entropy text.
_PKCE_VERIFIER_BYTES = 64

#: Entropy of the ``state`` parameter. 32 bytes = 256 bits, which is what makes
#: guessing an in-flight state (and thus injecting an authorization code)
#: computationally infeasible.
_STATE_BYTES = 32


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class OAuthError(Exception):
    """Base class for every OAuth failure the API layer maps to a status code."""


class OAuthNotConfiguredError(OAuthError):
    """The platform's client credentials are missing from configuration."""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__(
            f"The {provider} integration is not configured on this server. "
            f"Set its client id/secret in the backend environment."
        )


class OAuthStateError(OAuthError):
    """The ``state`` was missing, unknown, already used, or expired."""


class OAuthAuthorizationError(OAuthError):
    """The platform refused the authorization code or returned an error."""


class OAuthTokenError(OAuthError):
    """A token could not be obtained, refreshed, or was revoked upstream."""


class OAuthApiError(OAuthError):
    """A platform API call failed while reading account information."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class OAuthTokens:
    """The credentials a platform handed back, in platform-neutral form."""

    access_token: str
    refresh_token: str | None = None
    token_type: str = "Bearer"
    scopes: list[str] = field(default_factory=list)
    expires_at: datetime | None = None
    #: Raw provider payload. Kept in memory for diagnostics only — the account
    #: service persists just the fields above.
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def is_expired(self, *, margin_seconds: int = 0) -> bool:
        """True when the token is expired *or* about to expire.

        ``margin_seconds`` lets a caller refresh proactively: a token that dies
        in 30 seconds is useless to a publish that takes longer than that.
        """
        if self.expires_at is None:
            return False
        now = datetime.now(timezone.utc)
        return self.expires_at <= now + timedelta(seconds=margin_seconds)

    def redacted(self) -> dict[str, Any]:
        """A log-safe summary — never the token values themselves."""
        return {
            "token_type": self.token_type,
            "has_refresh_token": self.refresh_token is not None,
            "scopes": self.scopes,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


@dataclass(frozen=True, slots=True)
class AccountProfile:
    """Identity of the connected account, as the platform reports it.

    ``tokens`` is set only when the platform issues a *per-identity* credential
    that differs from the grant's own token — a Facebook Page access token, for
    example. When it is ``None`` the account is stored with the tokens from the
    authorization exchange.
    """

    platform_account_id: str
    display_name: str
    username: str | None = None
    avatar_url: str | None = None
    tokens: "OAuthTokens | None" = None


# ---------------------------------------------------------------------------
# PKCE + state helpers
# ---------------------------------------------------------------------------
def generate_code_verifier() -> str:
    """Return a fresh PKCE code verifier (RFC 7636 §4.1)."""
    return secrets.token_urlsafe(_PKCE_VERIFIER_BYTES)


def code_challenge_for(verifier: str) -> str:
    """``BASE64URL(SHA256(verifier))`` without padding — the S256 challenge."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def generate_state() -> str:
    """Return an unguessable ``state`` value (CSRF protection)."""
    return secrets.token_urlsafe(_STATE_BYTES)


# ---------------------------------------------------------------------------
# Provider base
# ---------------------------------------------------------------------------
class OAuthProvider(ABC):
    """One social platform's OAuth 2.0 implementation.

    Subclasses set :attr:`platform`, :attr:`display_name`, :attr:`uses_pkce`
    and implement :meth:`scopes`, :meth:`authorization_endpoint`,
    :meth:`token_endpoint`, :meth:`exchange_code`, :meth:`refresh` and
    :meth:`fetch_profile`.
    """

    #: Which :class:`~app.db.enums.SocialPlatform` this provider implements.
    platform: ClassVar[SocialPlatform]
    #: Name used in error messages.
    display_name: ClassVar[str] = "provider"
    #: Whether the platform requires (or supports) PKCE.
    uses_pkce: ClassVar[bool] = False
    #: Optional transport override used by the test suite.
    transport: ClassVar[httpx.BaseTransport | None] = None

    # --- configuration --------------------------------------------------
    def client_id(self) -> str:
        """The public client identifier issued by the platform."""
        raise NotImplementedError

    def client_secret(self) -> str:
        """The client secret. Never logged, never returned, never stored client-side."""
        raise NotImplementedError

    def is_configured(self) -> bool:
        """True when both halves of the client credential are present."""
        return bool(self.client_id().strip() and self.client_secret().strip())

    def require_configured(self) -> None:
        """Raise a capability error instead of performing a broken request."""
        if not self.is_configured():
            raise OAuthNotConfiguredError(self.display_name)

    # --- URLs -----------------------------------------------------------
    @abstractmethod
    def authorization_endpoint(self) -> str:
        """Where the browser is sent to grant access."""

    @abstractmethod
    def token_endpoint(self) -> str:
        """Where an authorization code (and a refresh token) is exchanged."""

    @abstractmethod
    def scopes(self) -> tuple[str, ...]:
        """The scope strings requested in this integration."""

    def callback_url(self) -> str:
        """The registered ``redirect_uri``.

        Built from ``BACKEND_PUBLIC_URL`` so it matches what is registered with
        the platform regardless of which host the API happens to be running on.
        """
        base = settings.backend_public_url.rstrip("/")
        return f"{base}{settings.api_v1_prefix}/accounts/{self.platform.value}/callback"

    def extra_authorization_params(self) -> dict[str, str]:
        """Provider-specific additions to the authorization URL."""
        return {}

    def authorization_url(self, *, state: str, code_challenge: str | None = None) -> str:
        """Build the full consent URL the user's browser is redirected to."""
        self.require_configured()
        params: dict[str, str] = {
            "client_id": self.client_id(),
            "redirect_uri": self.callback_url(),
            "response_type": "code",
            "scope": " ".join(self.scopes()),
            "state": state,
        }
        if self.uses_pkce and code_challenge is not None:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        params.update(self.extra_authorization_params())
        return f"{self.authorization_endpoint()}?{urlencode(params)}"

    # --- token lifecycle ------------------------------------------------
    @abstractmethod
    def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str | None,
        redirect_uri: str,
    ) -> OAuthTokens:
        """Trade an authorization code for tokens."""

    @abstractmethod
    def refresh(self, *, refresh_token: str) -> OAuthTokens:
        """Obtain a fresh access token from a refresh token."""

    @abstractmethod
    def fetch_profiles(self, tokens: OAuthTokens) -> list[AccountProfile]:
        """Read every identity this grant can publish to.

        Most platforms expose exactly one (a channel, a TikTok user). Meta is
        the exception: one Facebook grant covers every Page the user manages,
        and each Page — or the Instagram professional account attached to it —
        is a separate publishing destination.
        """

    def fetch_profile(self, tokens: OAuthTokens) -> AccountProfile:
        """The single identity behind a grant, for platforms that have one."""
        profiles = self.fetch_profiles(tokens)
        if not profiles:
            raise OAuthApiError(
                f"The {self.display_name} account you authorized has no "
                f"publishing destination attached to it."
            )
        return profiles[0]

    def revoke(self, tokens: OAuthTokens) -> bool:
        """Best-effort upstream revocation.

        Returns ``True`` when the platform confirmed the grant is gone. The base
        implementation reports that the platform does not support it, which the
        API surfaces as "disconnected locally, not revoked upstream".
        """
        return False

    # --- HTTP ------------------------------------------------------------
    def _client(self) -> httpx.Client:
        return httpx.Client(
            transport=self.transport,
            timeout=settings.oauth_http_timeout_seconds,
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        form: bool = False,
    ) -> httpx.Response:
        """Perform one platform call, translating transport faults.

        A connection error becomes an :class:`OAuthApiError` rather than
        propagating an ``httpx`` exception, so callers only ever handle the
        OAuth error taxonomy.
        """
        try:
            with self._client() as client:
                return client.request(
                    method,
                    url,
                    data=data if form else None,
                    json=None if form else data,
                    params=params,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            logger.warning(
                "oauth http call failed",
                extra={"platform": self.platform.value, "url": url, "error": str(exc)},
            )
            raise OAuthApiError(
                f"Could not reach the {self.display_name} API. Please try again."
            ) from exc

    def _failure_from_payload(
        self, response: httpx.Response, payload: Any
    ) -> str | None:
        """Return a human-readable failure reason, or ``None`` for a success.

        Platforms disagree about what a failure looks like — some use the HTTP
        status, some embed an ``error`` object, and TikTok returns an ``error``
        envelope with ``code: "ok"`` on success — so this is both a hook and the
        single place that decides whether a response is usable.
        """
        if response.status_code >= 400:
            if isinstance(payload, (dict, list)):
                return _describe_error(payload)
            return f"HTTP {response.status_code}"
        if isinstance(payload, dict) and payload.get("error"):
            return _describe_error(payload)
        return None

    def _json(self, response: httpx.Response, *, action: str) -> dict[str, Any]:
        """Parse a JSON body, or raise a platform-shaped error.

        The platform's own ``error``/``error_description`` text is included so a
        support engineer can see *why* a call failed, but the request that
        carried the client secret is never echoed.
        """
        try:
            payload = response.json()
        except ValueError as exc:
            raise OAuthApiError(
                f"{self.display_name} returned a non-JSON response while trying to {action} "
                f"(HTTP {response.status_code})."
            ) from exc

        failure = self._failure_from_payload(response, payload)
        if failure is not None:
            logger.warning(
                "oauth api error",
                extra={
                    "platform": self.platform.value,
                    "action": action,
                    "status": response.status_code,
                    "error": failure,
                },
            )
            raise OAuthApiError(f"{self.display_name} could not {action}: {failure}")
        if not isinstance(payload, dict):  # pragma: no cover - defensive
            raise OAuthApiError(
                f"{self.display_name} returned an unexpected response while trying to {action}."
            )
        return payload

    # --- misc -------------------------------------------------------------
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} configured={self.is_configured()}>"


def _describe_error(payload: dict[str, Any] | list[Any]) -> str:
    """Extract a human-readable reason from a platform error body."""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            # Meta and Google both nest a message inside an object.
            message = error.get("message") or error.get("type") or error.get("code")
            description = error.get("error_user_msg") or payload.get("error_description")
            if message and description and description != message:
                return f"{message} ({description})"
            if message:
                return str(message)
        if isinstance(error, str):
            description = payload.get("error_description") or payload.get("message")
            return f"{error}: {description}" if description else error
        for key in ("message", "error_description", "detail"):
            if payload.get(key):
                return str(payload[key])
    return "the platform rejected the request"


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
]
