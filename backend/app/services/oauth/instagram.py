"""Instagram connection provider (Instagram Graph API via Facebook Login).

Instagram does **not** have a separate OAuth dialog for professional accounts:
the user authorizes the Facebook app, and the Instagram professional account is
read from the linked Page. Authorization therefore returns the Instagram
Business/Creator accounts attached to the Pages the user manages — a personal
Instagram account has no API publishing access at all.

Required app configuration (see ``docs/OAUTH_SETUP.md``):

* The same Meta app as the Facebook provider (or its own pair of credentials).
* ``instagram_basic`` and ``instagram_content_publish`` require **App Review**,
  and ``instagram_manage_insights`` additionally requires the account to be a
  **Business** (not Creator) account for full metrics.
* The Instagram account must be **professional** and **linked to a Facebook
  Page** the connecting user administers.
* Publishing requires a public video URL — Instagram pulls the media itself.
"""

from __future__ import annotations

from app.core.config import settings
from app.db.enums import SocialPlatform
from app.services.oauth.base import AccountProfile, OAuthTokens
from app.services.oauth.meta import MetaOAuthProvider

#: Read the account plus publish Reels and read insights.
INSTAGRAM_SCOPES: tuple[str, ...] = (
    "instagram_basic",
    "instagram_content_publish",
    "instagram_manage_insights",
    "pages_show_list",
    "pages_read_engagement",
    "business_management",
)


class InstagramProvider(MetaOAuthProvider):
    """Connects Instagram professional accounts."""

    platform = SocialPlatform.INSTAGRAM
    display_name = "Instagram"

    def client_id(self) -> str:
        return settings.instagram_client_id or settings.facebook_client_id

    def client_secret(self) -> str:
        return settings.instagram_client_secret or settings.facebook_client_secret

    def scopes(self) -> tuple[str, ...]:
        return INSTAGRAM_SCOPES

    def fetch_profiles(self, tokens: OAuthTokens) -> list[AccountProfile]:
        """One profile per Instagram professional account linked to a Page."""
        scopes = self._granted_or_requested(tokens)
        nodes = self._accounts(
            tokens,
            "id,name,access_token,"
            "instagram_business_account{id,username,name,profile_picture_url}",
        )
        profiles: list[AccountProfile] = []
        for node in nodes:
            account = node.get("instagram_business_account")
            if not isinstance(account, dict) or not account.get("id"):
                # A Page without a linked professional account is simply not an
                # Instagram destination; it is skipped rather than failing the
                # whole connection.
                continue
            profiles.append(
                AccountProfile(
                    platform_account_id=str(account["id"]),
                    display_name=str(
                        account.get("name") or account.get("username") or "Instagram account"
                    ),
                    username=str(account["username"]) if account.get("username") else None,
                    avatar_url=account.get("profile_picture_url") or None,
                    tokens=self._page_tokens(node, fallback=tokens, scopes=scopes),
                )
            )
        return profiles

    def _granted_or_requested(self, tokens: OAuthTokens) -> list[str]:
        try:
            granted = self.granted_scopes(tokens)
        except Exception:  # noqa: BLE001 - deliberately non-fatal
            return list(self.scopes())
        return granted or list(self.scopes())


__all__ = ["INSTAGRAM_SCOPES", "InstagramProvider"]
