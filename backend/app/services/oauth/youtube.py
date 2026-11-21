"""YouTube connection provider (Google OAuth 2.0 + YouTube Data API v3).

A YouTube *channel* is the publishing destination, so the connection stores the
channel id along with the Google refresh token.

Required app configuration (see ``docs/OAUTH_SETUP.md``):

* A Google Cloud project with the **YouTube Data API v3** enabled.
* An OAuth client of type **Web application**; the authorized redirect URI must
  equal ``{BACKEND_PUBLIC_URL}/api/v1/accounts/youtube/callback`` exactly.
* ``youtube.upload`` is a **sensitive/restricted** scope: it requires OAuth
  verification plus a YouTube **API compliance audit** before the app can be
  used by the general public. Unverified apps are capped at 100 test users and
  their uploads are forced to **private** visibility.
* The connecting Google account must own or manage the target channel.

Google is a PKCE-capable provider, so the authorization request uses S256.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import settings
from app.db.enums import SocialPlatform
from app.services.oauth.base import (
    AccountProfile,
    OAuthApiError,
    OAuthProvider,
    OAuthTokenError,
    OAuthTokens,
)

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
CHANNELS_ENDPOINT = "https://www.googleapis.com/youtube/v3/channels"

#: Upload video plus read channel/statistics data.
YOUTUBE_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
)


class YouTubeProvider(OAuthProvider):
    """Connects YouTube channels."""

    platform = SocialPlatform.YOUTUBE
    display_name = "YouTube"
    uses_pkce = True

    def client_id(self) -> str:
        return settings.youtube_client_id

    def client_secret(self) -> str:
        return settings.youtube_client_secret

    def authorization_endpoint(self) -> str:
        return AUTHORIZATION_ENDPOINT

    def token_endpoint(self) -> str:
        return TOKEN_ENDPOINT

    def scopes(self) -> tuple[str, ...]:
        return YOUTUBE_SCOPES

    def extra_authorization_params(self) -> dict[str, str]:
        # ``access_type=offline`` is what yields a refresh token, and
        # ``prompt=consent`` forces one to be re-issued on every connection so a
        # reconnect after revocation actually repairs the grant.
        return {"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"}

    # --- token lifecycle ------------------------------------------------
    def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str | None,
        redirect_uri: str,
    ) -> OAuthTokens:
        payload: dict[str, Any] = {
            "code": code,
            "client_id": self.client_id(),
            "client_secret": self.client_secret(),
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        if code_verifier:
            payload["code_verifier"] = code_verifier
        return self._post_token(payload, action="exchange the authorization code")

    def refresh(self, *, refresh_token: str) -> OAuthTokens:
        return self._post_token(
            {
                "refresh_token": refresh_token,
                "client_id": self.client_id(),
                "client_secret": self.client_secret(),
                "grant_type": "refresh_token",
            },
            action="refresh the access token",
        )

    def _post_token(self, payload: dict[str, Any], *, action: str) -> OAuthTokens:
        """POST to Google's token endpoint (form-encoded, as Google requires)."""
        body = self._json(
            self._request("POST", TOKEN_ENDPOINT, data=payload, form=True),
            action=action,
        )
        access_token = body.get("access_token")
        if not access_token:
            raise OAuthTokenError(
                "Google did not return an access token. The refresh token may have "
                "been revoked — reconnect the channel."
            )
        expires_in = body.get("expires_in")
        scope = body.get("scope") or ""
        return OAuthTokens(
            access_token=str(access_token),
            # Google omits ``refresh_token`` on refresh; keeping it ``None``
            # signals the account service to preserve the stored one.
            refresh_token=body.get("refresh_token"),
            token_type="Bearer",
            scopes=[part for part in scope.split(" ") if part] or list(self.scopes()),
            expires_at=(
                datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
                if expires_in
                else None
            ),
            raw=body,
        )

    # --- identity -------------------------------------------------------
    def fetch_profiles(self, tokens: OAuthTokens) -> list[AccountProfile]:
        body = self._json(
            self._request(
                "GET",
                CHANNELS_ENDPOINT,
                params={"part": "snippet", "mine": "true"},
                headers={"Authorization": f"Bearer {tokens.access_token}"},
            ),
            action="read your YouTube channel",
        )
        items = body.get("items") or []
        profiles: list[AccountProfile] = []
        for item in items:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            snippet = item.get("snippet") or {}
            thumbnails = snippet.get("thumbnails") or {}
            avatar = None
            for size in ("default", "medium", "high"):
                node = thumbnails.get(size)
                if isinstance(node, dict) and node.get("url"):
                    avatar = str(node["url"])
                    break
            profiles.append(
                AccountProfile(
                    platform_account_id=str(item["id"]),
                    display_name=str(snippet.get("title") or "YouTube channel"),
                    username=snippet.get("customUrl") or None,
                    avatar_url=avatar,
                )
            )
        if not profiles:
            raise OAuthApiError(
                "That Google account has no YouTube channel. Create a channel "
                "first, then reconnect."
            )
        return profiles

    def revoke(self, tokens: OAuthTokens) -> bool:
        """Google revokes the whole grant — access *and* refresh token."""
        response = self._request(
            "POST",
            REVOKE_ENDPOINT,
            data={"token": tokens.refresh_token or tokens.access_token},
            form=True,
        )
        # Google returns 200 for a successful revoke and 400 for an already
        # invalid token, which is the desired end state either way.
        return response.status_code < 400 or response.status_code == 400


__all__ = ["YOUTUBE_SCOPES", "YouTubeProvider"]
