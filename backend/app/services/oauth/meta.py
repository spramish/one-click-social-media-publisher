"""Shared Meta (Facebook Graph) OAuth machinery.

Instagram publishing and Facebook Page publishing both ride on **Facebook
Login** and the Graph API, so the dialog URL, the token endpoint, the
long-lived-token exchange and the revocation call are identical. Only the
requested scopes and the way a connectable identity is read differ, and those
live in the two concrete providers.

Two Meta-specific behaviours are worth stating explicitly:

* **There is no refresh token.** Meta issues a short-lived user token that is
  exchanged once for a ~60-day long-lived token. "Refreshing" therefore means
  re-exchanging the current long-lived token. The provider stores that token in
  the *refresh* slot as well, so the generic refresh path works unchanged.
* **A Page carries its own token.** ``/me/accounts`` returns a Page access
  token that is what actually authorizes publishing, so each Page becomes its
  own connected account with its own stored credential.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import settings
from app.services.oauth.base import (
    AccountProfile,
    OAuthApiError,
    OAuthProvider,
    OAuthTokens,
)

GRAPH_SOURCE = "https://graph.facebook.com"
DIALOG_SOURCE = "https://www.facebook.com"


class MetaOAuthProvider(OAuthProvider):
    """Facebook-Login + Graph API base for the Facebook and Instagram providers."""

    display_name = "Meta"
    uses_pkce = False

    # --- endpoints ------------------------------------------------------
    def graph_version(self) -> str:
        return settings.meta_graph_version

    def graph_url(self, path: str) -> str:
        return f"{GRAPH_SOURCE}/{self.graph_version()}/{path.lstrip('/')}"

    def authorization_endpoint(self) -> str:
        return f"{DIALOG_SOURCE}/{self.graph_version()}/dialog/oauth"

    def token_endpoint(self) -> str:
        return self.graph_url("oauth/access_token")

    # --- token lifecycle ------------------------------------------------
    def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str | None,
        redirect_uri: str,
    ) -> OAuthTokens:
        """Exchange the code for a short-lived token, then upgrade it.

        The short-lived token lasts an hour, which is useless for a scheduler,
        so it is immediately traded for the ~60-day long-lived form.
        """
        payload = self._json(
            self._request(
                "GET",
                self.token_endpoint(),
                params={
                    "client_id": self.client_id(),
                    "client_secret": self.client_secret(),
                    "redirect_uri": redirect_uri,
                    "code": code,
                },
            ),
            action="exchange the authorization code",
        )
        short_lived = self._tokens_from_payload(payload)
        return self._exchange_long_lived(short_lived.access_token, scopes=short_lived.scopes)

    def refresh(self, *, refresh_token: str) -> OAuthTokens:
        """Re-exchange the current long-lived token for a fresh one."""
        return self._exchange_long_lived(refresh_token)

    def _exchange_long_lived(
        self, token: str, *, scopes: list[str] | None = None
    ) -> OAuthTokens:
        payload = self._json(
            self._request(
                "GET",
                self.token_endpoint(),
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": self.client_id(),
                    "client_secret": self.client_secret(),
                    "fb_exchange_token": token,
                },
            ),
            action="extend the access token",
        )
        tokens = self._tokens_from_payload(payload, scopes=scopes)
        # Meta has no refresh token: the long-lived token *is* what you refresh
        # with. Storing it in both slots keeps the generic path working.
        return OAuthTokens(
            access_token=tokens.access_token,
            refresh_token=tokens.access_token,
            token_type=tokens.token_type,
            scopes=tokens.scopes,
            expires_at=tokens.expires_at,
            raw=tokens.raw,
        )

    def _tokens_from_payload(
        self, payload: dict[str, Any], *, scopes: list[str] | None = None
    ) -> OAuthTokens:
        access_token = payload.get("access_token")
        if not access_token:
            raise OAuthApiError(
                f"{self.display_name} did not return an access token. "
                f"Check that the app's redirect URI matches exactly."
            )
        expires_in = payload.get("expires_in")
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
            if expires_in
            else None
        )
        return OAuthTokens(
            access_token=str(access_token),
            refresh_token=None,
            token_type=str(payload.get("token_type") or "Bearer").title(),
            scopes=scopes if scopes is not None else list(self.scopes()),
            expires_at=expires_at,
            raw=payload,
        )

    def granted_scopes(self, tokens: OAuthTokens) -> list[str]:
        """The subset of requested scopes the user actually granted."""
        payload = self._json(
            self._request(
                "GET",
                self.graph_url("me/permissions"),
                params={"access_token": tokens.access_token},
            ),
            action="read the granted permissions",
        )
        granted = [
            entry.get("permission")
            for entry in payload.get("data", [])
            if isinstance(entry, dict) and entry.get("status") == "granted"
        ]
        return [scope for scope in granted if scope]

    def revoke(self, tokens: OAuthTokens) -> bool:
        """Delete the app's grant, which invalidates every token issued from it."""
        response = self._request(
            "DELETE",
            self.graph_url("me/permissions"),
            params={"access_token": tokens.access_token},
        )
        if response.status_code >= 400:
            return False
        try:
            return bool(response.json().get("success", True))
        except ValueError:  # pragma: no cover - defensive
            return response.status_code < 400

    # --- helpers ---------------------------------------------------------
    def _page_avatar(self, node: dict[str, Any]) -> str | None:
        """Read a ``picture`` field, which Meta nests inconsistently."""
        picture = node.get("picture")
        if isinstance(picture, dict):
            data = picture.get("data")
            if isinstance(data, dict) and data.get("url"):
                return str(data["url"])
            if picture.get("url"):
                return str(picture["url"])
        if isinstance(picture, str):
            return picture
        return None

    def _accounts(self, tokens: OAuthTokens, fields: str) -> list[dict[str, Any]]:
        payload = self._json(
            self._request(
                "GET",
                self.graph_url("me/accounts"),
                params={"fields": fields, "limit": 200, "access_token": tokens.access_token},
            ),
            action="list the Pages you manage",
        )
        data = payload.get("data")
        if not isinstance(data, list):
            raise OAuthApiError(f"{self.display_name} returned no Page list.")
        return [node for node in data if isinstance(node, dict)]

    def _page_tokens(
        self, node: dict[str, Any], *, fallback: OAuthTokens, scopes: list[str]
    ) -> OAuthTokens:
        """A Page's own access token, or the grant token when Meta omitted it."""
        page_token = node.get("access_token")
        if not page_token:
            return fallback
        return OAuthTokens(
            access_token=str(page_token),
            refresh_token=str(page_token),
            token_type=fallback.token_type,
            scopes=scopes,
            # Page tokens minted from a long-lived user token do not expire on
            # their own; they die when the user token behind them does.
            expires_at=fallback.expires_at,
        )


__all__ = ["MetaOAuthProvider"]
