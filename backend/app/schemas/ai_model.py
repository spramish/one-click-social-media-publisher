"""Request/response schemas for the AI model registry and routing history.

The registry is what makes the assistant free and resilient: an administrator
lists the models it may call, in priority order, and the router fails over
between them. Three rules shape these contracts:

* **No credential is ever carried here.** A model row names the *environment
  variable* that holds its key (``credential_env``); the key itself is read from
  the process environment at call time and is never returned by any route.
  ``credential_ready`` is a live boolean — "the variable is set right now" —
  so an operator can see a missing key without ever seeing the key.
* **A partial update changes only what it names.** Every field of
  :class:`AIModelUpdate` is optional; an omitted field is left alone, and
  ``credential_env: ""`` is the documented way to clear it.
* **History is read-only.** :class:`AITaskPublic` is a task collapsed from its
  attempts and :class:`AITaskAttemptPublic` is one raw attempt, so both the
  failover chain and the per-model performance are visible without a write path.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.db.enums import AIContentKind, AIModelKind, AIModelStatus, AITaskStatus
from app.models.ai_model import AIModel, AITaskAttempt
from app.services import ai_router as router_service

#: Bounds that keep a registry row sane. They mirror the column widths on
#: :class:`~app.models.ai_model.AIModel`, so a value that passes validation can
#: always be stored.
_MAX_NAME_LENGTH = 80
_MAX_DISPLAY_NAME_LENGTH = 120
_MAX_MODEL_LENGTH = 120
_MAX_BASE_URL_LENGTH = 255
_MAX_CREDENTIAL_ENV_LENGTH = 80

#: Lower runs first; the ceiling is only there to stop a typo becoming a number
#: nothing else can ever follow.
_MAX_PRIORITY = 10_000


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
class AIModelPublic(BaseModel):
    """One registered model, with the health the router last observed."""

    id: uuid.UUID
    name: str
    display_name: str
    kind: AIModelKind
    model: str
    base_url: str
    #: Name of the environment variable holding the key — never the key itself.
    credential_env: str | None = None
    #: Whether that variable resolves in this process right now.
    credential_ready: bool
    priority: int
    enabled: bool
    supports: list[str] | None = None
    timeout_seconds: int
    max_output_tokens: int
    temperature: float
    status: AIModelStatus
    last_checked_at: datetime | None = None
    last_ok_at: datetime | None = None
    consecutive_failures: int
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, model: AIModel) -> "AIModelPublic":
        return cls(
            id=model.id,
            name=model.name,
            display_name=model.display_name,
            kind=model.kind,
            model=model.model,
            base_url=model.base_url,
            credential_env=model.credential_env,
            credential_ready=router_service.credential_ready(model),
            priority=model.priority,
            enabled=model.enabled,
            supports=list(model.supports) if model.supports else None,
            timeout_seconds=model.timeout_seconds,
            max_output_tokens=model.max_output_tokens,
            temperature=model.temperature,
            status=model.status,
            last_checked_at=model.last_checked_at,
            last_ok_at=model.last_ok_at,
            consecutive_failures=model.consecutive_failures,
            last_error=model.last_error,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )


class AIModelCreate(BaseModel):
    """Register one model.

    ``base_url`` may be left blank: an Ollama entry then defaults to
    ``OLLAMA_BASE_URL`` and an OpenAI-compatible entry to ``OPENAI_BASE_URL``.
    ``credential_env`` is omitted for a model that needs no key.
    """

    name: str = Field(
        min_length=1,
        max_length=_MAX_NAME_LENGTH,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
        description="Stable slug used by the API and in logs, e.g. 'groq-llama'.",
    )
    display_name: str = Field(min_length=1, max_length=_MAX_DISPLAY_NAME_LENGTH)
    kind: AIModelKind
    model: str = Field(min_length=1, max_length=_MAX_MODEL_LENGTH)
    base_url: str = Field(default="", max_length=_MAX_BASE_URL_LENGTH)
    credential_env: str | None = Field(
        default=None, max_length=_MAX_CREDENTIAL_ENV_LENGTH
    )
    priority: int = Field(default=100, ge=0, le=_MAX_PRIORITY)
    enabled: bool = True
    #: Which kinds of copy this model may produce; omitted means every kind.
    supports: list[AIContentKind] | None = None
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    max_output_tokens: int = Field(default=800, ge=16, le=32_000)
    temperature: float = Field(default=0.8, ge=0.0, le=2.0)


class AIModelUpdate(BaseModel):
    """A partial update — only the fields present are applied."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=_MAX_NAME_LENGTH,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    display_name: str | None = Field(
        default=None, min_length=1, max_length=_MAX_DISPLAY_NAME_LENGTH
    )
    kind: AIModelKind | None = None
    model: str | None = Field(default=None, min_length=1, max_length=_MAX_MODEL_LENGTH)
    base_url: str | None = Field(default=None, max_length=_MAX_BASE_URL_LENGTH)
    #: ``""`` clears the credential reference; omit it to leave it unchanged.
    credential_env: str | None = Field(
        default=None, max_length=_MAX_CREDENTIAL_ENV_LENGTH
    )
    priority: int | None = Field(default=None, ge=0, le=_MAX_PRIORITY)
    enabled: bool | None = None
    supports: list[AIContentKind] | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)
    max_output_tokens: int | None = Field(default=None, ge=16, le=32_000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


# ---------------------------------------------------------------------------
# Availability monitoring
# ---------------------------------------------------------------------------
class AIHealthSweepPublic(BaseModel):
    """What one availability sweep probed and found."""

    checked: int
    healthy: int
    unhealthy: int

    @classmethod
    def from_result(cls, result: router_service.HealthSweepResult) -> "AIHealthSweepPublic":
        return cls(checked=result.checked, healthy=result.healthy, unhealthy=result.unhealthy)


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------
class AIModelPerformancePublic(BaseModel):
    """Aggregated success rate and latency for one model, from the attempt log."""

    model_name: str
    total: int
    succeeded: int
    failed: int
    success_rate: float
    avg_latency_ms: int | None = None

    @classmethod
    def from_performance(
        cls, performance: router_service.ModelPerformance
    ) -> "AIModelPerformancePublic":
        return cls(
            model_name=performance.model_name,
            total=performance.total,
            succeeded=performance.succeeded,
            failed=performance.failed,
            success_rate=performance.success_rate,
            avg_latency_ms=performance.avg_latency_ms,
        )


# ---------------------------------------------------------------------------
# Task history / execution tracking
# ---------------------------------------------------------------------------
class AITaskPublic(BaseModel):
    """One routed task, collapsed from its attempts."""

    task_id: uuid.UUID
    kind: AIContentKind
    status: AITaskStatus
    attempts: int
    model_name: str
    created_at: datetime
    finished_at: datetime

    @classmethod
    def from_summary(cls, summary: router_service.TaskSummary) -> "AITaskPublic":
        return cls(
            task_id=summary.task_id,
            kind=summary.kind,
            status=summary.status,
            attempts=summary.attempts,
            model_name=summary.model_name,
            created_at=summary.created_at,
            finished_at=summary.finished_at,
        )


class AITaskAttemptPublic(BaseModel):
    """One model attempt: the usage log, the execution trail and the sample."""

    id: uuid.UUID
    task_id: uuid.UUID
    model_id: uuid.UUID | None = None
    model_name: str
    kind: AIContentKind
    status: AITaskStatus
    attempt: int
    latency_ms: int | None = None
    error_class: str | None = None
    error: str | None = None
    created_at: datetime

    @classmethod
    def from_attempt(cls, attempt: AITaskAttempt) -> "AITaskAttemptPublic":
        return cls(
            id=attempt.id,
            task_id=attempt.task_id,
            model_id=attempt.model_id,
            model_name=attempt.model_name,
            kind=attempt.kind,
            status=attempt.status,
            attempt=attempt.attempt,
            latency_ms=attempt.latency_ms,
            error_class=attempt.error_class,
            error=attempt.error,
            created_at=attempt.created_at,
        )


class AITaskListPublic(BaseModel):
    """A page of routed tasks."""

    items: list[AITaskPublic]
    total: int
    limit: int
    offset: int


class AITaskAttemptListPublic(BaseModel):
    """A page of raw attempts."""

    items: list[AITaskAttemptPublic]
    total: int
    limit: int
    offset: int


__all__ = [
    "AIModelCreate",
    "AIModelPerformancePublic",
    "AIModelPublic",
    "AIModelUpdate",
    "AIHealthSweepPublic",
    "AITaskAttemptListPublic",
    "AITaskAttemptPublic",
    "AITaskListPublic",
    "AITaskPublic",
]
