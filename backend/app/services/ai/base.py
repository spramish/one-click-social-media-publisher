"""Common machinery for the AI content providers.

The assistant generates text (a caption, a hashtag set, a description, a
rewrite) through one small interface so the rest of the application — the
generation service and the API layer — never names a concrete vendor. Adding a
second backend means adding one class and one registry line.

Design rules, mirroring :mod:`app.services.publishers` and
:mod:`app.services.oauth`:

* **A missing credential is a capability gap, not a crash.** An unconfigured
  provider raises :class:`AINotConfiguredError`, which the API answers with
  ``503`` so an operator knows to fill in ``.env`` — the user does not discover
  the problem only after typing a brief.
* **Every failure is classified.** :func:`AIProvider._raise_for_status` maps an
  HTTP response or transport fault onto the taxonomy below, splitting
  *transient* (rate limits, 5xx, timeouts — worth retrying) from *permanent*
  (a rejected key, a malformed response, a content-policy refusal).
* **The HTTP layer is injectable.** ``transport`` lets the test suite drive the
  provider against an ``httpx.MockTransport`` instead of the real API, so no
  test needs a network or a bill.
* **No secret ever reaches a log or an exception message.** Failures quote the
  endpoint and the vendor's own error text, never the API key.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, ClassVar

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class AIError(Exception):
    """Base class for every AI-generation failure.

    ``retryable`` is a class attribute rather than something a caller infers
    from the message, so the API layer maps a failure to a status code without
    parsing prose.
    """

    #: Whether re-running the same request could plausibly succeed later.
    retryable: ClassVar[bool] = False


class AINotConfiguredError(AIError):
    """The provider has no credential, so the feature cannot run at all.

    This is a *configuration state*, not a bug: the API turns it into ``503``
    and the UI says the assistant is unavailable on this server.
    """

    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__(
            f"The AI assistant is not configured on this server. "
            f"Set the {provider} API key in the backend environment."
        )


class AITransientError(AIError):
    """A failure that may succeed on a later attempt (5xx, timeout, network)."""

    retryable: ClassVar[bool] = True


class AIRateLimitError(AITransientError):
    """The provider refused the call because the account is being throttled."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(message)


class AIProviderError(AIError):
    """The provider rejected the request, or returned something unusable."""


class AIContentRejectedError(AIProviderError):
    """The provider refused to produce this content (safety/policy filter).

    Distinguished from a generic provider error because the *user's brief* is
    the cause, so the API answers ``422`` and the message asks them to rephrase
    rather than blaming the server.
    """


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class CompletionRequest:
    """One provider-agnostic completion call.

    ``system`` and ``prompt`` are already fully assembled by the generation
    service (which owns the prompt engineering); a provider only has to send
    them and return the text.
    """

    system: str
    prompt: str
    #: Ceiling on generated text. ``None`` falls back to the configured default.
    max_output_tokens: int | None = None
    #: Sampling temperature. ``None`` falls back to the configured default.
    temperature: float | None = None


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """A resolved model the router wants a provider bound to.

    Built by :mod:`app.services.ai_router` from an :class:`~app.models.ai_model.
    AIModel` row (or from ``settings`` for the single env-configured default).
    The ``api_key`` is the *resolved secret* read from the environment at call
    time — it lives only in memory and is never logged or persisted.
    """

    name: str
    kind: str
    model: str
    base_url: str
    api_key: str = ""
    timeout_seconds: float = 60.0
    max_output_tokens: int = 800
    temperature: float = 0.8


@dataclass(frozen=True, slots=True)
class AICompletion:
    """What the provider returned, in vendor-neutral form.

    Nothing here is inferred: ``text`` is the model's own output, and the token
    counts and ``finish_reason`` come straight from the response.
    """

    text: str
    model: str
    provider: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    #: Why the model stopped (``stop``, ``length``, ``content_filter``).
    finish_reason: str | None = None
    #: Raw vendor payload, kept for diagnostics only and never logged.
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# Provider base
# ---------------------------------------------------------------------------
class AIProvider(abc.ABC):
    """One AI backend's completion implementation.

    Subclasses set :attr:`name`/:attr:`display_name` and implement
    :meth:`model`, :meth:`is_configured` and :meth:`complete`.
    """

    #: Stable identifier used by the registry (``openai``).
    name: ClassVar[str] = "provider"
    #: Name used in error messages and logs.
    display_name: ClassVar[str] = "AI provider"
    #: Optional transport override used by the test suite.
    transport: ClassVar[httpx.BaseTransport | None] = None

    # --- configuration --------------------------------------------------
    @abc.abstractmethod
    def model(self) -> str:
        """The model identifier this provider will call."""

    @abc.abstractmethod
    def is_configured(self) -> bool:
        """True when the credential this provider needs is present."""

    def require_configured(self) -> None:
        """Raise a capability error instead of performing a broken request."""
        if not self.is_configured():
            raise AINotConfiguredError(self.display_name)

    # --- generation -----------------------------------------------------
    @abc.abstractmethod
    def complete(
        self, request: CompletionRequest, spec: ModelSpec | None = None
    ) -> AICompletion:
        """Send one completion request and return the model's text.

        ``spec`` binds the call to one configured model (its endpoint, its
        credential and its limits). It is ``None`` for the single model read
        straight from ``settings``, which is how the assistant behaved before
        the Phase 12 registry existed.

        Implementations must call :meth:`require_configured` first so a missing
        credential is reported as a capability gap rather than a 401.
        """

    # --- HTTP -----------------------------------------------------------
    def _client(self, *, timeout: float | None = None) -> httpx.Client:
        return httpx.Client(
            transport=self.transport,
            timeout=timeout if timeout is not None else settings.openai_http_timeout_seconds,
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """Perform one provider call, translating transport faults.

        A connection error, DNS failure or timeout becomes an
        :class:`AITransientError` rather than propagating an ``httpx``
        exception, so callers only ever handle this module's taxonomy.
        """
        try:
            with self._client(timeout=timeout) as client:
                return client.request(method, url, json=json, headers=headers)
        except httpx.TimeoutException as exc:
            logger.warning(
                "ai completion timed out",
                extra={"provider": self.name, "url": url},
            )
            raise AITransientError(
                f"The {self.display_name} API did not respond in time. Please try again."
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "ai http call failed",
                extra={"provider": self.name, "url": url, "error": str(exc)},
            )
            raise AITransientError(
                f"Could not reach the {self.display_name} API. Please try again."
            ) from exc

    def _raise_for_status(self, response: httpx.Response, *, action: str) -> None:
        """Classify a non-success response into the AI taxonomy."""
        status_code = response.status_code
        if status_code < 400:
            return
        reason = _describe_error(response) or f"HTTP {status_code}"
        if status_code == 429:
            raise AIRateLimitError(
                f"The {self.display_name} assistant is busy right now. Please try again shortly.",
                retry_after=_retry_after(response),
            )
        if status_code in (400, 422) and _looks_like_content_filter(reason):
            raise AIContentRejectedError(
                f"{self.display_name} declined to write this content: {reason} "
                f"Try rephrasing the brief."
            )
        if status_code >= 500 or status_code in (408, 425):
            raise AITransientError(
                f"{self.display_name} could not {action}: {reason}"
            )
        raise AIProviderError(f"{self.display_name} could not {action}: {reason}")


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------
def _retry_after(response: httpx.Response) -> float | None:
    """Seconds the provider asked us to wait, when it said so."""
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _describe_error(response: httpx.Response) -> str | None:
    """Extract a human-readable reason from a provider error body."""
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("type") or error.get("code")
        if message:
            return str(message)
    if isinstance(error, str):
        return error
    for key in ("message", "detail", "error_description"):
        if payload.get(key):
            return str(payload[key])
    return None


#: Substrings that mark a policy refusal rather than a malformed request, so a
#: user-caused rejection is not reported as a server fault.
_CONTENT_FILTER_MARKERS = (
    "content_policy",
    "content filter",
    "safety",
    "moderation",
    "disallowed",
)


def _looks_like_content_filter(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in _CONTENT_FILTER_MARKERS)


__all__ = [
    "AICompletion",
    "AIContentRejectedError",
    "AIError",
    "AINotConfiguredError",
    "AIProvider",
    "AIProviderError",
    "AIRateLimitError",
    "AITransientError",
    "CompletionRequest",
    "ModelSpec",
]
