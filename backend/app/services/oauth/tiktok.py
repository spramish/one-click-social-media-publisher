"""TikTok connection provider (Login Kit v2 + Content Posting API).

TikTok differs from the other three in three ways that matter here:

* scopes are **comma-separated** in the authorization URL, not space-separated;
* **PKCE is mandatory**, so every authorization carries an S256 challenge;
* successful responses still contain an ``error`` envelope (with
  ``code: "ok"``), so the failure check is overridden.

Required app configuration (see ``docs/OAUTH_SETUP.md``):

* A TikTok for Developers app with **Login Kit** and **Content Posting API**
  products added.
* The redirect URI must equal
  ``{BACKEND_PUBLIC_URL}/api/v1/accounts/tiktok/callback`` exactly.
* ``video.publish`` (direct post) and ``video.upload`` (draft to inbox) require
  the app to be **audited and approved**; until then only sandbox/target users
  can connect, and ``video.publish`` is unavailable.
* An unaudited app can only post to the user's **private** drafts, and TikTok
  requires the account to be in a supported region.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.db.enums import SocialPlatform
from app.services.oauth.base import (
    AccountProfile,
    OAuthApiError,
    OAuthProvider,
    OAuthTokenError,
    OAuthTokens,
)

AUTHORIZATION_ENDPOINT = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_ENDPOINT = "https://open.tiktokapis.com/v2/oauth/token/"
REVOKE_ENDPOINT = "https://open.tiktokapis.com/v2/oauth/revoke/"
USER_INFO_ENDPOINT = "https://open.tiktokapis.com/v2/user/info/"

#: Read the basic profile, publish directly, and upload to the inbox.
TIKTOK_SCOPES: tuple[str, ...] = (
    "user.info.basic",
    "video.publish",
    "video.upload",
)


class TikTokProvider(OAuthProvider):
    """Connects TikTok accounts."""

    platform = SocialPlatform.TIKTOK
    display_name = "TikTok"
    uses_pkce = True

    def client_id(self) -> str:
        return settings.tiktok_client_key

    def client_secret(self) -> str:
        return settings.tiktok_client_secret

    def authorization_endpoint(self) -> str:
        return AUTHORIZATION_ENDPOINT

    def token_endpoint(self) -> str:
        return TOKEN_ENDPOINT

    def scopes(self) -> tuple[str, ...]:
        return TIKTOK_SCOPES

    def authorization_url(self, *, state: str, code_challenge: str | None = None) -> str:
        """Build the consent URL with comma-separated scopes, as TikTok requires."""
        self.require_configured()
        params: dict[str, str] = {
            "client_key": self.client_id(),
            "response_type": "code",
            "scope": ",".join(self.scopes()),
            "redirect_uri": self.callback_url(),
            "state": state,
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        return f"{AUTHORIZATION_ENDPOINT}?{urlencode(params)}"

    # --- token lifecycle ------------------------------------------------
    def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str | None,
        redirect_uri: str,
    ) -> OAuthTokens:
        payload: dict[str, Any] = {
            "client_key": self.client_id(),
            "client_secret": self.client_secret(),
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
        if code_verifier:
            payload["code_verifier"] = code_verifier
        return self._post_token(payload, action="exchange the authorization code")

    def refresh(self, *, refresh_token: str) -> OAuthTokens:
        return self._post_token(
            {
                "client_key": self.client_id(),
                "client_secret": self.client_secret(),
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            action="refresh the access token",
        )

    def _post_token(self, payload: dict[str, Any], *, action: str) -> OAuthTokens:
        body = self._json(
            self._request("POST", TOKEN_ENDPOINT, data=payload, form=True),
            action=action,
        )
        access_token = body.get("access_token")
        if not access_token:
            raise OAuthTokenError(
                "TikTok did not return an access token. The authorization may have "
                "been revoked — reconnect the account."
            )
        expires_in = body.get("expires_in")
        scope = body.get("scope") or ""
        return OAuthTokens(
            access_token=str(access_token),
            refresh_token=body.get("refresh_token"),
            token_type=str(body.get("token_type") or "Bearer").title(),
            scopes=[part for part in str(scope).split(",") if part] or list(self.scopes()),
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
                USER_INFO_ENDPOINT,
                params={"fields": "open_id,union_id,avatar_url,display_name,username"},
                headers={"Authorization": f"Bearer {tokens.access_token}"},
            ),
            action="read your TikTok profile",
        )
        user = (body.get("data") or {}).get("user") or {}
        open_id = user.get("open_id")
        if not open_id:
            raise OAuthApiError("TikTok did not return an account id for this authorization.")
        username = user.get("username")
        return [
            AccountProfile(
                platform_account_id=str(open_id),
                display_name=str(user.get("display_name") or username or "TikTok account"),
                username=str(username) if username else None,
                avatar_url=user.get("avatar_url") or None,
            )
        ]

    def revoke(self, tokens: OAuthTokens) -> bool:
        response = self._request(
            "POST",
            REVOKE_ENDPOINT,
            data={
                "client_key": self.client_id(),
                "client_secret": self.client_secret(),
                "token": tokens.access_token,
            },
            form=True,
        )
        return response.status_code < 400

    # --- TikTok's error envelope -----------------------------------------
    def _failure_from_payload(self, response: Any, payload: Any) -> str | None:
        """TikTok wraps *every* response in an ``error`` object.

        ``code == "ok"`` (sometimes ``"success"``) means the call worked, so the
        base class's "an ``error`` key means failure" rule cannot apply here.
        """
        if response.status_code >= 400:
            return super()._failure_from_payload(response, payload)
        if not isinstance(payload, dict):
            return None
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            if code and str(code).lower() not in {"ok", "success"}:
                return str(error.get("message") or code)
            return None
        if isinstance(error, str) and error:
            description = payload.get("error_description") or payload.get("message")
            return f"{error}: {description}" if description else error
        return None


__all__ = ["TIKTOK_SCOPES", "TikTokProvider"]
