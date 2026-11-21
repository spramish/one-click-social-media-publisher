"""Common machinery for the platform metrics (analytics) providers.

Each social platform exposes performance numbers differently, but every metrics
provider in this package answers the same two questions and behaves the same way
around failure. This module holds that common surface so a concrete provider —
see ``facebook.py``, ``instagram.py``, ``youtube.py`` and ``tiktok.py`` — only
has to describe *what is different*.

Design rules, mirroring :mod:`app.services.publishers` and
:mod:`app.services.ai.base`:

* **Unavailable is not zero.** Every metric is an ``int | None``: a value the
  platform did not return stays ``None`` and is reported to the user as
  "not available", while a real ``0`` is stored and displayed as ``0``. The
  phase brief requires the two cases to stay distinguishable, and only the
  provider can tell them apart.
* **Every failure is classified.** :func:`MetricsProvider._raise_for_status`
  maps an HTTP response or transport fault onto the taxonomy below, splitting
  *retryable* (rate limits, 5xx, timeouts) from *permanent* (a metric this
  platform does not expose, an account that must be reconnected).
* **The HTTP layer is injectable.** ``transport`` lets the test suite drive
  every provider against an ``httpx.MockTransport`` instead of a live platform.
* **No token ever reaches a log or an exception message.** Failures quote the
  platform's own error text and the endpoint, never a credential.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.db.enums import SocialPlatform

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class MetricsError(Exception):
    """Base class for every metrics-retrieval failure.

    ``retryable`` is a class attribute rather than something a caller infers
    from the message, so the service maps a failure to an outcome without
    parsing prose.
    """

    #: Whether re-running the same retrieval could plausibly succeed later.
    retryable: ClassVar[bool] = False


class MetricsTransientError(MetricsError):
    """A failure that may succeed on a later attempt (5xx, timeout, network)."""

    retryable: ClassVar[bool] = True


class MetricsRateLimitError(MetricsTransientError):
    """The platform refused the call because the account is being throttled."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(message)


class MetricsAuthError(MetricsError):
    """The credential is missing, expired, or lacks the required scope.

    Retrying will never help: the user has to reconnect the account, so the
    service surfaces a "reconnect" message instead of a retry.
    """


class MetricsUnavailableError(MetricsError):
    """This platform does not expose the requested metric over its public API.

    The brief forbids inventing data, so a metric no official API provides is
    reported as unavailable rather than estimated.
    """


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class PostMetrics:
    """One retrieval of a single published target's video metrics.

    A ``None`` field means "the platform did not provide this metric"; an ``int``
    is the platform's own value, including a legitimate ``0``.
    """

    platform: SocialPlatform
    retrieved_at: datetime
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    reach: int | None = None
    impressions: int | None = None
    #: The platform's own response, kept for support and audit. Never a secret.
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class AccountMetrics:
    """One retrieval of a connected account's audience metrics."""

    platform: SocialPlatform
    retrieved_at: datetime
    followers: int | None = None
    follows: int | None = None
    posts_count: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# Provider base
# ---------------------------------------------------------------------------
class MetricsProvider(abc.ABC):
    """One social platform's read-only metrics implementation."""

    #: Which :class:`~app.db.enums.SocialPlatform` this provider implements.
    platform: ClassVar[SocialPlatform]
    #: Name used in error messages and logs.
    display_name: ClassVar[str] = "platform"
    #: Optional transport override used by the test suite.
    transport: ClassVar[httpx.BaseTransport | None] = None

    # --- reads ----------------------------------------------------------
    @abc.abstractmethod
    def fetch_post_metrics(
        self,
        *,
        access_token: str,
        remote_post_id: str,
        platform_account_id: str,
    ) -> PostMetrics:
        """Read one published video's metrics from the platform.

        Implementations return ``None`` for any metric the platform does not
        expose; they must never invent a value.
        """

    @abc.abstractmethod
    def fetch_account_metrics(
        self,
        *,
        access_token: str,
        platform_account_id: str,
    ) -> AccountMetrics:
        """Read one connected account's audience metrics from the platform."""

    # --- HTTP helpers ---------------------------------------------------
    def _client(self, *, timeout: float | None = None) -> httpx.Client:
        return httpx.Client(
            transport=self.transport,
            timeout=timeout or settings.analytics_http_timeout_seconds,
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """Perform one platform call, translating transport faults.

        A connection error, DNS failure or timeout becomes a
        :class:`MetricsTransientError` rather than propagating an ``httpx``
        exception, so callers only ever handle this module's taxonomy.
        """
        try:
            with self._client(timeout=timeout) as client:
                return client.request(method, url, params=params, json=json, headers=headers)
        except httpx.TimeoutException as exc:
            logger.warning(
                "metrics call timed out",
                extra={"platform": self.platform.value, "url": url},
            )
            raise MetricsTransientError(
                f"The {self.display_name} API did not respond in time. Try refreshing again."
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "metrics http call failed",
                extra={"platform": self.platform.value, "url": url, "error": str(exc)},
            )
            raise MetricsTransientError(
                f"Could not reach the {self.display_name} API. Try refreshing again."
            ) from exc

    def _raise_for_status(
        self,
        response: httpx.Response,
        *,
        action: str,
        failure: str | None = None,
    ) -> None:
        """Classify a non-success response into the metrics taxonomy."""
        status_code = response.status_code
        reason = failure or self._describe(response)
        message = f"{self.display_name} could not {action}: {reason}"
        if status_code == 429:
            raise MetricsRateLimitError(message, retry_after=_retry_after(response))
        if status_code in (401, 403):
            raise MetricsAuthError(
                f"{self.display_name} rejected the credentials while trying to {action}. "
                f"Reconnect the account."
            )
        if status_code >= 500 or status_code in (408, 425):
            raise MetricsTransientError(message)
        raise MetricsError(message)

    def _json(
        self,
        response: httpx.Response,
        *,
        action: str,
        failure: str | None = None,
    ) -> dict[str, Any]:
        """Parse a JSON body, raising a classified error for anything else.

        ``failure`` lets a caller that has already detected a platform-level
        failure (an ``error`` envelope inside a 200 response) force an error
        without re-deriving it. Error responses never carry a token, so the
        reason is safe to include in the message.
        """
        if response.status_code >= 400:
            self._raise_for_status(response, action=action, failure=failure)
        if failure is not None:
            raise MetricsError(f"{self.display_name} could not {action}: {failure}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise MetricsTransientError(
                f"{self.display_name} returned a non-JSON response while trying to "
                f"{action} (HTTP {response.status_code})."
            ) from exc
        if not isinstance(payload, dict):  # pragma: no cover - defensive
            raise MetricsTransientError(
                f"{self.display_name} returned an unexpected response while trying to {action}."
            )
        return payload

    def _describe(self, response: httpx.Response) -> str:
        """Extract a human-readable reason from a platform error body."""
        try:
            payload = response.json()
        except ValueError:
            return f"HTTP {response.status_code}"
        return _describe_error(payload) or f"HTTP {response.status_code}"


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------
def _retry_after(response: httpx.Response) -> float | None:
    """Seconds the platform asked us to wait, when it said so."""
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _describe_error(payload: Any) -> str | None:
    """Extract a human-readable reason from a platform error body."""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("type") or error.get("code")
            description = error.get("error_user_msg") or payload.get("error_description")
            if message and description and description != message:
                return f"{message} ({description})"
            if message:
                return str(message)
        if isinstance(error, str):
            description = payload.get("error_description") or payload.get("message")
            return f"{error}: {description}" if description else error
        for key in ("message", "error_description", "detail", "error"):
            if payload.get(key):
                return str(payload[key])
    return None


def as_int(value: Any) -> int | None:
    """Parse a platform's count into an ``int``, or ``None`` when it was absent.

    A metric the platform did not return is ``None`` (unavailable); a value the
    platform returned as ``0`` is ``0`` (a real zero). Strings are accepted
    because several platforms quote their numbers, and a non-numeric or empty
    value is treated as "not provided" rather than raising.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            return None
    return None


__all__ = [
    "AccountMetrics",
    "MetricsAuthError",
    "MetricsError",
    "MetricsProvider",
    "MetricsRateLimitError",
    "MetricsTransientError",
    "MetricsUnavailableError",
    "PostMetrics",
    "as_int",
]
