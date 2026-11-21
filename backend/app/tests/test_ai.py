"""Phase 9 integration tests — the AI content assistant.

Like the Phase 7 and 8 suites, these drive the real FastAPI app through
``TestClient`` against the migrated ``publisher_test`` database, with the
provider's HTTP layer swapped for an ``httpx.MockTransport`` so nothing touches
the network or the OpenAI bill. What is exercised for real is the assistant's
contract:

* a missing API key is a *capability gap* — ``GET .../capability`` reports
  ``configured: false`` and ``POST .../generate`` answers ``503``;
* a generation is stored as a suggestion and **nothing is published**: no post
  target, no job, and ``is_accepted`` stays false until the user accepts;
* the prompt, the model and the tone are recorded so a suggestion is auditable;
* unknown tones, a rewrite without a draft, and a policy refusal are ``422``;
  a throttled or unreachable provider is ``503``; a rejected call is ``502``;
* the provider is called with the configured model and a bearer credential, and
  the credential never appears in a response;
* tenant isolation (another workspace's post is ``404``) and RBAC (a viewer has
  no ``ai:generate``) hold on every route.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.enums import AIContentKind, WorkspaceMemberStatus
from app.models import AIGeneratedContent, Post, PublishingJob, User, WorkspaceMember
from app.services import ai_content as ai_service
from app.services import auth as auth_service
from app.services.ai import get_provider

# --- Endpoint URLs ---------------------------------------------------------
REGISTER = "/api/v1/auth/register"
WORKSPACES = "/api/v1/workspaces"
POSTS = "/api/v1/posts"
AI = "/api/v1/ai"

STRONG_PASSWORD = "correct-horse-battery"


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def ai_configured(monkeypatch):
    """Pretend a real API key is present for the duration of one test."""
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-not-a-real-key")
    return settings.openai_api_key


def _install_transport(monkeypatch, handler) -> None:
    """Swap the provider's HTTP layer for a mock, restored after the test."""
    monkeypatch.setattr(get_provider(), "transport", httpx.MockTransport(handler))


def _completion(
    text: str = "A brand new caption",
    *,
    model: str = "gpt-4o-mini",
    finish_reason: str | None = "stop",
    usage: dict | None = None,
):
    """A handler that returns one successful chat completion.

    ``handler.last_request`` keeps the request so a test can assert what the
    provider actually sent.
    """
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": usage or {"prompt_tokens": 20, "completion_tokens": 12},
            },
        )

    handler.calls = calls  # type: ignore[attr-defined]
    return handler


def _error_handler(status_code: int, message: str, *, code: str | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        body: dict = {"error": {"message": message}}
        if code:
            body["error"]["code"] = code
            body["error"]["type"] = code
        return httpx.Response(status_code, json=body)

    return handler


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(client, email: str) -> tuple[str, uuid.UUID]:
    response = client.post(
        REGISTER,
        json={"email": email, "password": STRONG_PASSWORD, "full_name": "Owner"},
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    workspaces = client.get(WORKSPACES, headers=_bearer(token)).json()
    return token, uuid.UUID(workspaces[0]["id"])


def _add_membership(db, workspace_id: uuid.UUID, user_id: uuid.UUID, role_name: str) -> None:
    db.add(
        WorkspaceMember(
            workspace_id=workspace_id,
            user_id=user_id,
            role_id=auth_service.get_role(db, role_name).id,
            status=WorkspaceMemberStatus.ACTIVE,
        )
    )
    db.flush()


def _generate(client, token, workspace_id, **payload):
    body = {"kind": AIContentKind.CAPTION.value, "brief": "A drone flight over the coast"}
    body.update(payload)
    return client.post(f"{AI}/{workspace_id}/generate", json=body, headers=_bearer(token))


# ---------------------------------------------------------------------------
# Capability
# ---------------------------------------------------------------------------
def test_capability_reports_unconfigured_without_a_key(api_client, db) -> None:
    token, workspace_id = _register(api_client, "ai-cap@example.com")

    response = api_client.get(f"{AI}/{workspace_id}/capability", headers=_bearer(token))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["configured"] is False
    assert body["provider"] == "openai"
    assert body["model"] == settings.openai_model
    assert set(body["kinds"]) == {kind.value for kind in AIContentKind}
    assert "professional" in body["tones"]
    assert "short" in body["styles"]
    assert body["max_brief_chars"] == settings.ai_max_brief_chars


def test_capability_reports_configured_once_a_key_exists(
    api_client, db, ai_configured
) -> None:
    token, workspace_id = _register(api_client, "ai-cap-on@example.com")

    body = api_client.get(f"{AI}/{workspace_id}/capability", headers=_bearer(token)).json()

    assert body["configured"] is True


# ---------------------------------------------------------------------------
# The capability gap: no key means 503, not a crash
# ---------------------------------------------------------------------------
def test_generating_without_a_key_is_service_unavailable(api_client, db) -> None:
    token, workspace_id = _register(api_client, "ai-nokey@example.com")

    response = _generate(api_client, token, workspace_id)

    assert response.status_code == 503
    assert "not configured" in response.json()["detail"].lower()
    assert db.scalars(select(AIGeneratedContent)).all() == []


# ---------------------------------------------------------------------------
# A successful generation
# ---------------------------------------------------------------------------
def test_generate_stores_a_suggestion_and_publishes_nothing(
    api_client, db, ai_configured, monkeypatch
) -> None:
    handler = _completion("Golden hour over the coastline.")
    _install_transport(monkeypatch, handler)
    token, workspace_id = _register(api_client, "ai-gen@example.com")

    response = _generate(
        api_client,
        token,
        workspace_id,
        brief="A drone flight over the coast at sunset",
        tone="inspirational",
        platform="instagram",
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["output"] == "Golden hour over the coastline."
    assert body["kind"] == AIContentKind.CAPTION.value
    assert body["model"] == "gpt-4o-mini"
    assert body["tone"] == "inspirational"
    assert body["is_accepted"] is False
    assert body["post_id"] is None
    assert "A drone flight over the coast at sunset" in body["prompt"]
    assert "Instagram" in body["prompt"]

    # Stored exactly once, as a draft...
    content = db.scalars(select(AIGeneratedContent)).one()
    assert content.output == "Golden hour over the coastline."
    assert content.created_by_id is not None

    # ...and publishing is untouched: the master prompt forbids auto-publishing
    # AI output without the user's authorization.
    assert db.scalars(select(Post)).all() == []
    assert db.scalars(select(PublishingJob)).all() == []


def test_generate_sends_the_configured_model_and_a_bearer_credential(
    api_client, db, ai_configured, monkeypatch
) -> None:
    handler = _completion()
    _install_transport(monkeypatch, handler)
    token, workspace_id = _register(api_client, "ai-request@example.com")

    assert _generate(api_client, token, workspace_id).status_code == 201

    request = handler.calls[0]  # type: ignore[attr-defined]
    assert request.method == "POST"
    assert request.url.path.endswith("/chat/completions")
    assert request.headers["authorization"] == f"Bearer {ai_configured}"
    sent = json.loads(request.content)
    assert sent["model"] == settings.openai_model
    assert sent["max_tokens"] == settings.openai_max_output_tokens
    assert sent["messages"][0]["role"] == "system"
    assert sent["messages"][1]["role"] == "user"


def test_hashtags_and_description_are_supported_kinds(
    api_client, db, ai_configured, monkeypatch
) -> None:
    _install_transport(monkeypatch, _completion("#travel #drone #coast"))
    token, workspace_id = _register(api_client, "ai-kinds@example.com")

    tags = _generate(
        api_client, token, workspace_id, kind=AIContentKind.HASHTAGS.value, brief="travel"
    )
    assert tags.status_code == 201, tags.text
    assert tags.json()["kind"] == AIContentKind.HASHTAGS.value
    assert tags.json()["output"].startswith("#travel")

    _install_transport(monkeypatch, _completion("A long-form description."))
    description = _generate(
        api_client, token, workspace_id, kind=AIContentKind.DESCRIPTION.value, brief="travel"
    )
    assert description.status_code == 201, description.text
    assert description.json()["kind"] == AIContentKind.DESCRIPTION.value


def test_a_rewrite_uses_the_supplied_draft(
    api_client, db, ai_configured, monkeypatch
) -> None:
    handler = _completion("A tighter caption.")
    _install_transport(monkeypatch, handler)
    token, workspace_id = _register(api_client, "ai-rewrite@example.com")

    response = _generate(
        api_client,
        token,
        workspace_id,
        kind=AIContentKind.REWRITE.value,
        brief="Make it shorter",
        draft="This is a very long caption that rambles on and on and on.",
    )

    assert response.status_code == 201, response.text
    assert "This is a very long caption" in response.json()["prompt"]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def test_a_rewrite_without_a_draft_is_rejected(api_client, db, ai_configured, monkeypatch) -> None:
    _install_transport(monkeypatch, _completion())
    token, workspace_id = _register(api_client, "ai-nodraft@example.com")

    response = _generate(
        api_client, token, workspace_id, kind=AIContentKind.REWRITE.value, brief="Shorten it"
    )

    assert response.status_code == 422
    assert "rewrit" in response.json()["detail"].lower()


def test_an_unknown_tone_is_rejected(api_client, db, ai_configured, monkeypatch) -> None:
    _install_transport(monkeypatch, _completion())
    token, workspace_id = _register(api_client, "ai-tone@example.com")

    response = _generate(api_client, token, workspace_id, tone="shakespearean")

    assert response.status_code == 422
    assert "Unknown tone" in response.json()["detail"]


def test_an_empty_brief_is_rejected(api_client, db, ai_configured, monkeypatch) -> None:
    _install_transport(monkeypatch, _completion())
    token, workspace_id = _register(api_client, "ai-empty@example.com")

    response = _generate(api_client, token, workspace_id, brief="   ")

    assert response.status_code == 422


def test_an_overlong_brief_is_rejected(api_client, db, ai_configured, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_max_brief_chars", 10)
    _install_transport(monkeypatch, _completion())
    token, workspace_id = _register(api_client, "ai-long@example.com")

    response = _generate(api_client, token, workspace_id, brief="x" * 50)

    assert response.status_code == 422
    assert "under 10 characters" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Provider failures are classified
# ---------------------------------------------------------------------------
def test_a_throttled_provider_is_service_unavailable(
    api_client, db, ai_configured, monkeypatch
) -> None:
    _install_transport(monkeypatch, _error_handler(429, "Rate limit reached"))
    token, workspace_id = _register(api_client, "ai-429@example.com")

    response = _generate(api_client, token, workspace_id)

    assert response.status_code == 503
    assert db.scalars(select(AIGeneratedContent)).all() == []


def test_an_unreachable_provider_is_service_unavailable(
    api_client, db, ai_configured, monkeypatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _install_transport(monkeypatch, handler)
    token, workspace_id = _register(api_client, "ai-down@example.com")

    response = _generate(api_client, token, workspace_id)

    assert response.status_code == 503


def test_a_provider_side_failure_is_bad_gateway(
    api_client, db, ai_configured, monkeypatch
) -> None:
    _install_transport(monkeypatch, _error_handler(500, "Internal error"))
    token, workspace_id = _register(api_client, "ai-500@example.com")

    response = _generate(api_client, token, workspace_id)

    assert response.status_code == 503  # 5xx is classified as transient


def test_a_rejected_request_is_bad_gateway(
    api_client, db, ai_configured, monkeypatch
) -> None:
    _install_transport(monkeypatch, _error_handler(401, "Incorrect API key provided"))
    token, workspace_id = _register(api_client, "ai-401@example.com")

    response = _generate(api_client, token, workspace_id)

    assert response.status_code == 502


def test_a_content_policy_refusal_is_unprocessable(
    api_client, db, ai_configured, monkeypatch
) -> None:
    _install_transport(monkeypatch, _error_handler(400, "Your request was rejected by our safety system"))
    token, workspace_id = _register(api_client, "ai-policy@example.com")

    response = _generate(api_client, token, workspace_id)

    assert response.status_code == 422
    assert "rephrasing" in response.json()["detail"].lower()


def test_a_finish_reason_of_content_filter_is_unprocessable(
    api_client, db, ai_configured, monkeypatch
) -> None:
    _install_transport(monkeypatch, _completion("", finish_reason="content_filter"))
    token, workspace_id = _register(api_client, "ai-filter@example.com")

    response = _generate(api_client, token, workspace_id)

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# History and acceptance
# ---------------------------------------------------------------------------
def test_accept_marks_a_suggestion_without_publishing_it(
    api_client, db, ai_configured, monkeypatch
) -> None:
    _install_transport(monkeypatch, _completion("Accept me"))
    token, workspace_id = _register(api_client, "ai-accept@example.com")
    content_id = _generate(api_client, token, workspace_id).json()["id"]

    response = api_client.post(
        f"{AI}/{workspace_id}/history/{content_id}/accept", headers=_bearer(token)
    )

    assert response.status_code == 200, response.text
    assert response.json()["is_accepted"] is True
    assert db.scalars(select(PublishingJob)).all() == []


def test_history_lists_only_this_workspace_and_filters_by_kind(
    api_client, db, ai_configured, monkeypatch
) -> None:
    token, workspace_id = _register(api_client, "ai-history@example.com")

    _install_transport(monkeypatch, _completion("caption one"))
    _generate(api_client, token, workspace_id, kind=AIContentKind.CAPTION.value)
    _install_transport(monkeypatch, _completion("#one #two"))
    _generate(api_client, token, workspace_id, kind=AIContentKind.HASHTAGS.value, brief="travel")

    everything = api_client.get(f"{AI}/{workspace_id}/history", headers=_bearer(token)).json()
    captions = api_client.get(
        f"{AI}/{workspace_id}/history?kind=caption", headers=_bearer(token)
    ).json()

    assert len(everything) == 2
    assert [row["kind"] for row in captions] == [AIContentKind.CAPTION.value]


# ---------------------------------------------------------------------------
# Tenant isolation + RBAC
# ---------------------------------------------------------------------------
def test_another_workspaces_suggestions_are_invisible_and_unreachable(
    api_client, db, ai_configured, monkeypatch
) -> None:
    _install_transport(monkeypatch, _completion("private"))
    token, workspace_id = _register(api_client, "ai-iso-a@example.com")
    content_id = _generate(api_client, token, workspace_id).json()["id"]

    other_token, other_workspace = _register(api_client, "ai-iso-b@example.com")

    # The other workspace's history is empty...
    assert (
        api_client.get(f"{AI}/{other_workspace}/history", headers=_bearer(other_token)).json()
        == []
    )
    # ...and its suggestions cannot be accepted through the other workspace path.
    assert (
        api_client.post(
            f"{AI}/{other_workspace}/history/{content_id}/accept", headers=_bearer(other_token)
        ).status_code
        == 404
    )
    # The owner cannot reach the other workspace either.
    assert (
        api_client.get(f"{AI}/{other_workspace}/capability", headers=_bearer(token)).status_code
        == 404
    )


def test_generating_for_an_unknown_post_is_not_found(
    api_client, db, ai_configured, monkeypatch
) -> None:
    _install_transport(monkeypatch, _completion())
    token, workspace_id = _register(api_client, "ai-post-a@example.com")

    # ``get_post`` resolves the reference through ``TenantMixin.scoped``, so a
    # post id from any other workspace is indistinguishable from a made-up one.
    response = _generate(api_client, token, workspace_id, post_id=str(uuid.uuid4()))

    assert response.status_code == 404
    assert db.scalars(select(AIGeneratedContent)).all() == []


def test_viewer_cannot_use_the_assistant(api_client, db) -> None:
    owner_token, workspace_id = _register(api_client, "ai-rbac-owner@example.com")

    viewer_token, _ = _register(api_client, "ai-rbac-viewer@example.com")
    viewer = db.scalar(select(User).where(User.email == "ai-rbac-viewer@example.com"))
    _add_membership(db, workspace_id, viewer.id, "viewer")

    # A viewer has no ai:generate, so even reading the capability is forbidden.
    assert (
        api_client.get(f"{AI}/{workspace_id}/capability", headers=_bearer(viewer_token)).status_code
        == 403
    )
    assert (
        api_client.post(
            f"{AI}/{workspace_id}/generate",
            json={"kind": "caption", "brief": "hi"},
            headers=_bearer(viewer_token),
        ).status_code
        == 403
    )
    del owner_token


def test_an_editor_may_use_the_assistant(api_client, db, ai_configured, monkeypatch) -> None:
    handler = _completion("Editor caption")
    _install_transport(monkeypatch, handler)
    owner_token, workspace_id = _register(api_client, "ai-editor-owner@example.com")
    del owner_token

    editor_token, _ = _register(api_client, "ai-editor@example.com")
    editor = db.scalar(select(User).where(User.email == "ai-editor@example.com"))
    _add_membership(db, workspace_id, editor.id, "editor")

    assert _generate(api_client, editor_token, workspace_id).status_code == 201


def test_ai_endpoints_require_authentication(api_client, db) -> None:
    token, workspace_id = _register(api_client, "ai-anon@example.com")
    del token

    assert api_client.get(f"{AI}/{workspace_id}/capability").status_code == 401
    assert (
        api_client.post(
            f"{AI}/{workspace_id}/generate", json={"kind": "caption", "brief": "hi"}
        ).status_code
        == 401
    )
    assert api_client.get(f"{AI}/{workspace_id}/history").status_code == 401


# ---------------------------------------------------------------------------
# Service + provider level
# ---------------------------------------------------------------------------
def test_capability_reflects_the_configured_model(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "openai_model", "gpt-4o-mini")
    assert ai_service.capability().configured is False

    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "openai_model", "gpt-5-mini")
    described = ai_service.capability()
    assert described.configured is True
    assert described.model == "gpt-5-mini"


def test_the_provider_rejects_a_missing_key_before_calling_out(monkeypatch) -> None:
    from app.services.ai.base import AINotConfiguredError, CompletionRequest

    monkeypatch.setattr(settings, "openai_api_key", "")
    with pytest.raises(AINotConfiguredError):
        get_provider().complete(CompletionRequest(system="s", prompt="p"))


def test_an_empty_completion_is_a_provider_error(monkeypatch, ai_configured) -> None:
    from app.services.ai.base import AIProviderError, CompletionRequest

    _install_transport(monkeypatch, _completion(""))
    with pytest.raises(AIProviderError):
        get_provider().complete(CompletionRequest(system="s", prompt="p"))


def test_a_non_json_response_is_transient(monkeypatch, ai_configured) -> None:
    from app.services.ai.base import AITransientError, CompletionRequest

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    _install_transport(monkeypatch, handler)
    with pytest.raises(AITransientError):
        get_provider().complete(CompletionRequest(system="s", prompt="p"))
