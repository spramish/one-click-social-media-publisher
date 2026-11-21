"""Phase 3 integration tests — the authentication & authorization API.

Every test drives the real FastAPI app through ``TestClient`` against the
migrated ``publisher_test`` database. The ``api_client`` fixture wires the app's
``get_db`` dependency to the test's own transaction, so rows created by a
request are visible to the assertions and everything is discarded afterwards.

The suite is organised around the guarantees the phase promises:

* registration provisions a personal workspace owned by the new account;
* credentials never leak (no hash, no refresh token in a JSON body);
* refresh tokens **rotate**, and replaying a rotated token revokes the family;
* logout / password changes invalidate live sessions immediately (the access
  token is stateless, the *session* is not);
* e-mail verification and password reset use single-use tokens;
* tenant isolation (404, not 403) and role-based access (403) hold on the
  workspace routes.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.enums import VerificationPurpose, WorkspaceMemberStatus
from app.models import User, WorkspaceMember
from app.services import auth as auth_service

# --- Endpoint URLs ---------------------------------------------------------
REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
LOGOUT_ALL = "/api/v1/auth/logout-all"
ME = "/api/v1/auth/me"
SESSIONS = "/api/v1/auth/sessions"
CHANGE_PASSWORD = "/api/v1/auth/password/change"
REQUEST_RESET = "/api/v1/auth/password/reset"
CONFIRM_RESET = "/api/v1/auth/password/reset/confirm"
VERIFY_EMAIL = "/api/v1/auth/email/verify"
REQUEST_VERIFICATION = "/api/v1/auth/email/verification"
WORKSPACES = "/api/v1/workspaces"

STRONG_PASSWORD = "correct-horse-battery"
OTHER_PASSWORD = "a-different-strong-pass"
COOKIE = settings.refresh_cookie_name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _register(
    client,
    email: str,
    *,
    password: str = STRONG_PASSWORD,
    full_name: str = "Test User",
):
    """Register an account and return the 201 response (asserted)."""
    response = client.post(
        REGISTER,
        json={"email": email, "password": password, "full_name": full_name},
    )
    assert response.status_code == 201, response.text
    return response


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _access_token(response) -> str:
    return response.json()["access_token"]


def _user(db, email: str) -> User:
    user = db.scalar(select(User).where(User.email == email.lower()))
    assert user is not None, f"user {email!r} was not persisted"
    return user


def _add_membership(db, workspace_id: uuid.UUID, user_id: uuid.UUID, role_name: str) -> WorkspaceMember:
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
# Registration
# ---------------------------------------------------------------------------
def test_register_provisions_account_workspace_and_session(api_client, db) -> None:
    response = _register(api_client, "Pita@Example.com")

    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["user"]["email"] == "pita@example.com"  # normalised
    assert body["user"]["is_verified"] is False
    assert COOKIE in response.cookies, "refresh token must be set as a cookie"

    # The personal workspace exists and the account owns it.
    user = _user(db, "pita@example.com")
    membership = db.scalar(
        select(WorkspaceMember).where(WorkspaceMember.user_id == user.id)
    )
    assert membership is not None
    assert membership.role.name == "owner"

    listing = api_client.get(WORKSPACES, headers=_bearer(_access_token(response)))
    assert listing.status_code == 200
    workspaces = listing.json()
    assert len(workspaces) == 1
    assert workspaces[0]["is_owner"] is True
    assert workspaces[0]["role"] == "owner"
    assert "workspace:read" in workspaces[0]["permissions"]


def test_register_response_never_exposes_secrets(api_client) -> None:
    response = _register(api_client, "secret@example.com")

    body = response.json()
    assert "hashed_password" not in body
    assert "refresh_token" not in body, "refresh token must travel only in the cookie"
    assert STRONG_PASSWORD not in response.text


def test_register_rejects_duplicate_email(api_client) -> None:
    _register(api_client, "dup@example.com")

    again = api_client.post(
        REGISTER,
        json={"email": "DUP@example.com", "password": STRONG_PASSWORD},
    )

    assert again.status_code == 409


def test_register_rejects_a_short_password(api_client) -> None:
    response = api_client.post(
        REGISTER,
        json={"email": "weak@example.com", "password": "short"},
    )

    assert response.status_code == 422


def test_register_sends_a_verification_email(api_client, monkeypatch) -> None:
    captured: list = []
    monkeypatch.setattr(auth_service, "send_email", captured.append)

    _register(api_client, "greet@example.com")

    assert len(captured) == 1
    message = captured[0]
    assert message.to == "greet@example.com"
    assert "verify-email?token=" in message.text_body


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
def test_login_returns_tokens_and_sets_cookie(api_client) -> None:
    _register(api_client, "return@example.com")

    response = api_client.post(
        LOGIN,
        json={"email": "return@example.com", "password": STRONG_PASSWORD},
    )

    assert response.status_code == 200
    assert COOKIE in response.cookies
    assert response.json()["user"]["email"] == "return@example.com"


def test_login_rejects_a_wrong_password(api_client) -> None:
    _register(api_client, "wrong@example.com")

    response = api_client.post(
        LOGIN,
        json={"email": "wrong@example.com", "password": "not-the-password-x"},
    )

    assert response.status_code == 401


def test_login_unknown_email_is_indistinguishable_from_a_wrong_password(api_client, db) -> None:
    _register(api_client, "known@example.com")

    unknown = api_client.post(
        LOGIN, json={"email": "nobody@example.com", "password": STRONG_PASSWORD}
    )
    wrong = api_client.post(
        LOGIN, json={"email": "known@example.com", "password": "not-the-password-x"}
    )

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json(), "responses must not reveal account existence"


# ---------------------------------------------------------------------------
# Current account / protected route
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer not-a-token"}, {"Authorization": "Bearer a.b.c"}],
)
def test_me_requires_a_valid_access_token(api_client, headers) -> None:
    assert api_client.get(ME, headers=headers).status_code == 401


def test_me_returns_the_authenticated_account(api_client) -> None:
    response = _register(api_client, "me@example.com")

    me = api_client.get(ME, headers=_bearer(_access_token(response)))

    assert me.status_code == 200
    assert me.json()["email"] == "me@example.com"


# ---------------------------------------------------------------------------
# Refresh rotation
# ---------------------------------------------------------------------------
def test_refresh_rotates_the_token_and_issues_a_usable_access_token(api_client) -> None:
    _register(api_client, "rotate@example.com")
    first = api_client.cookies.get(COOKIE)

    response = api_client.post(REFRESH)

    assert response.status_code == 200
    second = api_client.cookies.get(COOKIE)
    assert second and second != first, "refresh must hand out a new token"

    me = api_client.get(ME, headers=_bearer(_access_token(response)))
    assert me.status_code == 200


def test_refresh_reuse_revokes_the_whole_family(api_client) -> None:
    _register(api_client, "reuse@example.com")
    stolen = api_client.cookies.get(COOKIE)

    rotated = api_client.post(REFRESH)
    assert rotated.status_code == 200
    successor_token = _access_token(rotated)

    # Replay the already-rotated token: strong evidence of theft.
    replay = api_client.post(REFRESH, cookies={COOKIE: stolen})
    assert replay.status_code == 401

    # ... and the legitimate successor is invalidated too.
    assert api_client.get(ME, headers=_bearer(successor_token)).status_code == 401


def test_refresh_without_a_token_is_rejected(api_client) -> None:
    api_client.cookies.clear()

    assert api_client.post(REFRESH).status_code == 401


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------
def test_logout_revokes_the_session_immediately(api_client) -> None:
    response = _register(api_client, "bye@example.com")
    token = _access_token(response)

    logout = api_client.post(LOGOUT)

    assert logout.status_code == 200
    assert api_client.get(ME, headers=_bearer(token)).status_code == 401


def test_logout_all_revokes_every_session(api_client) -> None:
    first = _register(api_client, "all@example.com")
    second = api_client.post(
        LOGIN, json={"email": "all@example.com", "password": STRONG_PASSWORD}
    )
    assert second.status_code == 200

    logout_all = api_client.post(LOGOUT_ALL, headers=_bearer(_access_token(second)))
    assert logout_all.status_code == 200

    assert api_client.get(ME, headers=_bearer(_access_token(first))).status_code == 401
    assert api_client.get(ME, headers=_bearer(_access_token(second))).status_code == 401


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------
def test_sessions_lists_devices_and_flags_the_current_one(api_client) -> None:
    response = _register(api_client, "devices@example.com")

    sessions = api_client.get(SESSIONS, headers=_bearer(_access_token(response)))

    assert sessions.status_code == 200
    rows = sessions.json()
    assert len(rows) == 1
    assert rows[0]["current"] is True


def test_revoking_another_session_invalidates_its_token(api_client) -> None:
    first = _register(api_client, "two-devices@example.com")
    second = api_client.post(
        LOGIN, json={"email": "two-devices@example.com", "password": STRONG_PASSWORD}
    )
    assert second.status_code == 200

    listing = api_client.get(SESSIONS, headers=_bearer(_access_token(second))).json()
    other = next(row for row in listing if not row["current"])

    revoke = api_client.delete(
        f"{SESSIONS}/{other['id']}", headers=_bearer(_access_token(second))
    )
    assert revoke.status_code == 200
    assert api_client.get(ME, headers=_bearer(_access_token(first))).status_code == 401


def test_a_user_cannot_revoke_a_session_they_do_not_own(api_client, db) -> None:
    victim = _register(api_client, "victim@example.com")
    attacker = _register(api_client, "attacker@example.com")
    victim_session_id = api_client.get(
        SESSIONS, headers=_bearer(_access_token(victim))
    ).json()[0]["id"]

    response = api_client.delete(
        f"{SESSIONS}/{victim_session_id}", headers=_bearer(_access_token(attacker))
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Password change / reset
# ---------------------------------------------------------------------------
def test_change_password_revokes_other_sessions(api_client) -> None:
    first = _register(api_client, "change@example.com")
    second = api_client.post(
        LOGIN, json={"email": "change@example.com", "password": STRONG_PASSWORD}
    )
    assert second.status_code == 200

    change = api_client.post(
        CHANGE_PASSWORD,
        headers=_bearer(_access_token(second)),
        json={"current_password": STRONG_PASSWORD, "new_password": OTHER_PASSWORD},
    )
    assert change.status_code == 200

    # The other device is signed out; the caller survives.
    assert api_client.get(ME, headers=_bearer(_access_token(first))).status_code == 401
    assert api_client.get(ME, headers=_bearer(_access_token(second))).status_code == 200

    # New password works, old one does not.
    assert (
        api_client.post(
            LOGIN, json={"email": "change@example.com", "password": OTHER_PASSWORD}
        ).status_code
        == 200
    )
    assert (
        api_client.post(
            LOGIN, json={"email": "change@example.com", "password": STRONG_PASSWORD}
        ).status_code
        == 401
    )


def test_change_password_rejects_a_wrong_current_password(api_client) -> None:
    response = _register(api_client, "wrong-current@example.com")

    change = api_client.post(
        CHANGE_PASSWORD,
        headers=_bearer(_access_token(response)),
        json={"current_password": "not-the-password-x", "new_password": OTHER_PASSWORD},
    )

    assert change.status_code == 400


def test_password_reset_request_does_not_reveal_accounts(api_client) -> None:
    _register(api_client, "exists@example.com")

    known = api_client.post(REQUEST_RESET, json={"email": "exists@example.com"})
    unknown = api_client.post(REQUEST_RESET, json={"email": "ghost@example.com"})

    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()


def test_password_reset_confirm_sets_a_new_password(api_client, db) -> None:
    _register(api_client, "reset@example.com")
    user = _user(db, "reset@example.com")
    _, raw_token = auth_service.issue_verification_token(
        db, user, VerificationPurpose.PASSWORD_RESET
    )

    confirm = api_client.post(
        CONFIRM_RESET, json={"token": raw_token, "new_password": OTHER_PASSWORD}
    )

    assert confirm.status_code == 200
    assert (
        api_client.post(
            LOGIN, json={"email": "reset@example.com", "password": OTHER_PASSWORD}
        ).status_code
        == 200
    )
    assert (
        api_client.post(
            LOGIN, json={"email": "reset@example.com", "password": STRONG_PASSWORD}
        ).status_code
        == 401
    )


def test_password_reset_token_cannot_be_used_twice(api_client, db) -> None:
    _register(api_client, "once@example.com")
    user = _user(db, "once@example.com")
    _, raw_token = auth_service.issue_verification_token(
        db, user, VerificationPurpose.PASSWORD_RESET
    )

    first = api_client.post(
        CONFIRM_RESET, json={"token": raw_token, "new_password": OTHER_PASSWORD}
    )
    second = api_client.post(
        CONFIRM_RESET, json={"token": raw_token, "new_password": "yet-another-strong-x"}
    )

    assert first.status_code == 200
    assert second.status_code == 400


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------
def test_email_verification_marks_the_account_verified(api_client, db) -> None:
    response = _register(api_client, "verify@example.com")
    user = _user(db, "verify@example.com")
    _, raw_token = auth_service.issue_verification_token(
        db, user, VerificationPurpose.EMAIL_VERIFICATION
    )

    verify = api_client.post(VERIFY_EMAIL, json={"token": raw_token})
    assert verify.status_code == 200

    me = api_client.get(ME, headers=_bearer(_access_token(response)))
    assert me.json()["is_verified"] is True


def test_email_verification_rejects_an_unknown_token(api_client) -> None:
    response = api_client.post(VERIFY_EMAIL, json={"token": "x" * 40})

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Workspaces: tenant isolation & RBAC
# ---------------------------------------------------------------------------
def test_workspace_listing_requires_authentication(api_client) -> None:
    assert api_client.get(WORKSPACES).status_code == 401


def test_non_members_cannot_read_a_workspace(api_client) -> None:
    owner = _register(api_client, "owner@example.com")
    workspace_id = api_client.get(
        WORKSPACES, headers=_bearer(_access_token(owner))
    ).json()[0]["id"]

    intruder = _register(api_client, "intruder@example.com")
    response = api_client.get(
        f"{WORKSPACES}/{workspace_id}", headers=_bearer(_access_token(intruder))
    )

    assert response.status_code == 404, "non-members must not learn the workspace exists"


def test_viewer_is_denied_a_privileged_action(api_client, db) -> None:
    owner = _register(api_client, "boss@example.com")
    workspace_id = api_client.get(
        WORKSPACES, headers=_bearer(_access_token(owner))
    ).json()[0]["id"]

    viewer = _register(api_client, "viewer@example.com")
    viewer_user = _user(db, "viewer@example.com")
    membership = _add_membership(db, uuid.UUID(workspace_id), viewer_user.id, "viewer")

    response = api_client.delete(
        f"{WORKSPACES}/{workspace_id}/members/{membership.id}",
        headers=_bearer(_access_token(viewer)),
    )

    assert response.status_code == 403, "a viewer may not remove members"


def test_owner_can_remove_a_member_and_access_is_revoked(api_client, db) -> None:
    owner = _register(api_client, "chief@example.com")
    workspace_id = api_client.get(
        WORKSPACES, headers=_bearer(_access_token(owner))
    ).json()[0]["id"]

    member_resp = _register(api_client, "member@example.com")
    member_user = _user(db, "member@example.com")
    membership = _add_membership(db, uuid.UUID(workspace_id), member_user.id, "editor")

    removed = api_client.delete(
        f"{WORKSPACES}/{workspace_id}/members/{membership.id}",
        headers=_bearer(_access_token(owner)),
    )
    assert removed.status_code == 200

    # The removed member can no longer reach the workspace.
    after = api_client.get(
        f"{WORKSPACES}/{workspace_id}", headers=_bearer(_access_token(member_resp))
    )
    assert after.status_code == 404


def test_workspace_members_listing_is_scoped(api_client) -> None:
    response = _register(api_client, "listing@example.com")
    token = _access_token(response)
    workspace_id = api_client.get(WORKSPACES, headers=_bearer(token)).json()[0]["id"]

    members = api_client.get(f"{WORKSPACES}/{workspace_id}/members", headers=_bearer(token))

    assert members.status_code == 200
    body = members.json()
    assert len(body) == 1
    assert body[0]["email"] == "listing@example.com"
    assert body[0]["role"] == "owner"
