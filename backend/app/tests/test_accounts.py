"""Phase 6 integration tests — connected social accounts (the OAuth API).

These drive the real FastAPI app through ``TestClient`` against the migrated
``publisher_test`` database, with each platform's HTTP layer swapped for an
``httpx.MockTransport``. No test reaches a real platform; what is exercised for
real is the *server* side of the connection lifecycle:

* ``state`` is single-use, expiring, platform-bound, and cannot be forged;
* tokens are encrypted at rest — the ciphertext never equals the plaintext, and
  the API never returns a token in any shape;
* the refresh path rotates the stored credential, keeping Google's
  omitted-refresh-token behaviour working;
* disconnect revokes upstream when the platform supports it, yet always removes
  the local vault row and soft-deletes the account;
* role-based access (a viewer may read but not connect) and tenant isolation
  (another workspace's account is ``404``) hold on every route.

The provider-level parsing (URL shapes, per-platform payloads) is covered
separately in ``test_oauth.py``; this module is about the account service and
the HTTP surface on top of it.
"""

from __future__ import annotations

import uuid
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.crypto import EncryptionError, TokenCipher, decrypt_secret
from app.db.enums import SocialAccountStatus, SocialPlatform, WorkspaceMemberStatus
from app.models import OAuthCredential, SocialAccount, User, WorkspaceMember
from app.services import accounts as accounts_service
from app.services import auth as auth_service
from app.services.oauth import get_provider

# --- Endpoint URLs ---------------------------------------------------------
REGISTER = "/api/v1/auth/register"
WORKSPACES = "/api/v1/workspaces"
ACCOUNTS = "/api/v1/accounts"

STRONG_PASSWORD = "correct-horse-battery"

# The plaintext tokens the mock platform hands out. Every one of them must be
# absent from every API response and from the raw database column.
SHORT_TOKEN = "short-lived-token"
LONG_TOKEN = "long-lived-token"
PAGE_TOKEN = "page-one-token"


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def _register(client, email: str) -> tuple[str, uuid.UUID]:
    """Register an account; return its bearer token and personal workspace id."""
    response = client.post(
        REGISTER,
        json={"email": email, "password": STRONG_PASSWORD, "full_name": "Owner"},
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    workspaces = client.get(WORKSPACES, headers=_bearer(token)).json()
    return token, uuid.UUID(workspaces[0]["id"])


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _add_membership(
    db, workspace_id: uuid.UUID, user_id: uuid.UUID, role_name: str
) -> WorkspaceMember:
    member = WorkspaceMember(
        workspace_id=workspace_id,
        user_id=user_id,
        role_id=auth_service.get_role(db, role_name).id,
        status=WorkspaceMemberStatus.ACTIVE,
    )
    db.add(member)
    db.flush()
    return member


# ---------------------------------------------------------------------------
# Platform configuration + mock transport
# ---------------------------------------------------------------------------
def _configure(monkeypatch, platform: SocialPlatform) -> None:
    """Give one platform a client credential pair on this (test) server."""
    values = {
        SocialPlatform.FACEBOOK: ("facebook_client_id", "facebook_client_secret"),
        SocialPlatform.INSTAGRAM: ("instagram_client_id", "instagram_client_secret"),
        SocialPlatform.YOUTUBE: ("youtube_client_id", "youtube_client_secret"),
        SocialPlatform.TIKTOK: ("tiktok_client_key", "tiktok_client_secret"),
    }[platform]
    monkeypatch.setattr(settings, values[0], "test-client-id")
    monkeypatch.setattr(settings, values[1], "test-client-secret")


def _facebook_transport(
    *,
    long_token: str = LONG_TOKEN,
    revoke_status: int = 200,
) -> httpx.MockTransport:
    """A fake Meta Graph API covering the code exchange, Page list and revocation."""
    pages = [
        {
            "id": "page-1",
            "name": "Page One",
            "username": "pageone",
            "access_token": PAGE_TOKEN,
            "picture": {"data": {"url": "http://img/one"}},
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        if path.endswith("/oauth/access_token"):
            if params.get("grant_type") == "fb_exchange_token":
                return httpx.Response(
                    200,
                    json={"access_token": long_token, "token_type": "bearer", "expires_in": 5184000},
                )
            return httpx.Response(200, json={"access_token": SHORT_TOKEN, "token_type": "bearer"})
        if path.endswith("/me/permissions"):
            if request.method == "DELETE":
                return httpx.Response(revoke_status, json={"success": revoke_status < 400})
            return httpx.Response(
                200,
                json={"data": [{"permission": "pages_manage_posts", "status": "granted"}]},
            )
        if path.endswith("/me/accounts"):
            return httpx.Response(200, json={"data": pages})
        return httpx.Response(404, json={"error": {"message": f"unexpected {path}"}})

    return httpx.MockTransport(handler)


def _install_transport(monkeypatch, transport: httpx.MockTransport) -> None:
    monkeypatch.setattr(get_provider(SocialPlatform.FACEBOOK), "transport", transport)


def _start_authorization(client, workspace_id: uuid.UUID, token: str, platform: str = "facebook"):
    """POST the authorize endpoint and return the parsed consent URL params."""
    response = client.post(
        f"{ACCOUNTS}/{workspace_id}/{platform}/authorize", headers=_bearer(token)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return body, dict(parse_qsl(urlsplit(body["authorization_url"]).query))


def _connect_facebook(
    client, monkeypatch, workspace_id: uuid.UUID, token: str, *, revoke_status: int = 200
):
    """Run the full connect flow and return ``(start_body, callback_response, state)``."""
    _configure(monkeypatch, SocialPlatform.FACEBOOK)
    _install_transport(monkeypatch, _facebook_transport(revoke_status=revoke_status))

    start_body, params = _start_authorization(client, workspace_id, token)
    callback = client.get(
        f"{ACCOUNTS}/facebook/callback",
        params={"state": params["state"], "code": "auth-code-123"},
        follow_redirects=False,
    )
    return start_body, callback, params["state"]


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
def test_capabilities_lists_every_supported_platform(api_client, monkeypatch) -> None:
    for field in (
        "facebook_client_id",
        "facebook_client_secret",
        "instagram_client_id",
        "instagram_client_secret",
        "youtube_client_id",
        "youtube_client_secret",
        "tiktok_client_key",
        "tiktok_client_secret",
    ):
        monkeypatch.setattr(settings, field, "")
    token, workspace_id = _register(api_client, "caps@example.com")

    response = api_client.get(f"{ACCOUNTS}/{workspace_id}/capabilities", headers=_bearer(token))

    assert response.status_code == 200
    platforms = {item["platform"]: item for item in response.json()["platforms"]}
    assert set(platforms) == {"instagram", "facebook", "youtube", "tiktok"}
    # A server with no client credentials is honest about it.
    assert all(item["configured"] is False for item in platforms.values())
    assert all(item["connected_count"] == 0 for item in platforms.values())


def test_capabilities_reports_configured_and_counts(api_client, monkeypatch) -> None:
    token, workspace_id = _register(api_client, "caps2@example.com")
    _, callback, _ = _connect_facebook(api_client, monkeypatch, workspace_id, token)
    assert callback.status_code == 302

    platforms = {
        item["platform"]: item
        for item in api_client.get(
            f"{ACCOUNTS}/{workspace_id}/capabilities", headers=_bearer(token)
        ).json()["platforms"]
    }
    assert platforms["facebook"]["configured"] is True
    assert platforms["facebook"]["connected_count"] == 1


# ---------------------------------------------------------------------------
# Authorization start
# ---------------------------------------------------------------------------
def test_authorize_requires_authentication(api_client) -> None:
    token, workspace_id = _register(api_client, "authz-anon@example.com")
    del token

    response = api_client.post(f"{ACCOUNTS}/{workspace_id}/facebook/authorize")

    assert response.status_code == 401


def test_authorize_unconfigured_platform_returns_503(api_client, monkeypatch) -> None:
    token, workspace_id = _register(api_client, "authz-503@example.com")
    monkeypatch.setattr(settings, "youtube_client_id", "")
    monkeypatch.setattr(settings, "youtube_client_secret", "")

    response = api_client.post(
        f"{ACCOUNTS}/{workspace_id}/youtube/authorize", headers=_bearer(token)
    )

    assert response.status_code == 503
    assert "not configured" in response.json()["detail"].lower()


def test_authorize_returns_a_state_bearing_consent_url(api_client, monkeypatch) -> None:
    token, workspace_id = _register(api_client, "authz-url@example.com")
    _configure(monkeypatch, SocialPlatform.FACEBOOK)

    body, params = _start_authorization(api_client, workspace_id, token)

    assert urlsplit(body["authorization_url"]).netloc == "www.facebook.com"
    assert params["client_id"] == "test-client-id"
    assert params["state"]
    assert params["redirect_uri"].endswith("/api/v1/accounts/facebook/callback")
    assert body["state_ttl_seconds"] == settings.oauth_state_ttl_seconds


# ---------------------------------------------------------------------------
# Callback / connection
# ---------------------------------------------------------------------------
def test_callback_creates_an_account_with_encrypted_credentials(
    api_client, db, monkeypatch
) -> None:
    token, workspace_id = _register(api_client, "connect@example.com")
    _, callback, _ = _connect_facebook(api_client, monkeypatch, workspace_id, token)

    assert callback.status_code == 302
    location = callback.headers["location"]
    assert "connected=facebook" in location

    account = db.scalar(
        select(SocialAccount).where(SocialAccount.workspace_id == workspace_id)
    )
    assert account is not None
    assert account.platform_account_id == "page-1"
    assert account.display_name == "Page One"
    assert account.status is SocialAccountStatus.CONNECTED

    credential = db.scalar(
        select(OAuthCredential).where(OAuthCredential.social_account_id == account.id)
    )
    assert credential is not None
    # The stored value is ciphertext, never the token the platform issued.
    assert credential.access_token_encrypted != PAGE_TOKEN
    assert credential.access_token_encrypted.startswith("v1.")
    assert decrypt_secret(credential.access_token_encrypted) == PAGE_TOKEN


def test_connect_response_and_listing_never_expose_tokens(api_client, monkeypatch) -> None:
    token, workspace_id = _register(api_client, "nosecret@example.com")
    _, callback, _ = _connect_facebook(api_client, monkeypatch, workspace_id, token)

    listing = api_client.get(f"{ACCOUNTS}/{workspace_id}", headers=_bearer(token))
    assert listing.status_code == 200

    body = listing.text + callback.headers["location"]
    for secret in (SHORT_TOKEN, LONG_TOKEN, PAGE_TOKEN, "test-client-secret"):
        assert secret not in body
    # The account shape carries expiry *metadata*, never a token field.
    assert "access_token" not in body.lower()


def test_listing_and_get_are_scoped_to_the_workspace(api_client, monkeypatch) -> None:
    token, workspace_id = _register(api_client, "scope@example.com")
    _, callback, _ = _connect_facebook(api_client, monkeypatch, workspace_id, token)
    assert callback.status_code == 302

    listed = api_client.get(f"{ACCOUNTS}/{workspace_id}", headers=_bearer(token)).json()
    assert len(listed) == 1
    account_id = listed[0]["id"]
    assert listed[0]["platform"] == "facebook"
    assert listed[0]["scopes"] == ["pages_manage_posts"]

    # Reading a specific account works...
    got = api_client.get(f"{ACCOUNTS}/{workspace_id}/{account_id}", headers=_bearer(token))
    assert got.status_code == 200
    assert got.json()["id"] == account_id

    # ...but another workspace's id is a 404, never a 403.
    other_token, other_workspace = _register(api_client, "other@example.com")
    del other_token
    missing = api_client.get(
        f"{ACCOUNTS}/{other_workspace}/{account_id}", headers=_bearer(token)
    )
    assert missing.status_code == 404


def test_callback_rejects_an_unknown_state(api_client) -> None:
    response = api_client.get(
        f"{ACCOUNTS}/facebook/callback",
        params={"state": "not-a-real-state", "code": "c"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = urlsplit(response.headers["location"])
    assert "error" in dict(parse_qsl(location.query))


def test_callback_state_is_single_use(api_client, monkeypatch) -> None:
    token, workspace_id = _register(api_client, "replay@example.com")
    _configure(monkeypatch, SocialPlatform.FACEBOOK)
    _install_transport(monkeypatch, _facebook_transport())

    _, params = _start_authorization(api_client, workspace_id, token)
    first = api_client.get(
        f"{ACCOUNTS}/facebook/callback",
        params={"state": params["state"], "code": "c"},
        follow_redirects=False,
    )
    assert "connected=facebook" in first.headers["location"]

    # Replaying the same code+state must not connect a second account.
    second = api_client.get(
        f"{ACCOUNTS}/facebook/callback",
        params={"state": params["state"], "code": "c"},
        follow_redirects=False,
    )
    query = dict(parse_qsl(urlsplit(second.headers["location"]).query))
    assert "error" in query
    assert "already been used" in query["error"].lower()


def test_state_started_for_one_platform_is_rejected_on_another(api_client, monkeypatch) -> None:
    token, workspace_id = _register(api_client, "crossplat@example.com")
    _configure(monkeypatch, SocialPlatform.FACEBOOK)
    _configure(monkeypatch, SocialPlatform.YOUTUBE)

    _, params = _start_authorization(api_client, workspace_id, token, platform="facebook")
    response = api_client.get(
        f"{ACCOUNTS}/youtube/callback",
        params={"state": params["state"], "code": "c"},
        follow_redirects=False,
    )

    query = dict(parse_qsl(urlsplit(response.headers["location"]).query))
    assert "different platform" in query["error"].lower()


def test_callback_reports_a_platform_refusal(api_client) -> None:
    response = api_client.get(
        f"{ACCOUNTS}/facebook/callback",
        params={"state": "s", "error": "access_denied", "error_description": "User denied"},
        follow_redirects=False,
    )

    query = dict(parse_qsl(urlsplit(response.headers["location"]).query))
    assert "declined" in query["error"].lower()


# ---------------------------------------------------------------------------
# Role-based access
# ---------------------------------------------------------------------------
def test_viewer_can_read_but_not_connect(api_client, db, monkeypatch) -> None:
    owner_token, workspace_id = _register(api_client, "owner-rbac@example.com")
    _, callback, _ = _connect_facebook(api_client, monkeypatch, workspace_id, owner_token)
    assert callback.status_code == 302

    viewer_token, viewer_workspace = _register(api_client, "viewer@example.com")
    viewer = db.scalar(select(User).where(User.email == "viewer@example.com"))
    _add_membership(db, workspace_id, viewer.id, "viewer")

    # account:read is granted to a viewer, so listing succeeds.
    assert (
        api_client.get(f"{ACCOUNTS}/{workspace_id}", headers=_bearer(viewer_token)).status_code
        == 200
    )
    # account:connect is not, so starting an authorization is refused.
    denied = api_client.post(
        f"{ACCOUNTS}/{workspace_id}/facebook/authorize", headers=_bearer(viewer_token)
    )
    assert denied.status_code == 403
    assert viewer_workspace != workspace_id


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------
def test_refresh_rotates_the_stored_credential(api_client, db, monkeypatch) -> None:
    token, workspace_id = _register(api_client, "refresh@example.com")
    _, callback, _ = _connect_facebook(api_client, monkeypatch, workspace_id, token)
    account_id = api_client.get(
        f"{ACCOUNTS}/{workspace_id}", headers=_bearer(token)
    ).json()[0]["id"]

    # The next long-lived exchange returns a different token.
    _install_transport(monkeypatch, _facebook_transport(long_token="rotated-token"))
    response = api_client.post(
        f"{ACCOUNTS}/{workspace_id}/{account_id}/refresh", headers=_bearer(token)
    )
    assert response.status_code == 200, response.text

    credential = db.scalar(
        select(OAuthCredential).where(OAuthCredential.social_account_id == uuid.UUID(account_id))
    )
    assert decrypt_secret(credential.access_token_encrypted) == "rotated-token"


def test_refresh_without_a_stored_credential_needs_reconnect(
    api_client, db, monkeypatch
) -> None:
    token, workspace_id = _register(api_client, "refresh-fail@example.com")
    _, callback, _ = _connect_facebook(api_client, monkeypatch, workspace_id, token)
    account_id = api_client.get(
        f"{ACCOUNTS}/{workspace_id}", headers=_bearer(token)
    ).json()[0]["id"]

    # Simulate a vault row that was lost: refresh must answer 409, not 500.
    credential = db.scalar(
        select(OAuthCredential).where(OAuthCredential.social_account_id == uuid.UUID(account_id))
    )
    db.delete(credential)
    db.flush()

    response = api_client.post(
        f"{ACCOUNTS}/{workspace_id}/{account_id}/refresh", headers=_bearer(token)
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------
def test_disconnect_revokes_upstream_and_destroys_the_vault(
    api_client, db, monkeypatch
) -> None:
    token, workspace_id = _register(api_client, "disconnect@example.com")
    _, callback, _ = _connect_facebook(api_client, monkeypatch, workspace_id, token)
    account_id = api_client.get(
        f"{ACCOUNTS}/{workspace_id}", headers=_bearer(token)
    ).json()[0]["id"]

    response = api_client.delete(
        f"{ACCOUNTS}/{workspace_id}/{account_id}", headers=_bearer(token)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["revoked_upstream"] is True

    # The secret is gone and the account no longer lists.
    assert (
        db.scalar(
            select(OAuthCredential).where(
                OAuthCredential.social_account_id == uuid.UUID(account_id)
            )
        )
        is None
    )
    account = db.get(SocialAccount, uuid.UUID(account_id))
    assert account.deleted_at is not None
    assert account.status is SocialAccountStatus.REVOKED

    assert api_client.get(f"{ACCOUNTS}/{workspace_id}", headers=_bearer(token)).json() == []
    assert (
        api_client.get(f"{ACCOUNTS}/{workspace_id}/{account_id}", headers=_bearer(token)).status_code
        == 404
    )


def test_disconnect_still_removes_locally_when_revocation_fails(
    api_client, db, monkeypatch
) -> None:
    token, workspace_id = _register(api_client, "disconnect-fail@example.com")
    _, callback, _ = _connect_facebook(
        api_client, monkeypatch, workspace_id, token, revoke_status=500
    )
    account_id = api_client.get(
        f"{ACCOUNTS}/{workspace_id}", headers=_bearer(token)
    ).json()[0]["id"]

    response = api_client.delete(
        f"{ACCOUNTS}/{workspace_id}/{account_id}", headers=_bearer(token)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["revoked_upstream"] is False
    assert "disconnected" in body["message"].lower()
    assert api_client.get(f"{ACCOUNTS}/{workspace_id}", headers=_bearer(token)).json() == []


# ---------------------------------------------------------------------------
# Service-level: expired tokens and encryption integrity
# ---------------------------------------------------------------------------
def test_expired_token_without_a_refresh_token_flags_reconnect(db) -> None:
    from datetime import timedelta

    from app.core.crypto import encrypt_secret
    from app.core.security import utcnow

    user = User(email="expired@example.com", hashed_password="x", is_active=True)
    db.add(user)
    db.flush()
    # Reuse the personal workspace the registration flow would create.
    from app.models import Workspace

    workspace = Workspace(name="Expired WS", slug="expired-ws", owner_id=user.id)
    db.add(workspace)
    db.flush()

    account = SocialAccount(
        workspace_id=workspace.id,
        platform=SocialPlatform.FACEBOOK,
        platform_account_id="dead-page",
        display_name="Dead Page",
    )
    db.add(account)
    db.flush()
    db.add(
        OAuthCredential(
            social_account_id=account.id,
            access_token_encrypted=encrypt_secret("dead-token"),
            refresh_token_encrypted=None,
            expires_at=utcnow() - timedelta(hours=1),
        )
    )
    db.flush()
    db.refresh(account)

    with pytest.raises(accounts_service.AccountNeedsReconnectError):
        accounts_service.ensure_fresh_tokens(db, account)
    assert account.status is SocialAccountStatus.EXPIRED


def test_token_cipher_round_trip_and_tamper_detection() -> None:
    cipher = TokenCipher()
    ciphertext = cipher.encrypt("a-very-secret-value")

    assert ciphertext.startswith("v1.")
    assert ciphertext != "a-very-secret-value"
    assert cipher.decrypt(ciphertext) == "a-very-secret-value"

    # A single flipped character must fail authentication, not decode to garbage.
    index = len(ciphertext) // 2
    flipped = "A" if ciphertext[index] != "A" else "B"
    tampered = ciphertext[:index] + flipped + ciphertext[index + 1:]
    with pytest.raises(EncryptionError):
        cipher.decrypt(tampered)


def test_token_cipher_rejects_a_value_from_a_different_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "oauth_encryption_key", "key-one")
    encrypted = TokenCipher().encrypt("secret")

    monkeypatch.setattr(settings, "oauth_encryption_key", "key-two")
    with pytest.raises(EncryptionError):
        TokenCipher().decrypt(encrypted)
