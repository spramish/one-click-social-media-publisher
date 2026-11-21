"""Free AI model management and automatic failover (Phase 12).

This module is the second half of the assistant. :mod:`app.services.ai_content`
owns the *prompt* (what to write); this module owns the *delivery* (which model
writes it, and what happens when that model cannot).

The routing rules, in one place so they cannot drift:

* **Priority order is the failover order.** Enabled models are walked from the
  lowest ``priority`` upward. The head of that list is the primary; every other
  entry is a backup, and a locally hosted Ollama model is simply the entry an
  operator placed last.
* **The same request is re-sent unchanged.** Failover re-issues the *identical*
  :class:`~app.services.ai.base.CompletionRequest`, so the original instructions
  and the required output format are never altered by a switch. The router never
  rewrites a brief to suit a weaker model.
* **Only a model-local failure causes a switch.** A timeout, a rate limit, a 5xx,
  a rejected key or a malformed response is that model's problem, so the next
  model is tried. A *content refusal* is the user's brief, not the model, so it
  is re-raised immediately: quietly trying a looser model would be a policy
  bypass dressed up as resilience.
* **Retries are bounded and backed off.** A transient failure is retried on the
  same model up to ``AI_ROUTER_MAX_ATTEMPTS`` times with exponential backoff; a
  rate limit skips the retry entirely (retrying a throttled account only burns
  the same quota). Either way the router then moves on.
* **Every attempt is recorded.** One append-only ``ai_task_attempts`` row per
  call is the usage log, the execution trail, the task history and the
  performance sample at once, and it carries the ``task_id`` shared by every
  attempt of one routed task.
* **A task is never executed twice.** Given a ``task_id`` that already has a
  successful attempt, :func:`complete` refuses to run again — the guard that
  makes a redelivered background job a no-op instead of a second generation.
* **A switch is announced, not hidden.** When a backup finishes the work, the
  user gets a ``model_fallback`` notification naming the model that stepped in.
* **Failure is reported only after everyone failed.** The last model's error is
  what propagates, so the caller's existing status-code mapping still applies.

Availability *monitoring* is deliberately non-blocking: the router always walks
the whole list in priority order, which is exactly how the primary resumes the
moment it works again. The ``status``/``consecutive_failures`` columns the router
and :func:`health_sweep` maintain are a monitoring signal for operators, never a
gate that could pin traffic to a backup.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Select, desc, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import utcnow
from app.db.enums import (
    AIContentKind,
    AIModelKind,
    AIModelStatus,
    AITaskStatus,
    NotificationType,
)
from app.models.ai_model import AIModel, AITaskAttempt
from app.services import notifications as notifications_service
from app.services.ai import DEFAULT_PROVIDER, get_provider
from app.services.ai.base import (
    AICompletion,
    AIContentRejectedError,
    AIError,
    AINotConfiguredError,
    AIProvider,
    AIProviderError,
    AIRateLimitError,
    AITransientError,
    CompletionRequest,
    ModelSpec,
)

logger = get_logger(__name__)

#: Which transport serves each model kind. ``openai_compatible`` covers every
#: gateway that speaks the chat-completions protocol; ``ollama`` is the local
#: server. Adding a family is one registry line plus one provider.
_KIND_PROVIDER: dict[AIModelKind, str] = {
    AIModelKind.OPENAI_COMPATIBLE: "openai",
    AIModelKind.OLLAMA: "ollama",
}

#: Longest stored error string. Enough to diagnose, short enough to keep the log
#: table cheap and to bound what a provider can put in a column.
_MAX_ERROR_CHARS = 500

#: The probe a health check sends. Deliberately trivial: the question is "can
#: this model answer at all", not "is it any good".
_HEALTH_SYSTEM = "You are a health probe. Answer with a single word."
_HEALTH_PROMPT = "Reply with the single word: ok"
_HEALTH_MAX_TOKENS = 8


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class AIRouterError(AIProviderError):
    """The router could not complete the task with any configured model.

    Subclasses :class:`~app.services.ai.base.AIProviderError` so the existing API
    error mapping keeps working: an exhausted router is a gateway failure.
    """


class AITaskAlreadyCompletedError(AIRouterError):
    """A task with this id already succeeded, so it must not run again."""


class AIModelError(Exception):
    """Base class for model-registry failures."""


class AIModelNotFoundError(AIModelError):
    """No such model in the registry."""


class AIModelConflictError(AIModelError):
    """A model with this name (or slug) already exists."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class _Candidate:
    """One model the router may call, resolved into a provider + spec."""

    model_id: uuid.UUID | None
    model_name: str
    provider: AIProvider
    #: ``None`` means "read ``settings``" — the single environment-configured
    #: model, which is the whole registry when no rows exist.
    spec: ModelSpec | None
    is_env_default: bool


@dataclass(slots=True)
class RoutedCompletion:
    """The outcome of one routed task, including how it got there."""

    completion: AICompletion
    model_name: str
    model_id: uuid.UUID | None
    #: How many model calls were made in total (retries included).
    attempts: int
    #: True when a model other than the first candidate produced the result.
    switched: bool
    task_id: uuid.UUID


@dataclass(slots=True)
class ModelPerformance:
    """Aggregated success rate and latency for one model name."""

    model_name: str
    total: int
    succeeded: int
    failed: int
    success_rate: float
    avg_latency_ms: int | None


@dataclass(slots=True)
class TaskSummary:
    """One routed task, collapsed from its attempts for the history view."""

    task_id: uuid.UUID
    kind: AIContentKind
    status: AITaskStatus
    attempts: int
    model_name: str
    created_at: datetime
    finished_at: datetime


@dataclass(slots=True)
class HealthSweepResult:
    """What one availability sweep did, for the worker's task result."""

    checked: int = 0
    healthy: int = 0
    unhealthy: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "checked": self.checked,
            "healthy": self.healthy,
            "unhealthy": self.unhealthy,
        }


# ---------------------------------------------------------------------------
# Registry reads
# ---------------------------------------------------------------------------
def list_models(db: Session, *, enabled_only: bool = False) -> list[AIModel]:
    """The registry, ordered the way the router walks it.

    ``priority`` ascending puts the preferred model first; ``created_at`` breaks
    ties so two models at the same priority still have a stable, explainable
    order rather than an arbitrary one.
    """
    stmt: Select = select(AIModel)
    if enabled_only:
        stmt = stmt.where(AIModel.enabled.is_(True))
    stmt = stmt.order_by(AIModel.priority.asc(), AIModel.created_at.asc())
    return list(db.scalars(stmt))


def get_model(db: Session, model_id: uuid.UUID) -> AIModel:
    """One registry entry, or raise.

    Raises:
        AIModelNotFoundError: no such model.
    """
    model = db.get(AIModel, model_id)
    if model is None:
        raise AIModelNotFoundError("That AI model is not registered.")
    return model


def get_model_by_name(db: Session, name: str) -> AIModel | None:
    """The entry with this slug, or ``None``."""
    return db.scalar(select(AIModel).where(AIModel.name == name))


# ---------------------------------------------------------------------------
# Registry writes (called by the admin API, which owns the transaction)
# ---------------------------------------------------------------------------
def create_model(
    db: Session,
    *,
    name: str,
    display_name: str,
    kind: AIModelKind,
    model: str,
    base_url: str,
    credential_env: str | None = None,
    priority: int = 100,
    enabled: bool = True,
    supports: list[str] | None = None,
    timeout_seconds: int = 60,
    max_output_tokens: int = 800,
    temperature: float = 0.8,
) -> AIModel:
    """Register one model.

    Raises:
        AIModelConflictError: the slug is already taken.
    """
    if get_model_by_name(db, name) is not None:
        raise AIModelConflictError(f"A model called '{name}' is already registered.")
    entry = AIModel(
        name=name,
        display_name=display_name,
        kind=kind,
        model=model,
        base_url=_default_base_url(kind, base_url),
        credential_env=credential_env or None,
        priority=priority,
        enabled=enabled,
        supports=supports,
        timeout_seconds=timeout_seconds,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
    )
    db.add(entry)
    db.flush()
    logger.info("ai model registered", extra={"model": entry.name, "kind": kind.value})
    return entry


def update_model(db: Session, model_id: uuid.UUID, **fields: object) -> AIModel:
    """Apply the provided fields to one model.

    ``None`` values are ignored so a partial update cannot blank a column by
    omission; clearing ``credential_env`` is done by sending an empty string.

    Raises:
        AIModelNotFoundError: no such model.
        AIModelConflictError: the new slug collides with another entry.
    """
    entry = get_model(db, model_id)
    new_name = fields.get("name")
    if isinstance(new_name, str) and new_name != entry.name:
        existing = get_model_by_name(db, new_name)
        if existing is not None and existing.id != entry.id:
            raise AIModelConflictError(f"A model called '{new_name}' is already registered.")
    for key, value in fields.items():
        if value is None:
            continue
        if key == "credential_env":
            entry.credential_env = str(value).strip() or None
            continue
        if key == "base_url":
            entry.base_url = _default_base_url(entry.kind, str(value))
            continue
        setattr(entry, key, value)
    db.flush()
    return entry


def delete_model(db: Session, model_id: uuid.UUID) -> None:
    """Remove one model from the registry.

    Its attempts are kept: ``ai_task_attempts.model_id`` is ``SET NULL`` and the
    row keeps the denormalised ``model_name``, so the history still names the
    model that answered.

    Raises:
        AIModelNotFoundError: no such model.
    """
    entry = get_model(db, model_id)
    db.delete(entry)
    db.flush()


def _default_base_url(kind: AIModelKind, base_url: str) -> str:
    """Fall back to the documented local default when a host is left blank."""
    cleaned = (base_url or "").strip()
    if cleaned:
        return cleaned
    if kind is AIModelKind.OLLAMA:
        return settings.ollama_base_url
    return settings.openai_base_url


# ---------------------------------------------------------------------------
# Candidate resolution
# ---------------------------------------------------------------------------
def credential_ready(model: AIModel) -> bool:
    """Whether this model's credential resolves right now.

    A model that needs no key (Ollama) is always ready; one that names an
    environment variable is ready only while that variable is set. The variable
    is read, never stored, so this is a live check rather than a saved flag.
    """
    if not model.credential_env:
        return True
    return bool(os.environ.get(model.credential_env, "").strip())


def _resolve_spec(model: AIModel) -> ModelSpec:
    """Build the spec for one registry row.

    The credential is read from the **process environment** at call time (the
    variable named by ``credential_env``) — it is never stored in the database
    and never written to a log, so a database dump cannot leak a key.
    """
    api_key = ""
    if model.credential_env:
        api_key = os.environ.get(model.credential_env, "").strip()
    return ModelSpec(
        name=model.name,
        kind=model.kind.value,
        model=model.model,
        base_url=model.base_url,
        api_key=api_key,
        timeout_seconds=float(model.timeout_seconds),
        max_output_tokens=model.max_output_tokens,
        temperature=model.temperature,
    )


def _candidates(db: Session, kind: AIContentKind) -> list[_Candidate]:
    """The ordered models the router may call for ``kind``.

    Three filters apply, in order: the model must be enabled, it must declare it
    can serve this ``kind`` (``supports`` is ``NULL`` for "every kind"), and it
    must have a usable credential when it needs one. A row whose
    ``credential_env`` is blank in the environment is unusable and is skipped
    rather than attempted — the router must never call a model it cannot
    authenticate.

    When the registry is empty the single environment-configured model is the
    whole candidate list, which is how the assistant behaves on a deployment
    that has not registered anything.
    """
    rows = list_models(db, enabled_only=True)
    if not rows:
        default = get_provider()
        if default.is_configured():
            return [
                _Candidate(
                    model_id=None,
                    model_name=default.model(),
                    provider=default,
                    spec=None,
                    is_env_default=True,
                )
            ]
        return []

    candidates: list[_Candidate] = []
    for model in rows:
        if not model.serves(kind):
            continue
        provider_name = _KIND_PROVIDER.get(model.kind)
        if provider_name is None:  # pragma: no cover - enum is closed
            continue
        if not credential_ready(model):
            logger.debug(
                "ai model skipped: credential not present",
                extra={"model": model.name, "credential_env": model.credential_env},
            )
            continue
        candidates.append(
            _Candidate(
                model_id=model.id,
                model_name=model.name,
                provider=get_provider(provider_name),
                spec=_resolve_spec(model),
                is_env_default=False,
            )
        )
    return candidates


def _first_usable(rows: list[AIModel]) -> AIModel | None:
    """The first row whose credential resolves (``None`` when none does)."""
    for model in rows:
        if credential_ready(model):
            return model
    return None


def describe(db: Session | None = None) -> tuple[str, str, bool]:
    """The ``(provider, model, configured)`` the assistant should advertise.

    The primary registered model wins when one is usable — that is what will
    actually answer a request — so the capability banner can never say
    "unavailable" while generation would in fact succeed. With no usable
    registered model the single environment-configured default is reported,
    which is exactly what an unregistered deployment runs on. ``db=None``
    always reports that default.
    """
    if db is not None:
        primary = _first_usable(list_models(db, enabled_only=True))
        if primary is not None:
            return _KIND_PROVIDER.get(primary.kind, DEFAULT_PROVIDER), primary.model, True
    default = get_provider()
    return default.name, default.model(), default.is_configured()


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
def complete(
    db: Session,
    *,
    request: CompletionRequest,
    kind: AIContentKind,
    workspace_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> RoutedCompletion:
    """Complete one AI task, failing over between models as needed.

    Args:
        request: The fully assembled call. It is re-sent **unchanged** to every
            model, so failing over never alters the instructions or the expected
            output format.
        kind: What is being generated; decides which models are compatible.
        workspace_id: Recorded on each attempt for audit; also the notification's
            workspace when a switch is announced.
        user_id: Who to notify when a backup model finishes the task.
        task_id: Groups the attempts. Passing the same id again after a success
            is refused instead of re-running the task.

    Returns:
        The completion plus how many model calls it took and whether a backup
        produced it.

    Raises:
        AITaskAlreadyCompletedError: this ``task_id`` already succeeded.
        AINotConfiguredError: no compatible, usable model exists.
        AIContentRejectedError: the brief was refused (never failed over).
        AIError: the last model's error, once every model has failed.
    """
    if task_id is not None and _already_succeeded(db, task_id):
        raise AITaskAlreadyCompletedError(
            "This AI task has already completed; it will not be run again."
        )
    task_id = task_id or uuid.uuid4()

    candidates = _candidates(db, kind)
    if not candidates:
        # A capability gap, worded like every other unconfigured provider.
        raise AINotConfiguredError(get_provider().display_name)

    try:
        return _route(
            db,
            request=request,
            kind=kind,
            candidates=candidates,
            task_id=task_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )
    finally:
        # The attempt log is the audit trail *and* the performance sample, so it
        # must survive the very failure it records: it is committed here rather
        # than rolled back with the request that failed.
        db.commit()


def _route(
    db: Session,
    *,
    request: CompletionRequest,
    kind: AIContentKind,
    candidates: list[_Candidate],
    task_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
) -> RoutedCompletion:
    """Walk the priority list until a model answers, logging every attempt."""
    attempt_no = 0
    last_error: AIError | None = None

    for index, candidate in enumerate(candidates):
        max_tries = 1 if candidate.is_env_default else max(1, settings.ai_router_max_attempts)
        for retry in range(max_tries):
            attempt_no += 1
            started = time.monotonic()
            try:
                completion = candidate.provider.complete(request, candidate.spec)
            except AIError as exc:
                latency = _elapsed_ms(started)
                _record_attempt(
                    db,
                    task_id=task_id,
                    candidate=candidate,
                    kind=kind,
                    status=AITaskStatus.FAILED,
                    attempt=attempt_no,
                    latency_ms=latency,
                    error=exc,
                    workspace_id=workspace_id,
                    user_id=user_id,
                )
                _mark_failure(db, candidate.model_id, exc)
                if isinstance(exc, AIContentRejectedError):
                    # The brief, not the model, was refused. Trying a different
                    # model would be a silent attempt to get a looser answer.
                    raise
                last_error = exc
                if isinstance(exc, AIRateLimitError):
                    # Throttled: retrying the same account is pointless.
                    break
                if isinstance(exc, AITransientError) and retry < max_tries - 1:
                    time.sleep(_backoff_seconds(retry))
                    continue
                break

            _record_attempt(
                db,
                task_id=task_id,
                candidate=candidate,
                kind=kind,
                status=AITaskStatus.SUCCEEDED,
                attempt=attempt_no,
                latency_ms=_elapsed_ms(started),
                error=None,
                workspace_id=workspace_id,
                user_id=user_id,
            )
            _mark_success(db, candidate.model_id)
            switched = index > 0
            if switched:
                logger.info(
                    "ai task completed by a backup model",
                    extra={
                        "task_id": str(task_id),
                        "model": candidate.model_name,
                        "preferred": candidates[0].model_name,
                        "attempts": attempt_no,
                    },
                )
                if user_id is not None:
                    _notify_fallback(
                        db,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        model_name=candidate.model_name,
                        preferred=candidates[0].model_name,
                    )
            return RoutedCompletion(
                completion=completion,
                model_name=candidate.model_name,
                model_id=candidate.model_id,
                attempts=attempt_no,
                switched=switched,
                task_id=task_id,
            )

    # Every compatible model failed. Report the last reason so the caller's
    # status mapping (503 for transient, 502 for a rejection) still holds.
    logger.warning(
        "ai task failed on every configured model",
        extra={
            "task_id": str(task_id),
            "kind": kind.value,
            "models": [candidate.model_name for candidate in candidates],
            "attempts": attempt_no,
        },
    )
    if last_error is not None:
        raise last_error
    raise AIRouterError("No configured AI model could complete this task.")  # pragma: no cover


def _already_succeeded(db: Session, task_id: uuid.UUID) -> bool:
    """Whether this task already has a successful attempt."""
    found = db.scalar(
        select(AITaskAttempt.id)
        .where(
            AITaskAttempt.task_id == task_id,
            AITaskAttempt.status == AITaskStatus.SUCCEEDED,
        )
        .limit(1)
    )
    return found is not None


def _backoff_seconds(retry: int) -> float:
    """Exponential backoff for the ``retry``-th repeat of one model (0-based)."""
    return min(
        settings.ai_router_backoff_base_seconds * (2**retry),
        settings.ai_router_backoff_max_seconds,
    )


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


# ---------------------------------------------------------------------------
# Attempt logging + model health
# ---------------------------------------------------------------------------
def _record_attempt(
    db: Session,
    *,
    task_id: uuid.UUID,
    candidate: _Candidate,
    kind: AIContentKind,
    status: AITaskStatus,
    attempt: int,
    latency_ms: int,
    error: Exception | None,
    workspace_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
) -> None:
    """Append one attempt row (the usage log, execution trail and history)."""
    db.add(
        AITaskAttempt(
            task_id=task_id,
            workspace_id=workspace_id,
            user_id=user_id,
            model_id=candidate.model_id,
            model_name=candidate.model_name,
            kind=kind,
            status=status,
            attempt=attempt,
            latency_ms=latency_ms,
            error_class=type(error).__name__ if error is not None else None,
            error=_short(str(error)) if error is not None else None,
        )
    )
    db.flush()


def _mark_success(db: Session, model_id: uuid.UUID | None, *, now: datetime | None = None) -> None:
    """A model answered: it is healthy and its failure streak is over."""
    model = db.get(AIModel, model_id) if model_id is not None else None
    if model is None:
        return
    now = now or utcnow()
    model.status = AIModelStatus.HEALTHY
    model.last_checked_at = now
    model.last_ok_at = now
    model.consecutive_failures = 0
    model.last_error = None


def _mark_failure(
    db: Session, model_id: uuid.UUID | None, error: Exception, *, now: datetime | None = None
) -> None:
    """A model failed: record why, so the registry shows a real reason."""
    model = db.get(AIModel, model_id) if model_id is not None else None
    if model is None:
        return
    now = now or utcnow()
    model.status = AIModelStatus.UNHEALTHY
    model.last_checked_at = now
    model.consecutive_failures = (model.consecutive_failures or 0) + 1
    model.last_error = _short(str(error))


def _short(text: str, limit: int = _MAX_ERROR_CHARS) -> str:
    text = text.strip()
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _notify_fallback(
    db: Session,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
    model_name: str,
    preferred: str,
) -> None:
    """Tell the user a backup model produced their content."""
    notifications_service.record(
        db,
        user_id=user_id,
        type=NotificationType.MODEL_FALLBACK,
        title="A backup AI model finished your request",
        body=(
            f"The preferred model ({preferred}) was unavailable, so “{model_name}” "
            f"wrote this for you. The text was produced normally — review it as usual."
        ),
        workspace_id=workspace_id,
        payload={"model": model_name, "preferred_model": preferred},
    )


# ---------------------------------------------------------------------------
# Health checks / availability monitoring
# ---------------------------------------------------------------------------
def check_health(db: Session, model: AIModel, *, now: datetime | None = None) -> bool:
    """Probe one model with a minimal completion and record the result.

    A disabled model, one with an unresolvable credential or one whose kind has
    no transport is reported as ``unknown``: it is not *unavailable*, it is not
    currently usable, and the operator should see that distinction.

    Returns:
        ``True`` when the model answered.
    """
    now = now or utcnow()
    provider_name = _KIND_PROVIDER.get(model.kind)
    if not model.enabled or provider_name is None or not credential_ready(model):
        model.status = AIModelStatus.UNKNOWN
        model.last_checked_at = now
        model.last_error = "This model is disabled or has no usable credential."
        db.flush()
        return False

    request = CompletionRequest(
        system=_HEALTH_SYSTEM,
        prompt=_HEALTH_PROMPT,
        max_output_tokens=_HEALTH_MAX_TOKENS,
        temperature=0.0,
    )
    try:
        get_provider(provider_name).complete(request, _resolve_spec(model))
    except AIError as exc:
        _mark_failure(db, model.id, exc, now=now)
        return False
    _mark_success(db, model.id, now=now)
    return True


def health_sweep(
    db: Session, *, now: datetime | None = None, force: bool = False
) -> HealthSweepResult:
    """Probe every enabled model whose last result has gone stale.

    Ordinary routing already refreshes a busy model's health on each call, so
    this only has to cover the idle ones. ``force`` re-probes everything, which
    is what the "check now" action uses.
    """
    now = now or utcnow()
    interval = timedelta(seconds=settings.ai_health_check_interval_seconds)
    result = HealthSweepResult()
    for model in list_models(db, enabled_only=True):
        if (
            not force
            and model.last_checked_at is not None
            and model.last_checked_at > now - interval
        ):
            continue
        result.checked += 1
        if check_health(db, model, now=now):
            result.healthy += 1
        else:
            result.unhealthy += 1
    return result


# ---------------------------------------------------------------------------
# Performance tracking
# ---------------------------------------------------------------------------
def performance_by_model(
    db: Session, *, since: datetime | None = None
) -> dict[str, ModelPerformance]:
    """Success rate and average latency per model name, from the attempt log."""
    succeeded = func.count().filter(AITaskAttempt.status == AITaskStatus.SUCCEEDED)
    stmt = select(
        AITaskAttempt.model_name,
        func.count().label("total"),
        succeeded.label("succeeded"),
        func.avg(AITaskAttempt.latency_ms).label("avg_latency"),
    ).group_by(AITaskAttempt.model_name)
    if since is not None:
        stmt = stmt.where(AITaskAttempt.created_at >= since)

    performance: dict[str, ModelPerformance] = {}
    for row in db.execute(stmt):
        total = int(row.total or 0)
        ok = int(row.succeeded or 0)
        performance[row.model_name] = ModelPerformance(
            model_name=row.model_name,
            total=total,
            succeeded=ok,
            failed=total - ok,
            success_rate=(ok / total) if total else 0.0,
            avg_latency_ms=int(row.avg_latency) if row.avg_latency is not None else None,
        )
    return performance


# ---------------------------------------------------------------------------
# Task history
# ---------------------------------------------------------------------------
def list_attempts(
    db: Session,
    *,
    task_id: uuid.UUID | None = None,
    model_name: str | None = None,
    status: AITaskStatus | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[AITaskAttempt], int]:
    """A page of raw attempts, newest first, plus the total matching the filters."""
    filters = []
    if task_id is not None:
        filters.append(AITaskAttempt.task_id == task_id)
    if model_name is not None:
        filters.append(AITaskAttempt.model_name == model_name)
    if status is not None:
        filters.append(AITaskAttempt.status == status)

    items = list(
        db.scalars(
            select(AITaskAttempt)
            .where(*filters)
            .order_by(AITaskAttempt.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    total = db.scalar(select(func.count()).select_from(AITaskAttempt).where(*filters)) or 0
    return items, total


def list_recent_tasks(
    db: Session, *, limit: int = 50, offset: int = 0
) -> tuple[list[TaskSummary], int]:
    """The most recently active routed tasks, each collapsed from its attempts.

    Grouping happens here rather than in the database because "the model that
    answered" and "whether the task needed a switch" are derived from the
    attempts themselves, and a routed task has only a handful of them.
    """
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    recent = (
        select(
            AITaskAttempt.task_id,
            func.max(AITaskAttempt.created_at).label("last_seen"),
        )
        .group_by(AITaskAttempt.task_id)
        .order_by(desc("last_seen"))
        .limit(limit)
        .offset(offset)
        .subquery()
    )
    rows = list(
        db.scalars(
            select(AITaskAttempt)
            .join(recent, AITaskAttempt.task_id == recent.c.task_id)
            .order_by(AITaskAttempt.task_id, AITaskAttempt.attempt)
        )
    )
    total = db.scalar(select(func.count(func.distinct(AITaskAttempt.task_id)))) or 0

    grouped: dict[uuid.UUID, list[AITaskAttempt]] = {}
    for attempt in rows:
        grouped.setdefault(attempt.task_id, []).append(attempt)

    summaries = [_summarise(task_id, attempts) for task_id, attempts in grouped.items()]
    # The subquery ordered the ids, but a dict does not keep that order for a
    # plain iteration guarantee across databases; sort explicitly.
    summaries.sort(key=lambda item: item.finished_at, reverse=True)
    return summaries, total


def _summarise(task_id: uuid.UUID, attempts: list[AITaskAttempt]) -> TaskSummary:
    """Collapse one task's attempts into a single history row."""
    winner = next(
        (item for item in attempts if item.status == AITaskStatus.SUCCEEDED),
        None,
    )
    last = attempts[-1]
    return TaskSummary(
        task_id=task_id,
        kind=last.kind,
        status=AITaskStatus.SUCCEEDED if winner is not None else AITaskStatus.FAILED,
        attempts=len(attempts),
        model_name=(winner or last).model_name,
        created_at=attempts[0].created_at,
        finished_at=last.created_at,
    )


__all__ = [
    "AIModelConflictError",
    "AIModelError",
    "AIModelNotFoundError",
    "AIRouterError",
    "AITaskAlreadyCompletedError",
    "HealthSweepResult",
    "ModelPerformance",
    "RoutedCompletion",
    "TaskSummary",
    "check_health",
    "complete",
    "create_model",
    "credential_ready",
    "delete_model",
    "describe",
    "get_model",
    "get_model_by_name",
    "health_sweep",
    "list_attempts",
    "list_models",
    "list_recent_tasks",
    "performance_by_model",
    "update_model",
]
