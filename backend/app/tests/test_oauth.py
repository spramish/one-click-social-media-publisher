"""Provider-level OAuth tests, driven entirely against ``httpx.MockTransport``.

No test in this module touches a real platform. Each provider's HTTP layer is
injectable (``OAuthProvider.transport``), so a fake transport stands in for
Facebook, Google and TikTok and the *parsing* logic — which is where the
platform differences actually live — is exercised for real.

The master prompt requires "mocked platform APIs for local testing"; this is
that requirement, applied at the provider boundary.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest

from app.core.config import settings
from app.db.enums import SocialPlatform
from app.services.oauth import (
    OAuthApiError,
    OAuthNotConfiguredError,
    OAuthTokens,
    code_challenge_for,
    generate_state,
    get_provider,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _params(url: str) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(url).query))


def _configure(monkeypatch, platform: SocialPlatform) -> None:
    """Give one platform a client credential pair."""
    values = {
        SocialPlatform.FACEBOOK: ("facebook_client_id", "facebook_client_secret"),
        SocialPlatform.INSTAGRAM: ("instagram_client_id", "instagram_client_secret"),
        SocialPlatform.YOUTUBE: ("youtube_client_id", "youtube_client_secret"),
        SocialPlatform.TIKTOK: ("tiktok_client_key", "tiktok_client_secret"),
    }[platform]
    monkeypatch.setattr(settings, values[0], "test-client-id")
    monkeypatch.setattr(settings, values[1], "test-client-secret")


def _transport(monkeypatch, platform: SocialPlatform, handler) -> None:
    monkeypatch.setattr(
        get_provider(platform), "transport", httpx.MockTransport(handler)
    )


def _user_token() -> OAuthTokens:
    return OAuthTokens(access_token="user-token")


# ---------------------------------------------------------------------------
# PKCE + state primitives
# ---------------------------------------------------------------------------
def test_pkce_challenge_matches_the_rfc7636_vector() -> None:
    """The canonical S256 example from RFC 7636 appendix B."""
    assert (
        code_challenge_for("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_generate_state_is_unique_and_high_entropy() -> None:
    states = {generate_state() for _ in range(100)}
    assert len(states) == 100
    # 32 random bytes → at least 43 characters of url-safe base64.
    assert all(len(state) >= 43 for state in states)


# ---------------------------------------------------------------------------
# Configuration gap
# ---------------------------------------------------------------------------
def test_unconfigured_provider_reports_the_gap(monkeypatch) -> None:
    monkeypatch.setattr(settings, "youtube_client_id", "")
    monkeypatch.setattr(settings, "youtube_client_secret", "")
    provider = get_provider(SocialPlatform.YOUTUBE)

    assert provider.is_configured() is False
    with pytest.raises(OAuthNotConfiguredError):
        provider.authorization_url(state="abc")


# ---------------------------------------------------------------------------
# Authorization URL shapes
# ---------------------------------------------------------------------------
def test_facebook_authorization_url_shape(monkeypatch) -> None:
    _configure(monkeypatch, SocialPlatform.FACEBOOK)
    provider = get_provider(SocialPlatform.FACEBOOK)

    parsed = urlsplit(provider.authorization_url(state="state-123"))
    params = _params(provider.authorization_url(state="state-123"))

    assert parsed.netloc == "www.facebook.com"
    assert "/dialog/oauth" in parsed.path
    assert params["client_id"] == "test-client-id"
    assert params["state"] == "state-123"
    assert params["response_type"] == "code"
    assert params["redirect_uri"] == provider.callback_url()
    assert params["scope"] == " ".join(provider.scopes())
    # Meta uses Facebook Login, which does not take a PKCE challenge.
    assert "code_challenge" not in params


def test_youtube_authorization_url_requests_offline_access_and_pkce(monkeypatch) -> None:
    _configure(monkeypatch, SocialPlatform.YOUTUBE)
    provider = get_provider(SocialPlatform.YOUTUBE)

    params = _params(provider.authorization_url(state="s", code_challenge="chal"))

    assert params["code_challenge"] == "chal"
    assert params["code_challenge_method"] == "S256"
    assert params["access_type"] == "offline"
    assert params["prompt"] == "consent"


def test_tiktok_authorization_url_uses_comma_separated_scopes(monkeypatch) -> None:
    _configure(monkeypatch, SocialPlatform.TIKTOK)
    provider = get_provider(SocialPlatform.TIKTOK)

    params = _params(provider.authorization_url(state="s", code_challenge="chal"))

    # TikTok joins scopes with commas and calls the client id ``client_key``.
    assert params["client_key"] == "test-client-id"
    assert params["scope"] == ",".join(provider.scopes())
    assert params["code_challenge_method"] == "S256"


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------
def test_youtube_exchange_posts_form_encoded_and_parses_tokens(monkeypatch) -> None:
    _configure(monkeypatch, SocialPlatform.YOUTUBE)
    provider = get_provider(SocialPlatform.YOUTUBE)
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["content_type"] = request.headers.get("content-type", "")
        seen["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "access_token": "at",
                "refresh_token": "rt",
                "expires_in": 3600,
                "scope": "https://www.googleapis.com/auth/youtube.upload",
            },
        )

    _transport(monkeypatch, SocialPlatform.YOUTUBE, handler)
    tokens = provider.exchange_code(code="code", code_verifier="verifier", redirect_uri="http://cb")

    assert tokens.access_token == "at"
    assert tokens.refresh_token == "rt"
    assert tokens.expires_at is not None
    assert seen["method"] == "POST"
    assert seen["content_type"].startswith("application/x-www-form-urlencoded")
    assert "code_verifier=verifier" in seen["body"]


def test_youtube_refresh_keeps_no_refresh_token_when_google_omits_it(monkeypatch) -> None:
    _configure(monkeypatch, SocialPlatform.YOUTUBE)
    provider = get_provider(SocialPlatform.YOUTUBE)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})

    _transport(monkeypatch, SocialPlatform.YOUTUBE, handler)
    tokens = provider.refresh(refresh_token="stored-refresh")

    assert tokens.access_token == "fresh"
    # ``None`` signals the account service to preserve the stored refresh token.
    assert tokens.refresh_token is None


def test_meta_long_lived_token_fills_both_slots(monkeypatch) -> None:
    """Meta has no refresh token, so the long-lived token doubles as one."""
    _configure(monkeypatch, SocialPlatform.FACEBOOK)
    provider = get_provider(SocialPlatform.FACEBOOK)

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if params.get("grant_type") == "fb_exchange_token":
            return httpx.Response(
                200, json={"access_token": "long", "token_type": "bearer", "expires_in": 5184000}
            )
        return httpx.Response(200, json={"access_token": "short", "token_type": "bearer"})

    _transport(monkeypatch, SocialPlatform.FACEBOOK, handler)
    tokens = provider.exchange_code(code="c", code_verifier=None, redirect_uri="http://cb")

    assert tokens.access_token == "long"
    assert tokens.refresh_token == "long"


def test_tiktok_treats_the_ok_envelope_as_success(monkeypatch) -> None:
    _configure(monkeypatch, SocialPlatform.TIKTOK)
    provider = get_provider(SocialPlatform.TIKTOK)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "at",
                "refresh_token": "rt",
                "expires_in": 86400,
                "scope": "user.info.basic,video.publish",
                # TikTok wraps even a success in an ``error`` object.
                "error": {"code": "ok", "message": "", "log_id": "log"},
            },
        )

    _transport(monkeypatch, SocialPlatform.TIKTOK, handler)
    tokens = provider.exchange_code(code="c", code_verifier="v", redirect_uri="cb")

    assert tokens.access_token == "at"
    assert tokens.scopes == ["user.info.basic", "video.publish"]


def test_tiktok_rejects_a_non_ok_envelope(monkeypatch) -> None:
    _configure(monkeypatch, SocialPlatform.TIKTOK)
    provider = get_provider(SocialPlatform.TIKTOK)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"error": {"code": "invalid_grant", "message": "bad code"}}
        )

    _transport(monkeypatch, SocialPlatform.TIKTOK, handler)
    with pytest.raises(OAuthApiError, match="bad code"):
        provider.exchange_code(code="c", code_verifier="v", redirect_uri="cb")


def test_a_transport_fault_becomes_an_oauth_api_error(monkeypatch) -> None:
    _configure(monkeypatch, SocialPlatform.YOUTUBE)
    provider = get_provider(SocialPlatform.YOUTUBE)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    _transport(monkeypatch, SocialPlatform.YOUTUBE, handler)
    with pytest.raises(OAuthApiError):
        provider.refresh(refresh_token="rt")


# ---------------------------------------------------------------------------
# Identity reads
# ---------------------------------------------------------------------------
def test_facebook_reads_one_profile_per_page(monkeypatch) -> None:
    _configure(monkeypatch, SocialPlatform.FACEBOOK)
    provider = get_provider(SocialPlatform.FACEBOOK)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me/permissions"):
            return httpx.Response(
                200, json={"data": [{"permission": "pages_manage_posts", "status": "granted"}]}
            )
        if request.url.path.endswith("/me/accounts"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "p1",
                            "name": "Page One",
                            "username": "pageone",
                            "access_token": "page-one-token",
                            "picture": {"data": {"url": "http://img/one"}},
                        },
                        {"id": "p2", "name": "Page Two"},
                    ]
                },
            )
        return httpx.Response(404, json={"error": {"message": "unexpected"}})

    _transport(monkeypatch, SocialPlatform.FACEBOOK, handler)
    profiles = provider.fetch_profiles(_user_token())

    assert [profile.platform_account_id for profile in profiles] == ["p1", "p2"]
    # A Page carries its own token, which is what authorizes publishing.
    assert profiles[0].tokens is not None
    assert profiles[0].tokens.access_token == "page-one-token"
    assert profiles[0].tokens.scopes == ["pages_manage_posts"]
    # A Page without its own token falls back to the grant token.
    assert profiles[1].tokens is not None
    assert profiles[1].tokens.access_token == "user-token"


def test_instagram_skips_pages_without_a_professional_account(monkeypatch) -> None:
    _configure(monkeypatch, SocialPlatform.INSTAGRAM)
    provider = get_provider(SocialPlatform.INSTAGRAM)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me/permissions"):
            return httpx.Response(200, json={"data": []})
        if request.url.path.endswith("/me/accounts"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "p1",
                            "name": "Has IG",
                            "access_token": "page-token",
                            "instagram_business_account": {
                                "id": "ig-1",
                                "username": "has_ig",
                                "name": "Has IG",
                                "profile_picture_url": "http://img/ig",
                            },
                        },
                        {"id": "p2", "name": "No IG"},
                    ]
                },
            )
        return httpx.Response(404, json={"error": {"message": "unexpected"}})

    _transport(monkeypatch, SocialPlatform.INSTAGRAM, handler)
    profiles = provider.fetch_profiles(_user_token())

    assert [profile.platform_account_id for profile in profiles] == ["ig-1"]
    assert profiles[0].username == "has_ig"


def test_youtube_reports_a_helpful_error_when_the_account_has_no_channel(monkeypatch) -> None:
    _configure(monkeypatch, SocialPlatform.YOUTUBE)
    provider = get_provider(SocialPlatform.YOUTUBE)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    _transport(monkeypatch, SocialPlatform.YOUTUBE, handler)
    with pytest.raises(OAuthApiError, match="no YouTube channel"):
        provider.fetch_profiles(OAuthTokens(access_token="at"))


def test_tiktok_reads_the_account_identity(monkeypatch) -> None:
    _configure(monkeypatch, SocialPlatform.TIKTOK)
    provider = get_provider(SocialPlatform.TIKTOK)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "user": {
                        "open_id": "oid",
                        "display_name": "Tik Toker",
                        "username": "tiktoker",
                        "avatar_url": "http://img/tik",
                    }
                },
                "error": {"code": "ok"},
            },
        )

    _transport(monkeypatch, SocialPlatform.TIKTOK, handler)
    profiles = provider.fetch_profiles(OAuthTokens(access_token="at"))

    assert len(profiles) == 1
    assert profiles[0].platform_account_id == "oid"
    assert profiles[0].display_name == "Tik Toker"


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------
def test_facebook_revocation_reports_success(monkeypatch) -> None:
    _configure(monkeypatch, SocialPlatform.FACEBOOK)
    provider = get_provider(SocialPlatform.FACEBOOK)
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        return httpx.Response(200, json={"success": True})

    _transport(monkeypatch, SocialPlatform.FACEBOOK, handler)

    assert provider.revoke(_user_token()) is True
    assert seen["method"] == "DELETE"


def test_youtube_revocation_treats_an_already_invalid_token_as_done(monkeypatch) -> None:
    _configure(monkeypatch, SocialPlatform.YOUTUBE)
    provider = get_provider(SocialPlatform.YOUTUBE)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_token"})

    _transport(monkeypatch, SocialPlatform.YOUTUBE, handler)

    # Revoking an already-dead token reaches the desired end state.
    assert provider.revoke(OAuthTokens(access_token="at", refresh_token="rt")) is True


def test_tiktok_revocation_reports_success(monkeypatch) -> None:
    _configure(monkeypatch, SocialPlatform.TIKTOK)
    provider = get_provider(SocialPlatform.TIKTOK)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"code": "ok"}})

    _transport(monkeypatch, SocialPlatform.TIKTOK, handler)

    assert provider.revoke(_user_token()) is True
