"""Phase 13 integration tests — rate limiting, security headers, CSRF, audit.

These exercise the guarantees the phase adds on top of the earlier ones:

* **Rate limiting** — a caller that exceeds a bucket gets ``429`` with a
  ``Retry-After``; the window is per caller and per bucket, and the counter
  degrades to an in-process store rather than failing open.
* **Security headers** — every JSON response carries ``nosniff``, a clickjacking
  and referrer policy, and a deny-by-default CSP (the docs pages are exempt).
* **CSRF** — a state-changing request that presents a cross-origin ``Origin`` is
  refused with ``403``, while same-origin, origin-less and safe requests pass.
* **Audit** — sign-in events are written next to their transaction, and the
  trail is readable only with ``audit:read`` and only inside the workspace it
  belongs to.

Rate limiting is disabled suite-wide by ``conftest.py`` so ordinary tests can
authenticate freely; the tests here turn it back on for one call.
"""

from __future__ import annotations

import types
import uuid

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.enums import WorkspaceMemberStatus
from app.models import User, WorkspaceMember
from app.models.audit import AuditLog
from app.services import audit as audit_service
from app.services import auth as auth_service

# --- Endpoint URLs ---------------------------------------------------------
REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
WORKSPACES = "/api/v1/workspaces"
HEALTH = "/api/v1/health"

STRONG_PASSWORD = "correct-horse-battery"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _register(client, email: str, *, password: str = STRONG_PASSWORD):
    response = client.post(
        REGISTER,
        json={"email": email, "password": password, "full_name": "Test User"},
    )
    assert response.status_code == 201, response.text
    return response


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _workspace_id(client, token: str) -> str:
    response = client.get(WORKSPACES, headers=_bearer(token))
    assert response.status_code == 200, response.text
    return response.json()[0]["id"]


def _add_membership(db, workspace_id: str, user_id: uuid.UUID, role_name: str) -> WorkspaceMember:
    member = WorkspaceMember(
        workspace_id=uuid.UUID(workspace_id),
        user_id=user_id,
        role_id=auth_service.get_role(db, role_name).id,
        status=WorkspaceMemberStatus.ACTIVE,
    )
    db.add(member)
    db.flush()
    return member


class _FakeRequest:
    """The minimum ``client_identity`` reads: headers and a socket peer."""

    def __init__(self, *, ip: str = "1.2.3.4", forwarded: str | None = None) -> None:
        self.headers = {"x-forwarded-for": forwarded} if forwarded else {}
        self.client = types.SimpleNamespace(host=ip)


@pytest.fixture
def enabled_rate_limit(monkeypatch):
    """Turn the limiter on for one test, over a fresh in-process store."""
    from app.core import rate_limit as module

    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(
        module, "_limiter", module.RateLimiter(module.MemoryStore())
    )
    return module


# ---------------------------------------------------------------------------
# Rate limiter — unit
# ---------------------------------------------------------------------------
class _Clock:
    """A monotonic clock the test can advance."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now


def test_memory_store_starts_a_new_window_once_the_old_one_expires(monkeypatch) -> None:
    from app.core import rate_limit as module

    clock = _Clock()
    monkeypatch.setattr(module, "time", clock)

    store = module.MemoryStore()
    assert store.hit("k", 60) == 1
    assert store.hit("k", 60) == 2
    # A different key is a different bucket.
    assert store.hit("other", 60) == 1
    # Once the window elapses the count restarts.
    clock.now += 61
    assert store.hit("k", 60) == 1


def test_limiter_check_returns_retry_after_only_once_over_the_limit() -> None:
    from app.core.rate_limit import MemoryStore, RateLimiter

    limiter = RateLimiter(MemoryStore())
    assert limiter.check("k", limit=2, window_seconds=60) == 0
    assert limiter.check("k", limit=2, window_seconds=60) == 0
    assert limiter.check("k", limit=2, window_seconds=60) == 60


def test_limiter_key_is_the_first_hop_of_x_forwarded_for(enabled_rate_limit) -> None:
    from app.core.rate_limit import rate_limit

    dependency = rate_limit("unit", limit=1)

    # Two different proxy chains ending at the same client share one bucket...
    dependency(_FakeRequest(forwarded="9.9.9.9, 10.0.0.1"))
    with pytest.raises(Exception) as exc:
        dependency(_FakeRequest(forwarded="9.9.9.9, 10.0.0.2"))

    # ...and the dependency raised the 429 the limiter asked for.
    assert getattr(exc.value, "status_code", None) == 429

    # A different client is counted separately.
    dependency(_FakeRequest(forwarded="8.8.8.8, 10.0.0.3"))


def test_rate_limit_dependency_is_a_no_op_when_disabled(monkeypatch) -> None:
    from app.core.rate_limit import rate_limit

    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    dependency = rate_limit("unit-disabled", limit=1)
    # Would raise on the second call if the limit were enforced.
    dependency(_FakeRequest())
    dependency(_FakeRequest())


# ---------------------------------------------------------------------------
# Rate limiter — through the API
# ---------------------------------------------------------------------------
def test_auth_endpoint_returns_429_with_retry_after(api_client, enabled_rate_limit) -> None:
    limit = settings.rate_limit_auth_per_minute
    payload = {"email": "nobody@example.com", "password": "not-the-right-pass"}

    for _ in range(limit):
        assert api_client.post(LOGIN, json=payload).status_code == 401

    throttled = api_client.post(LOGIN, json=payload)
    assert throttled.status_code == 429
    assert throttled.headers["retry-after"] == str(settings.rate_limit_window_seconds)


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------
def test_json_responses_carry_security_headers(api_client) -> None:
    response = api_client.get(HEALTH)
    assert response.status_code == 200

    headers = response.headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cross-origin-opener-policy"] == "same-origin"
    assert headers["content-security-policy"].startswith("default-src 'none'")

    # HSTS only ships where HTTPS is guaranteed, so never in a dev/test run.
    if settings.environment != "production":
        assert "strict-transport-security" not in headers


def test_docs_pages_are_exempt_from_the_csp(api_client) -> None:
    response = api_client.get("/docs")
    assert response.status_code == 200
    assert "content-security-policy" not in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"


# ---------------------------------------------------------------------------
# CSRF — the origin guard
# ---------------------------------------------------------------------------
def test_cross_origin_state_change_is_refused(api_client) -> None:
    response = api_client.post(
        LOGIN,
        json={"email": "nobody@example.com", "password": "whatever-pass"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert "cross-origin" in response.json()["detail"].lower()


def test_same_origin_state_change_is_allowed(api_client) -> None:
    response = api_client.post(
        LOGIN,
        json={"email": "nobody@example.com", "password": "whatever-pass"},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code != 403


def test_originless_state_change_is_allowed(api_client) -> None:
    """CLIs, mobile clients and the test suite send no ``Origin`` header."""
    response = api_client.post(
        LOGIN, json={"email": "nobody@example.com", "password": "whatever-pass"}
    )
    assert response.status_code != 403


def test_safe_methods_are_not_origin_checked(api_client) -> None:
    response = api_client.get(HEALTH, headers={"Origin": "https://evil.example"})
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------
def test_registration_is_recorded_in_the_audit_trail(api_client, db) -> None:
    _register(api_client, "audit-reg@example.com")

    entries = db.scalars(
        select(AuditLog).where(AuditLog.action == audit_service.REGISTERED)
    ).all()
    assert len(entries) == 1
    assert entries[0].user_id is not None
    assert entries[0].workspace_id is not None
    assert entries[0].ip_address  # the caller's address is captured


def test_failed_login_is_recorded_in_the_audit_trail(api_client, db) -> None:
    _register(api_client, "audit-fail@example.com")

    response = api_client.post(
        LOGIN,
        json={"email": "audit-fail@example.com", "password": "not-the-password-x"},
    )
    assert response.status_code == 401

    entries = db.scalars(
        select(AuditLog).where(AuditLog.action == audit_service.LOGIN_FAILED)
    ).all()
    assert len(entries) == 1
    # The address is known, so the attempt is filed against that account.
    assert entries[0].user_id is not None
    assert entries[0].workspace_id is not None


def test_failed_login_for_an_unknown_address_names_no_account(api_client, db) -> None:
    response = api_client.post(
        LOGIN, json={"email": "ghost@example.com", "password": "not-the-password-x"}
    )
    assert response.status_code == 401

    entries = db.scalars(
        select(AuditLog).where(AuditLog.action == audit_service.LOGIN_FAILED)
    ).all()
    assert len(entries) == 1
    assert entries[0].user_id is None
    assert entries[0].workspace_id is None


def test_successful_login_is_recorded_in_the_audit_trail(api_client, db) -> None:
    _register(api_client, "audit-ok@example.com")

    api_client.post(
        LOGIN, json={"email": "audit-ok@example.com", "password": STRONG_PASSWORD}
    )

    entries = db.scalars(
        select(AuditLog).where(AuditLog.action == audit_service.LOGIN_SUCCEEDED)
    ).all()
    assert len(entries) == 1
    assert entries[0].user_id is not None


def test_an_owner_can_read_the_audit_trail_newest_first(api_client) -> None:
    response = _register(api_client, "audit-reader@example.com")
    token = response.json()["access_token"]
    workspace_id = _workspace_id(api_client, token)

    listing = api_client.get(
        f"{WORKSPACES}/{workspace_id}/audit", headers=_bearer(token)
    )
    assert listing.status_code == 200, listing.text

    body = listing.json()
    assert body["count"] >= 1
    assert body["max_limit"] == audit_service.MAX_PAGE_SIZE
    created = [item["created_at"] for item in body["items"]]
    assert created == sorted(created, reverse=True), "entries must be newest first"


def test_the_audit_trail_can_be_filtered_by_action_prefix(api_client) -> None:
    response = _register(api_client, "audit-filter@example.com")
    token = response.json()["access_token"]
    workspace_id = _workspace_id(api_client, token)

    api_client.post(
        LOGIN, json={"email": "audit-filter@example.com", "password": STRONG_PASSWORD}
    )

    listing = api_client.get(
        f"{WORKSPACES}/{workspace_id}/audit",
        params={"action": "auth.login"},
        headers=_bearer(token),
    )
    assert listing.status_code == 200
    actions = {item["action"] for item in listing.json()["items"]}
    assert actions == {audit_service.LOGIN_SUCCEEDED}


def test_a_member_without_audit_read_is_forbidden(api_client, db) -> None:
    owner = _register(api_client, "audit-owner@example.com")
    owner_token = owner.json()["access_token"]
    workspace_id = _workspace_id(api_client, owner_token)

    editor = _register(api_client, "audit-editor@example.com")
    editor_token = editor.json()["access_token"]
    editor_user = db.scalar(select(User).where(User.email == "audit-editor@example.com"))
    _add_membership(db, workspace_id, editor_user.id, "editor")

    denied = api_client.get(
        f"{WORKSPACES}/{workspace_id}/audit", headers=_bearer(editor_token)
    )
    assert denied.status_code == 403


def test_the_audit_trail_is_hidden_from_non_members(api_client) -> None:
    owner = _register(api_client, "audit-owner2@example.com")
    workspace_id = _workspace_id(api_client, owner.json()["access_token"])

    stranger = _register(api_client, "audit-stranger@example.com")
    stranger_token = stranger.json()["access_token"]

    hidden = api_client.get(
        f"{WORKSPACES}/{workspace_id}/audit", headers=_bearer(stranger_token)
    )
    assert hidden.status_code == 404, "a non-member must not learn the workspace exists"


def test_a_registry_change_is_recorded_in_the_audit_trail(api_client, db) -> None:
    owner = _register(api_client, "audit-admin@example.com")
    token = owner.json()["access_token"]
    workspace_id = _workspace_id(api_client, token)

    created = api_client.post(
        f"/api/v1/ai/{workspace_id}/models",
        json={
            "name": "audit-test-model",
            "display_name": "Audit Test Model",
            "kind": "openai_compatible",
            "model": "gpt-4o-mini",
        },
        headers=_bearer(token),
    )
    assert created.status_code == 201, created.text

    entries = db.scalars(
        select(AuditLog).where(AuditLog.action == audit_service.AI_MODEL_CREATED)
    ).all()
    assert len(entries) == 1
    assert entries[0].target_type == "ai_model"
    assert entries[0].workspace_id is not None
