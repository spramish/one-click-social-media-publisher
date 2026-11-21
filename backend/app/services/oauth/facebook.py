"""Facebook Page connection provider (Meta Graph API).

A Facebook *Page* is the publishing destination — personal profiles cannot
publish video through the API — so one authorization can yield several
connected accounts, one per Page the user manages.

Required app configuration (see ``docs/OAUTH_SETUP.md``):

* A Meta app of type **Business**, with **Facebook Login** added.
* The **Valid OAuth Redirect URI** must equal
  ``{BACKEND_PUBLIC_URL}/api/v1/accounts/facebook/callback`` exactly.
* Permissions ``pages_show_list``, ``pages_read_engagement`` and
  ``pages_manage_posts`` require **App Review** before the app can serve users
  outside its own admins/developers/testers; ``business_management`` requires a
  verified business.
* The connecting user must hold a **role on the Page** (admin/editor) and the
  Page must have no restriction that blocks API publishing.
"""

from __future__ import annotations

from app.core.config import settings
from app.db.enums import SocialPlatform
from app.services.oauth.base import AccountProfile, OAuthTokens
from app.services.oauth.meta import MetaOAuthProvider

#: Everything the publisher needs to read a Page and post video to it.
FACEBOOK_SCOPES: tuple[str, ...] = (
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "business_management",
    "public_profile",
)


class FacebookProvider(MetaOAuthProvider):
    """Connects Facebook Pages."""

    platform = SocialPlatform.FACEBOOK
    display_name = "Facebook"

    def client_id(self) -> str:
        # A single Meta app usually serves both Facebook and Instagram; the
        # Facebook values win, and the Instagram pair is the documented fallback.
        return settings.facebook_client_id or settings.instagram_client_id

    def client_secret(self) -> str:
        return settings.facebook_client_secret or settings.instagram_client_secret

    def scopes(self) -> tuple[str, ...]:
        return FACEBOOK_SCOPES

    def fetch_profiles(self, tokens: OAuthTokens) -> list[AccountProfile]:
        """One profile per Page the user administers."""
        scopes = self._safe_granted_scopes(tokens)
        nodes = self._accounts(
            tokens, "id,name,username,picture{url},access_token"
        )
        return [
            AccountProfile(
                platform_account_id=str(node["id"]),
                display_name=str(node.get("name") or "Facebook Page"),
                username=str(node["username"]) if node.get("username") else None,
                avatar_url=self._page_avatar(node),
                tokens=self._page_tokens(node, fallback=tokens, scopes=scopes),
            )
            for node in nodes
            if node.get("id")
        ]

    def _safe_granted_scopes(self, tokens: OAuthTokens) -> list[str]:
        """Read granted scopes, falling back to the requested set.

        ``/me/permissions`` can fail for an app that has not been reviewed yet;
        that must not stop a connection the platform otherwise allowed.
        """
        try:
            granted = self.granted_scopes(tokens)
        except Exception:  # noqa: BLE001 - deliberately non-fatal
            return list(self.scopes())
        return granted or list(self.scopes())


__all__ = ["FACEBOOK_SCOPES", "FacebookProvider"]
