"""Sandbox (``DEMO_MODE``) behaviour.

The sandbox exists so the connect → publish → analytics journey works with no
developer credentials. These tests pin the three things that matter:

* a sandbox provider reports itself configured, so the UI enables Connect;
* the full connect flow still creates a real account through the ordinary
  service code (state validation included), with a simulated identity;
* the flag is refused in production, so it can never be left on by accident.
"""

from __future__ import annotations

import uuid
from urllib.parse import parse_qsl, urlsplit

import pytest
from pydantic import ValidationError

from app.core.config import INSECURE_SECRET_KEY, Settings, settings
from app.db.enums import SocialPlatform
from app.services.metrics import get_metrics_provider
from app.services.oauth import get_provider
from app.services.publishers import get_publisher
from app.services.publishers.base import MediaAsset, PublishRequest

REGISTER = "/api/v1/auth/register"
WORKSPACES = "/api/v1/workspaces"
ACCOUNTS = "/api/v1/accounts"
STRONG_PASSWORD = "correct-horse-battery"


def _register(client, email: str) -> tuple[str, uuid.UUID]:
    response = client.post(
        REGISTER,
        json={"email": email, "password": STRONG_PASSWORD, "full_name": "Owner"},
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    workspaces = client.get(WORKSPACES, headers={"Authorization": f"Bearer {token}"}).json()
    return token, uuid.UUID(workspaces[0]["id"])


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
def test_sandbox_provider_is_configured_without_credentials(monkeypatch) -> None:
    for field in (
        "facebook_client_id",
        "facebook_client_secret",
        "tiktok_client_key",
        "tiktok_client_secret",
    ):
        monkeypatch.setattr(settings, field, "")
    monkeypatch.setattr(settings, "demo_mode", True)

    assert get_provider(SocialPlatform.FACEBOOK).is_configured() is True
    assert get_provider(SocialPlatform.TIKTOK).is_configured() is True
    # The real provider behind the same platform is unchanged.
    monkeypatch.setattr(settings, "demo_mode", False)
    assert get_provider(SocialPlatform.FACEBOOK).is_configured() is False


def test_sandbox_authorization_url_targets_this_server(monkeypatch) -> None:
    monkeypatch.setattr(settings, "demo_mode", True)
    url = get_provider(SocialPlatform.YOUTUBE).authorization_url(state="state-123")

    parts = urlsplit(url)
    params = dict(parse_qsl(parts.query))
    assert parts.path.endswith("/api/v1/accounts/youtube/callback")
    assert params["state"] == "state-123"
    assert params["code"].startswith("sandbox-")


# ---------------------------------------------------------------------------
# The user-panel journey
# ---------------------------------------------------------------------------
def test_capabilities_report_configured_in_demo_mode(api_client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "demo_mode", True)
    token, workspace_id = _register(api_client, "sandbox-caps@example.com")

    response = api_client.get(f"{ACCOUNTS}/{workspace_id}/capabilities", headers=_bearer(token))

    assert response.status_code == 200
    platforms = response.json()["platforms"]
    assert len(platforms) == 4
    assert all(item["configured"] is True for item in platforms)


def test_connect_creates_an_account_in_demo_mode(api_client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "demo_mode", True)
    token, workspace_id = _register(api_client, "sandbox-connect@example.com")

    start = api_client.post(
        f"{ACCOUNTS}/{workspace_id}/facebook/authorize", headers=_bearer(token)
    )
    assert start.status_code == 200, start.text

    # Follow the consent URL the sandbox handed back — it points at our callback.
    consent = urlsplit(start.json()["authorization_url"])
    callback = api_client.get(
        f"{consent.path}?{consent.query}", follow_redirects=False
    )
    assert callback.status_code == 302
    assert "connected=facebook" in callback.headers["location"]

    listing = api_client.get(f"{ACCOUNTS}/{workspace_id}", headers=_bearer(token)).json()
    assert len(listing) == 1
    assert listing[0]["platform"] == "facebook"
    assert listing[0]["display_name"].endswith("(sandbox)")
    # No token material is ever exposed by the API, sandbox or not.
    assert "access_token" not in listing[0]


# ---------------------------------------------------------------------------
# Publishing + analytics
# ---------------------------------------------------------------------------
def test_sandbox_publish_returns_a_terminal_result(monkeypatch) -> None:
    monkeypatch.setattr(settings, "demo_mode", True)
    asset = MediaAsset(
        media_id=uuid.uuid4(),
        path=None,
        size_bytes=1024 * 1024,
        mime_type="video/mp4",
        duration_seconds=12.0,
        width=1080,
        height=1920,
        public_url="http://localhost:8000/media/signed/clip.mp4",
    )
    result = get_publisher(SocialPlatform.FACEBOOK).publish(
        PublishRequest(
            asset=asset,
            caption="Hello from the sandbox",
            title=None,
            access_token="sandbox-access",
            platform_account_id="sandbox-facebook-1",
        )
    )
    assert result.status == "published"
    assert result.remote_post_id.startswith("sandbox-")


def test_sandbox_metrics_are_stable_across_reads(monkeypatch) -> None:
    monkeypatch.setattr(settings, "demo_mode", True)
    provider = get_metrics_provider(SocialPlatform.INSTAGRAM)

    first = provider.fetch_post_metrics(
        access_token="sandbox-access",
        remote_post_id="sandbox-post-1",
        platform_account_id="sandbox-ig-1",
    )
    second = provider.fetch_post_metrics(
        access_token="sandbox-access",
        remote_post_id="sandbox-post-1",
        platform_account_id="sandbox-ig-1",
    )
    assert first.views == second.views
    assert first.views is not None and first.views > 0
    # Internally consistent funnel: impressions >= reach >= views.
    assert first.impressions >= first.reach >= first.views


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------
def test_demo_mode_is_refused_in_production() -> None:
    with pytest.raises(ValidationError, match="DEMO_MODE"):
        Settings(
            environment="production",
            secret_key="x" * 40,
            refresh_cookie_secure=True,
            demo_mode=True,
        )


def test_production_defaults_still_include_the_placeholder_secret() -> None:
    # Guards that the sandbox work did not disturb the existing production rules.
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(environment="production", secret_key=INSECURE_SECRET_KEY)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
