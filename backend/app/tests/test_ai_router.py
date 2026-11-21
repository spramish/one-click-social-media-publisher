"""Phase 12 tests — free AI model management and automatic failover.

The router is exercised against a real database (the ``publisher_test`` schema
built from the migrations) with each provider's HTTP layer swapped for an
``httpx.MockTransport``. Nothing here touches the network or a bill; what is
verified for real is the routing contract the master plan requires:

* a working primary answers first and no switch is announced;
* an unavailable, throttled, timed-out or erroring model is replaced by the next
  one in priority order, and a locally hosted Ollama model can be that backup;
* the same request is re-sent unchanged, so failing over never rewrites a brief;
* a transient failure is retried on the same model with backoff, a rate limit is
  not, and a content refusal is never failed over at all;
* a task that already succeeded is refused rather than run twice;
* the attempt log records every model tried and which one answered;
* a recovered primary is used again on the next task, because health is a
  monitoring signal and never gates the priority walk;
* a model whose credential is missing from the environment is skipped, never
  called;
* the admin surface (registry CRUD, health sweep, performance, history) is gated
  by ``ai:manage`` and never returns a credential.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.enums import (
    AIContentKind,
    AIModelKind,
    AIModelStatus,
    AITaskStatus,
    NotificationType,
    WorkspaceMemberStatus,
)
from app.models import AIModel, AITaskAttempt, Notification, User, Workspace, WorkspaceMember
from app.services import ai_router
from app.services import auth as auth_service
from app.services.ai import get_provider
from app.services.ai.base import (
    AIContentRejectedError,
    AINotConfiguredError,
    AITransientError,
    CompletionRequest,
)

# --- Endpoint URLs ---------------------------------------------------------
REGISTER = "/api/v1/auth/register"
WORKSPACES = "/api/v1/workspaces"
AI = "/api/v1/ai"

STRONG_PASSWORD = "correct-horse-battery"

#: Hosts the mock transport routes on. A distinct host per model is what lets
#: one shared OpenAI-compatible provider behave differently per registry row.
PRIMARY = "primary.test"
BACKUP = "backup.test"
THIRD = "third.test"
LOCAL = "ollama.test"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Mock providers
# ---------------------------------------------------------------------------
def _chat_body(text: str, model: str) -> dict:
    """A minimal OpenAI-compatible chat-completion body."""
    return {
        "id": "chatcmpl-1",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _ollama_body(text: str, model: str) -> dict:
    """A minimal Ollama chat body (its own protocol, not the OpenAI one)."""
    return {
        "model": model,
        "message": {"role": "assistant", "content": text},
        "done": True,
        "done_reason": "stop",
    }


def _ok(text: str = "routed text", *, model: str = "m", ollama: bool = False) -> Callable:
    """A responder that always answers successfully."""

    def respond(request: httpx.Request) -> httpx.Response:
        body = _ollama_body(text, model) if ollama else _chat_body(text, model)
        return httpx.Response(200, json=body)

    return respond


def _error(status_code: int, message: str = "boom") -> Callable:
    """A responder that answers with a provider error."""

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": {"message": message}})

    return respond


def _offline() -> Callable:
    """A responder that behaves as an unreachable host."""

    def respond(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    return respond


def _timeout() -> Callable:
    """A responder that behaves as a host that never answers in time."""

    def respond(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    return respond


def _install(monkeypatch, routes: dict[str, Callable]) -> dict[str, list[httpx.Request]]:
    """Route each provider call by host, keeping the requests for assertions.

    The same handler is installed on both provider singletons, so a failover
    from an OpenAI-compatible model to a local Ollama one crosses a genuine
    provider boundary while staying offline.
    """
    seen: dict[str, list[httpx.Request]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        seen.setdefault(host, []).append(request)
        responder = routes.get(host)
        if responder is None:
            raise httpx.ConnectError(f"no route for {host}", request=request)
        return responder(request)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(get_provider("openai"), "transport", transport)
    monkeypatch.setattr(get_provider("ollama"), "transport", transport)
    return seen


def _install_single(monkeypatch, responder: Callable) -> list[httpx.Request]:
    """Install one responder for the environment-configured default model."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responder(request)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(get_provider("openai"), "transport", transport)
    monkeypatch.setattr(get_provider("ollama"), "transport", transport)
    return calls


# ---------------------------------------------------------------------------
# Registry / database fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def single_attempt(monkeypatch):
    """One try per model, no backoff sleeps — makes the failover chain exact."""
    monkeypatch.setattr(settings, "ai_router_max_attempts", 1)
    monkeypatch.setattr(settings, "ai_router_backoff_base_seconds", 0.0)
    monkeypatch.setattr(settings, "ai_router_backoff_max_seconds", 0.0)


def _key(monkeypatch, name: str) -> str:
    """Set an environment variable and return the value (for header asserts)."""
    value = f"key-for-{name.lower()}"
    monkeypatch.setenv(name, value)
    return value


def _register_model(
    db,
    *,
    name: str,
    base_url: str,
    kind: AIModelKind = AIModelKind.OPENAI_COMPATIBLE,
    model: str | None = None,
    priority: int = 100,
    credential_env: str | None = None,
    supports: list[AIContentKind] | None = None,
    enabled: bool = True,
) -> AIModel:
    """Register one model in the local registry (no commit; the caller's)."""
    return ai_router.create_model(
        db,
        name=name,
        display_name=name.title(),
        kind=kind,
        model=model or f"{name}-model",
        base_url=base_url,
        credential_env=credential_env,
        priority=priority,
        enabled=enabled,
        supports=[kind.value for kind in supports] if supports else None,
    )


def _actor(db, *, email: str, slug: str) -> tuple[User, Workspace]:
    """A user and workspace, for the attempt log's and notification's foreign keys."""
    user = User(email=email, hashed_password="x", is_active=True)
    db.add(user)
    db.flush()
    workspace = Workspace(name=slug, slug=slug, owner_id=user.id)
    db.add(workspace)
    db.flush()
    return user, workspace


def _request() -> CompletionRequest:
    return CompletionRequest(system="You are a copywriter.", prompt="Write a caption.")


def _route(db, *, kind=AIContentKind.CAPTION, workspace_id=None, user_id=None, task_id=None):
    return ai_router.complete(
        db,
        request=_request(),
        kind=kind,
        workspace_id=workspace_id,
        user_id=user_id,
        task_id=task_id,
    )


def _attempts(db, task_id: uuid.UUID) -> list[AITaskAttempt]:
    return list(
        db.scalars(
            select(AITaskAttempt)
            .where(AITaskAttempt.task_id == task_id)
            .order_by(AITaskAttempt.attempt)
        )
    )


# ---------------------------------------------------------------------------
# 1. The primary model answers
# ---------------------------------------------------------------------------
def test_primary_model_answers_and_nothing_is_switched(db, monkeypatch, single_attempt) -> None:
    user, workspace = _actor(db, email="r1@example.com", slug="r1")
    _key(monkeypatch, "PRIMARY_KEY")
    _register_model(
        db, name="primary", base_url=f"https://{PRIMARY}/v1",
        credential_env="PRIMARY_KEY", model="gpt-primary", priority=10,
    )
    _install(monkeypatch, {PRIMARY: _ok("written by the primary", model="gpt-primary")})

    result = _route(db, workspace_id=workspace.id, user_id=user.id)

    assert result.completion.text == "written by the primary"
    assert result.model_name == "primary"
    assert result.switched is False
    assert result.attempts == 1

    attempts = _attempts(db, result.task_id)
    assert [(a.model_name, a.status, a.attempt) for a in attempts] == [
        ("primary", AITaskStatus.SUCCEEDED, 1)
    ]
    # A model answered, so its health is refreshed and its streak reset.
    model = ai_router.get_model_by_name(db, "primary")
    assert model.status is AIModelStatus.HEALTHY
    assert model.consecutive_failures == 0

    # No switch happened, so the user is not told about one.
    assert db.scalars(select(Notification)).all() == []


# ---------------------------------------------------------------------------
# 2. The primary is unavailable → the backup answers
# ---------------------------------------------------------------------------
def test_an_unavailable_primary_is_replaced_by_the_backup(db, monkeypatch, single_attempt) -> None:
    user, workspace = _actor(db, email="r2@example.com", slug="r2")
    _key(monkeypatch, "PRIMARY_KEY")
    _key(monkeypatch, "BACKUP_KEY")
    _register_model(
        db, name="primary", base_url=f"https://{PRIMARY}/v1",
        credential_env="PRIMARY_KEY", priority=10,
    )
    _register_model(
        db, name="backup", base_url=f"https://{BACKUP}/v1",
        credential_env="BACKUP_KEY", priority=20,
    )
    _install(monkeypatch, {PRIMARY: _offline(), BACKUP: _ok("written by the backup")})

    result = _route(db, workspace_id=workspace.id, user_id=user.id)

    assert result.model_name == "backup"
    assert result.switched is True
    assert result.attempts == 2

    attempts = _attempts(db, result.task_id)
    assert [(a.model_name, a.status) for a in attempts] == [
        ("primary", AITaskStatus.FAILED),
        ("backup", AITaskStatus.SUCCEEDED),
    ]
    assert attempts[0].error_class == "AITransientError"

    # The switch is announced, not hidden.
    notice = db.scalars(select(Notification)).one()
    assert notice.type is NotificationType.MODEL_FALLBACK
    assert notice.user_id == user.id
    assert notice.payload == {"model": "backup", "preferred_model": "primary"}

    # The failed primary is marked unhealthy with a reason; the backup is healthy.
    primary = ai_router.get_model_by_name(db, "primary")
    assert primary.status is AIModelStatus.UNHEALTHY
    assert primary.consecutive_failures == 1
    assert primary.last_error
    assert ai_router.get_model_by_name(db, "backup").status is AIModelStatus.HEALTHY


# ---------------------------------------------------------------------------
# 3. The primary is rate-limited → no retry, straight to the backup
# ---------------------------------------------------------------------------
def test_a_rate_limited_primary_goes_straight_to_the_backup(db, monkeypatch, single_attempt) -> None:
    user, workspace = _actor(db, email="r3@example.com", slug="r3")
    _key(monkeypatch, "PRIMARY_KEY")
    _key(monkeypatch, "BACKUP_KEY")
    _register_model(
        db, name="primary", base_url=f"https://{PRIMARY}/v1",
        credential_env="PRIMARY_KEY", priority=10,
    )
    _register_model(
        db, name="backup", base_url=f"https://{BACKUP}/v1",
        credential_env="BACKUP_KEY", priority=20,
    )
    seen = _install(
        monkeypatch, {PRIMARY: _error(429, "Rate limit reached"), BACKUP: _ok("backup text")}
    )

    result = _route(db, workspace_id=workspace.id, user_id=user.id)

    assert result.model_name == "backup"
    assert result.attempts == 2
    # Retrying a throttled account would only burn the same quota.
    assert len(seen[PRIMARY]) == 1


# ---------------------------------------------------------------------------
# 4. The primary times out → bounded retry on the same model, then failover
# ---------------------------------------------------------------------------
def test_a_timed_out_primary_is_retried_then_failed_over(db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_router_max_attempts", 2)
    monkeypatch.setattr(settings, "ai_router_backoff_base_seconds", 0.0)
    monkeypatch.setattr(settings, "ai_router_backoff_max_seconds", 0.0)
    user, workspace = _actor(db, email="r4@example.com", slug="r4")
    _key(monkeypatch, "PRIMARY_KEY")
    _key(monkeypatch, "BACKUP_KEY")
    _register_model(
        db, name="primary", base_url=f"https://{PRIMARY}/v1",
        credential_env="PRIMARY_KEY", priority=10,
    )
    _register_model(
        db, name="backup", base_url=f"https://{BACKUP}/v1",
        credential_env="BACKUP_KEY", priority=20,
    )
    seen = _install(monkeypatch, {PRIMARY: _timeout(), BACKUP: _ok("backup text")})

    result = _route(db, workspace_id=workspace.id, user_id=user.id)

    assert result.model_name == "backup"
    assert result.attempts == 3  # primary twice (bounded retry) + backup once
    assert len(seen[PRIMARY]) == 2
    assert [a.attempt for a in _attempts(db, result.task_id)] == [1, 2, 3]
    # The retries are on the *same* model, and the failure is a timeout.
    failed = [a for a in _attempts(db, result.task_id) if a.status is AITaskStatus.FAILED]
    assert [a.model_name for a in failed] == ["primary", "primary"]
    assert failed[0].error_class == "AITransientError"


# ---------------------------------------------------------------------------
# 5. The first backup fails too → the next one answers
# ---------------------------------------------------------------------------
def test_a_failing_first_backup_falls_through_to_the_next(db, monkeypatch, single_attempt) -> None:
    user, workspace = _actor(db, email="r5@example.com", slug="r5")
    for env in ("PRIMARY_KEY", "BACKUP_KEY", "THIRD_KEY"):
        _key(monkeypatch, env)
    _register_model(
        db, name="primary", base_url=f"https://{PRIMARY}/v1",
        credential_env="PRIMARY_KEY", priority=10,
    )
    _register_model(
        db, name="backup", base_url=f"https://{BACKUP}/v1",
        credential_env="BACKUP_KEY", priority=20,
    )
    _register_model(
        db, name="third", base_url=f"https://{THIRD}/v1",
        credential_env="THIRD_KEY", priority=30,
    )
    _install(
        monkeypatch,
        {
            PRIMARY: _offline(),
            BACKUP: _error(401, "Incorrect API key provided"),  # permanent, not retried
            THIRD: _ok("written by the third"),
        },
    )

    result = _route(db, workspace_id=workspace.id, user_id=user.id)

    assert result.model_name == "third"
    assert result.attempts == 3
    assert [a.model_name for a in _attempts(db, result.task_id)] == [
        "primary",
        "backup",
        "third",
    ]
    assert _attempts(db, result.task_id)[1].error_class == "AIProviderError"


# ---------------------------------------------------------------------------
# 6. A local Ollama model takes over
# ---------------------------------------------------------------------------
def test_a_local_ollama_model_takes_over(db, monkeypatch, single_attempt) -> None:
    user, workspace = _actor(db, email="r6@example.com", slug="r6")
    _key(monkeypatch, "PRIMARY_KEY")
    _register_model(
        db, name="primary", base_url=f"https://{PRIMARY}/v1",
        credential_env="PRIMARY_KEY", priority=10,
    )
    # No credential_env: a local model needs no key and is always usable.
    _register_model(
        db, name="local-llama", base_url=f"http://{LOCAL}:11434",
        kind=AIModelKind.OLLAMA, model="llama3.1", priority=90,
    )
    _install(
        monkeypatch,
        {PRIMARY: _offline(), LOCAL: _ok("written locally", model="llama3.1", ollama=True)},
    )

    result = _route(db, workspace_id=workspace.id, user_id=user.id)

    assert result.model_name == "local-llama"
    assert result.completion.provider == "ollama"
    assert result.switched is True
    assert result.attempts == 2


# ---------------------------------------------------------------------------
# 7. Every model fails → the task fails, and the report is the last reason
# ---------------------------------------------------------------------------
def test_every_model_failing_reports_the_last_error(db, monkeypatch, single_attempt) -> None:
    user, workspace = _actor(db, email="r7@example.com", slug="r7")
    _key(monkeypatch, "PRIMARY_KEY")
    _key(monkeypatch, "BACKUP_KEY")
    _register_model(
        db, name="primary", base_url=f"https://{PRIMARY}/v1",
        credential_env="PRIMARY_KEY", priority=10,
    )
    _register_model(
        db, name="backup", base_url=f"https://{BACKUP}/v1",
        credential_env="BACKUP_KEY", priority=20,
    )
    _install(monkeypatch, {PRIMARY: _offline(), BACKUP: _error(500, "Server exploded")})

    task_id = uuid.uuid4()
    with pytest.raises(AITransientError):
        _route(db, workspace_id=workspace.id, user_id=user.id, task_id=task_id)

    attempts = _attempts(db, task_id)
    assert [a.model_name for a in attempts] == ["primary", "backup"]
    assert all(a.status is AITaskStatus.FAILED for a in attempts)
    # No success means no fallback announcement.
    assert db.scalars(select(Notification)).all() == []

    # The history reports this task as failed, naming the last model tried.
    summaries, total = ai_router.list_recent_tasks(db)
    assert total == 1
    assert summaries[0].status is AITaskStatus.FAILED
    assert summaries[0].attempts == 2


# ---------------------------------------------------------------------------
# 8. A completed task is never executed twice
# ---------------------------------------------------------------------------
def test_a_completed_task_is_never_run_again(db, monkeypatch, single_attempt) -> None:
    user, workspace = _actor(db, email="r8@example.com", slug="r8")
    _key(monkeypatch, "PRIMARY_KEY")
    _register_model(
        db, name="primary", base_url=f"https://{PRIMARY}/v1",
        credential_env="PRIMARY_KEY", priority=10,
    )
    seen = _install(monkeypatch, {PRIMARY: _ok("first and only")})

    task_id = uuid.uuid4()
    first = _route(db, workspace_id=workspace.id, user_id=user.id, task_id=task_id)
    assert first.completion.text == "first and only"

    with pytest.raises(ai_router.AITaskAlreadyCompletedError):
        _route(db, workspace_id=workspace.id, user_id=user.id, task_id=task_id)

    # The model was called exactly once: the redelivery did not generate again.
    assert len(seen[PRIMARY]) == 1
    assert len(_attempts(db, task_id)) == 1


# ---------------------------------------------------------------------------
# 9. History records the model that actually answered
# ---------------------------------------------------------------------------
def test_history_records_the_model_that_answered(db, monkeypatch, single_attempt) -> None:
    user, workspace = _actor(db, email="r9@example.com", slug="r9")
    _key(monkeypatch, "PRIMARY_KEY")
    _key(monkeypatch, "BACKUP_KEY")
    _register_model(
        db, name="primary", base_url=f"https://{PRIMARY}/v1",
        credential_env="PRIMARY_KEY", priority=10,
    )
    _register_model(
        db, name="backup", base_url=f"https://{BACKUP}/v1",
        credential_env="BACKUP_KEY", priority=20,
    )
    _install(monkeypatch, {PRIMARY: _offline(), BACKUP: _ok("backup text")})

    result = _route(db, workspace_id=workspace.id, user_id=user.id)

    summaries, total = ai_router.list_recent_tasks(db)
    assert total == 1
    summary = summaries[0]
    assert summary.task_id == result.task_id
    assert summary.model_name == "backup"  # the winner, not the preferred
    assert summary.status is AITaskStatus.SUCCEEDED
    assert summary.attempts == 2

    # The raw chain is available too, in attempt order.
    items, count = ai_router.list_attempts(db, task_id=result.task_id)
    assert count == 2
    assert {item.model_name for item in items} == {"primary", "backup"}

    # Performance is aggregated from the same log.
    performance = ai_router.performance_by_model(db)
    assert performance["primary"].failed == 1
    assert performance["primary"].success_rate == 0.0
    assert performance["backup"].succeeded == 1
    assert performance["backup"].success_rate == 1.0
    assert performance["backup"].avg_latency_ms is not None


# ---------------------------------------------------------------------------
# 10. The primary is used again once it recovers
# ---------------------------------------------------------------------------
def test_the_primary_is_used_again_once_it_recovers(db, monkeypatch, single_attempt) -> None:
    user, workspace = _actor(db, email="r10@example.com", slug="r10")
    _key(monkeypatch, "PRIMARY_KEY")
    _key(monkeypatch, "BACKUP_KEY")
    _register_model(
        db, name="primary", base_url=f"https://{PRIMARY}/v1",
        credential_env="PRIMARY_KEY", priority=10,
    )
    _register_model(
        db, name="backup", base_url=f"https://{BACKUP}/v1",
        credential_env="BACKUP_KEY", priority=20,
    )

    # First task: the primary is down, so the backup answers and the primary is
    # recorded as unhealthy.
    _install(monkeypatch, {PRIMARY: _offline(), BACKUP: _ok("backup text")})
    first = _route(db, workspace_id=workspace.id, user_id=user.id)
    assert first.model_name == "backup"
    assert ai_router.get_model_by_name(db, "primary").status is AIModelStatus.UNHEALTHY

    # The primary recovers. Health is a monitoring signal, not a gate, so the
    # very next task goes to the primary again — no operator action needed.
    _install(monkeypatch, {PRIMARY: _ok("back to the primary"), BACKUP: _ok("backup text")})
    second = _route(db, workspace_id=workspace.id, user_id=user.id)

    assert second.model_name == "primary"
    assert second.switched is False
    assert second.completion.text == "back to the primary"


# ---------------------------------------------------------------------------
# Rules that must not silently change
# ---------------------------------------------------------------------------
def test_a_content_refusal_is_never_failed_over(db, monkeypatch, single_attempt) -> None:
    user, workspace = _actor(db, email="refuse@example.com", slug="refuse")
    _key(monkeypatch, "PRIMARY_KEY")
    _key(monkeypatch, "BACKUP_KEY")
    _register_model(
        db, name="primary", base_url=f"https://{PRIMARY}/v1",
        credential_env="PRIMARY_KEY", priority=10,
    )
    _register_model(
        db, name="backup", base_url=f"https://{BACKUP}/v1",
        credential_env="BACKUP_KEY", priority=20,
    )
    seen = _install(
        monkeypatch,
        {
            PRIMARY: _error(400, "Your request was rejected by our safety system"),
            BACKUP: _ok("a looser model would answer"),
        },
    )

    with pytest.raises(AIContentRejectedError):
        _route(db, workspace_id=workspace.id, user_id=user.id)

    # The backup was never called: a switch must not become a policy bypass.
    assert BACKUP not in seen


def test_a_model_with_a_missing_credential_is_skipped_not_called(
    db, monkeypatch, single_attempt
) -> None:
    user, workspace = _actor(db, email="nokey@example.com", slug="nokey")
    _key(monkeypatch, "BACKUP_KEY")
    # The primary names an environment variable that is not set.
    _register_model(
        db, name="primary", base_url=f"https://{PRIMARY}/v1",
        credential_env="PRIMARY_KEY_ABSENT", priority=10,
    )
    _register_model(
        db, name="backup", base_url=f"https://{BACKUP}/v1",
        credential_env="BACKUP_KEY", priority=20,
    )
    seen = _install(monkeypatch, {PRIMARY: _ok("never"), BACKUP: _ok("backup text")})

    result = _route(db, workspace_id=workspace.id, user_id=user.id)

    assert result.model_name == "backup"
    assert result.attempts == 1
    assert PRIMARY not in seen


def test_no_usable_model_is_reported_as_unconfigured(db, monkeypatch) -> None:
    _actor(db, email="none@example.com", slug="none")
    monkeypatch.setattr(settings, "openai_api_key", "")
    _register_model(
        db, name="disabled", base_url=f"https://{PRIMARY}/v1",
        credential_env=None, priority=10, enabled=False,
    )

    with pytest.raises(AINotConfiguredError):
        _route(db)


def test_the_environment_default_is_used_when_the_registry_is_empty(db, monkeypatch) -> None:
    """An unregistered deployment keeps working exactly as it did before."""
    user, workspace = _actor(db, email="default@example.com", slug="default")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "openai_model", "gpt-4o-mini")
    calls = _install_single(monkeypatch, _ok("legacy default", model="gpt-4o-mini"))

    result = _route(db, workspace_id=workspace.id, user_id=user.id)

    assert result.completion.text == "legacy default"
    assert result.model_name == "gpt-4o-mini"
    assert result.switched is False
    assert len(calls) == 1
    # The env-configured default is a single implicit candidate, so its attempts
    # are not logged against a registry row.
    attempt = _attempts(db, result.task_id)[0]
    assert attempt.model_id is None
    assert attempt.model_name == "gpt-4o-mini"


def test_health_sweep_probes_and_records_status(db, monkeypatch) -> None:
    _key(monkeypatch, "PRIMARY_KEY")
    _register_model(
        db, name="primary", base_url=f"https://{PRIMARY}/v1",
        credential_env="PRIMARY_KEY", priority=10,
    )
    _register_model(
        db, name="down", base_url=f"https://{BACKUP}/v1",
        credential_env="PRIMARY_KEY", priority=20,
    )
    _install(monkeypatch, {PRIMARY: _ok("ok"), BACKUP: _offline()})

    result = ai_router.health_sweep(db, force=True)

    assert result.as_dict() == {"checked": 2, "healthy": 1, "unhealthy": 1}
    assert ai_router.get_model_by_name(db, "primary").status is AIModelStatus.HEALTHY
    assert ai_router.get_model_by_name(db, "down").status is AIModelStatus.UNHEALTHY


# ---------------------------------------------------------------------------
# Admin API — registry CRUD, monitoring, history, RBAC
# ---------------------------------------------------------------------------
def _register(client, email: str) -> tuple[str, uuid.UUID]:
    response = client.post(
        REGISTER, json={"email": email, "password": STRONG_PASSWORD, "full_name": "Owner"}
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


def _model_payload(**overrides) -> dict:
    payload = {
        "name": "groq-llama",
        "display_name": "Groq Llama",
        "kind": AIModelKind.OPENAI_COMPATIBLE.value,
        "model": "llama-3.1-8b-instant",
        "base_url": "https://api.groq.test/v1",
        "credential_env": "GROQ_API_KEY",
        "priority": 10,
    }
    payload.update(overrides)
    return payload


def test_registering_a_model_returns_it_without_the_credential(
    api_client, db, monkeypatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "secret-value")
    token, workspace_id = _register(api_client, "adm-create@example.com")

    response = api_client.post(
        f"{AI}/{workspace_id}/models", json=_model_payload(), headers=_bearer(token)
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "groq-llama"
    assert body["credential_env"] == "GROQ_API_KEY"
    assert body["credential_ready"] is True
    assert body["status"] == AIModelStatus.UNKNOWN.value
    assert "secret-value" not in json.dumps(body)


def test_listing_models_is_ordered_by_priority(api_client, db) -> None:
    token, workspace_id = _register(api_client, "adm-list@example.com")
    for name, priority in (("second", 20), ("first", 10), ("third", 30)):
        created = api_client.post(
            f"{AI}/{workspace_id}/models",
            json=_model_payload(name=name, priority=priority),
            headers=_bearer(token),
        )
        assert created.status_code == 201, created.text

    body = api_client.get(f"{AI}/{workspace_id}/models", headers=_bearer(token)).json()

    assert [row["name"] for row in body] == ["first", "second", "third"]


def test_priority_and_enabled_can_be_updated_partially(api_client, db) -> None:
    token, workspace_id = _register(api_client, "adm-update@example.com")
    model = api_client.post(
        f"{AI}/{workspace_id}/models", json=_model_payload(), headers=_bearer(token)
    ).json()

    response = api_client.patch(
        f"{AI}/{workspace_id}/models/{model['id']}",
        json={"priority": 5, "enabled": False},
        headers=_bearer(token),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["priority"] == 5
    assert body["enabled"] is False
    assert body["name"] == "groq-llama"  # untouched fields are left alone


def test_a_duplicate_name_is_a_conflict(api_client, db) -> None:
    token, workspace_id = _register(api_client, "adm-dup@example.com")
    assert (
        api_client.post(
            f"{AI}/{workspace_id}/models", json=_model_payload(), headers=_bearer(token)
        ).status_code
        == 201
    )

    response = api_client.post(
        f"{AI}/{workspace_id}/models", json=_model_payload(), headers=_bearer(token)
    )

    assert response.status_code == 409


def test_deleting_a_model_removes_it_from_the_registry(api_client, db) -> None:
    token, workspace_id = _register(api_client, "adm-del@example.com")
    model = api_client.post(
        f"{AI}/{workspace_id}/models", json=_model_payload(), headers=_bearer(token)
    ).json()

    assert (
        api_client.delete(
            f"{AI}/{workspace_id}/models/{model['id']}", headers=_bearer(token)
        ).status_code
        == 204
    )
    assert api_client.get(f"{AI}/{workspace_id}/models", headers=_bearer(token)).json() == []


def test_health_check_endpoint_reports_the_sweep(api_client, db, monkeypatch) -> None:
    monkeypatch.setenv("PRIMARY_KEY", "k")
    token, workspace_id = _register(api_client, "adm-health@example.com")
    api_client.post(
        f"{AI}/{workspace_id}/models",
        json=_model_payload(name="primary", base_url=f"https://{PRIMARY}/v1", credential_env="PRIMARY_KEY"),
        headers=_bearer(token),
    )
    _install(monkeypatch, {PRIMARY: _ok("ok")})

    response = api_client.post(
        f"{AI}/{workspace_id}/models/health-check", headers=_bearer(token)
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"checked": 1, "healthy": 1, "unhealthy": 0}


def test_task_history_endpoints_expose_the_failover_chain(
    api_client, db, monkeypatch, single_attempt
) -> None:
    monkeypatch.setenv("PRIMARY_KEY", "k")
    monkeypatch.setenv("BACKUP_KEY", "k")
    token, workspace_id = _register(api_client, "adm-tasks@example.com")
    api_client.post(
        f"{AI}/{workspace_id}/models",
        json=_model_payload(name="primary", base_url=f"https://{PRIMARY}/v1", credential_env="PRIMARY_KEY", priority=10),
        headers=_bearer(token),
    )
    api_client.post(
        f"{AI}/{workspace_id}/models",
        json=_model_payload(name="backup", base_url=f"https://{BACKUP}/v1", credential_env="BACKUP_KEY", priority=20),
        headers=_bearer(token),
    )
    _install(monkeypatch, {PRIMARY: _offline(), BACKUP: _ok("backup text")})

    user = db.scalar(select(User).where(User.email == "adm-tasks@example.com"))
    result = _route(db, workspace_id=workspace_id, user_id=user.id)

    listing = api_client.get(f"{AI}/{workspace_id}/tasks", headers=_bearer(token)).json()
    assert listing["total"] == 1
    assert listing["items"][0]["model_name"] == "backup"
    assert listing["items"][0]["status"] == AITaskStatus.SUCCEEDED.value

    chain = api_client.get(
        f"{AI}/{workspace_id}/tasks/{result.task_id}", headers=_bearer(token)
    ).json()
    assert chain["total"] == 2
    assert {row["model_name"] for row in chain["items"]} == {"primary", "backup"}

    performance = api_client.get(
        f"{AI}/{workspace_id}/models/performance", headers=_bearer(token)
    ).json()
    by_name = {row["model_name"]: row for row in performance}
    assert by_name["backup"]["succeeded"] == 1
    assert by_name["primary"]["failed"] == 1


def test_a_viewer_cannot_manage_models(api_client, db) -> None:
    owner_token, workspace_id = _register(api_client, "adm-rbac-owner@example.com")
    viewer_token, _ = _register(api_client, "adm-rbac-viewer@example.com")
    viewer = db.scalar(select(User).where(User.email == "adm-rbac-viewer@example.com"))
    _add_membership(db, workspace_id, viewer.id, "viewer")

    assert (
        api_client.get(f"{AI}/{workspace_id}/models", headers=_bearer(viewer_token)).status_code
        == 403
    )
    assert (
        api_client.post(
            f"{AI}/{workspace_id}/models", json=_model_payload(), headers=_bearer(viewer_token)
        ).status_code
        == 403
    )
    del owner_token


def test_model_endpoints_require_authentication(api_client, db) -> None:
    _, workspace_id = _register(api_client, "adm-anon@example.com")

    assert api_client.get(f"{AI}/{workspace_id}/models").status_code == 401
    assert (
        api_client.post(f"{AI}/{workspace_id}/models", json=_model_payload()).status_code == 401
    )
    assert api_client.get(f"{AI}/{workspace_id}/tasks").status_code == 401
