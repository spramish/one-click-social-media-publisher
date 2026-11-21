"""Common publishing machinery for the platform publishers.

Each social platform uploads a video differently, but every publisher in this
package answers the same three questions and behaves the same way around
failure. This module holds that common surface so a concrete publisher — see
``instagram.py``, ``facebook.py``, ``youtube.py`` and ``tiktok.py`` — only has
to describe *what is different*.

Design rules, mirroring ``app.services.oauth.base``:

* **Success is only reported once the platform confirms the final status.** A
  ``PublishResult`` is returned exclusively after the platform's own processing
  status reached a terminal, successful state. An HTTP 200 that merely accepted
  the upload is *not* success.
* **Every failure is classified.** :func:`Publisher._classify` maps an HTTP
  response or transport fault onto the error taxonomy below, splitting
  *retryable* (rate limits, 5xx, timeouts) from *permanent* (bad media, bad
  caption, revoked access). The orchestration service retries the first kind
  with exponential backoff and dead-letters the rest.
* **The HTTP layer is injectable.** ``transport`` lets the test suite drive
  every publisher against an ``httpx.MockTransport`` instead of a real platform.
* **No token ever reaches a log or an exception message.** Failures quote the
  platform's own error text and the endpoint, never a credential.
"""

from __future__ import annotations

import abc
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, TypeVar

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.db.enums import SocialPlatform

logger = get_logger(__name__)

_T = TypeVar("_T")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class PublishError(Exception):
    """Base class for every publishing failure.

    ``retryable`` is what the orchestration service reads to decide between a
    backoff retry and a dead letter, so it is a class attribute rather than
    something a caller has to infer from the message.
    """

    #: Whether re-running the same job could plausibly succeed later.
    retryable: ClassVar[bool] = False


class PublishTransientError(PublishError):
    """A failure that may succeed on a later attempt (5xx, timeout, network)."""

    retryable: ClassVar[bool] = True


class PublishRateLimitError(PublishTransientError):
    """The platform refused the call because the account is being throttled."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(message)


class PublishAuthError(PublishError):
    """The credential is missing, expired, or lacks the required permission.

    Retrying will never help: the user has to reconnect the account, so the
    orchestration service surfaces a "reconnect" message instead of a retry.
    """


class PublishPermanentError(PublishError):
    """A request the platform rejected and will keep rejecting."""


class MediaValidationError(PublishError):
    """The video does not meet this platform's rules; nothing is uploaded."""


class CaptionValidationError(PublishError):
    """The caption/title does not meet this platform's rules; nothing is uploaded."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class PublisherCapability:
    """The documented limits a platform imposes on a video post."""

    platform: SocialPlatform
    display_name: str
    #: Largest accepted file. Enforced before a byte is uploaded.
    max_size_bytes: int
    #: Longest accepted clip, or ``None`` when the platform has no practical cap.
    max_duration_seconds: int | None
    min_width: int
    min_height: int
    #: Longest accepted caption.
    max_caption_length: int
    #: Whether a non-empty title is mandatory (YouTube).
    requires_title: bool
    #: Whether the platform uses a separate title field at all.
    supports_title: bool
    #: Container MIME types the platform accepts.
    allowed_mime_types: tuple[str, ...]
    #: Human-readable note shown in the composer (aspect ratio guidance etc.).
    notes: str = ""


@dataclass(frozen=True, slots=True)
class MediaAsset:
    """Everything a publisher needs about the video it must upload.

    Both a local ``path`` and a public ``public_url`` are carried because the
    platforms differ: YouTube and TikTok stream the bytes from the worker's
    disk, whereas Instagram and Facebook download the file themselves from the
    signed URL.
    """

    media_id: uuid.UUID
    #: Server-local filesystem path, or ``None`` when only a URL is available.
    path: Path | None
    size_bytes: int
    mime_type: str
    duration_seconds: float | None
    width: int | None
    height: int | None
    #: Signed, expiring URL a pull-based platform can fetch the video from.
    public_url: str
    #: SHA-256 of the video's bytes, when known. Lets duplicate-publishing
    #: guards recognise the same file across separate uploads.
    checksum_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class PublishRequest:
    """One (video, destination) publishing attempt."""

    asset: MediaAsset
    caption: str
    title: str | None
    #: A live access token for the destination account.
    access_token: str
    #: The platform's identifier for the destination (Page id, channel id, ...).
    platform_account_id: str
    #: Upload visibility: ``public``, ``private``, or a platform-specific value.
    privacy: str = "public"


@dataclass(frozen=True, slots=True)
class PublishResult:
    """The outcome of a publish the *platform* confirmed.

    ``status`` is the platform's own terminal status string (for example
    ``"ready"`` on Facebook, ``"PUBLISH_COMPLETE"`` on TikTok) and ``raw`` keeps
    the final response for the job log. Nothing here is inferred.
    """

    remote_post_id: str
    remote_url: str | None
    status: str
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Publisher base
# ---------------------------------------------------------------------------
class Publisher(abc.ABC):
    """One social platform's video-publishing implementation."""

    #: Which :class:`~app.db.enums.SocialPlatform` this publisher implements.
    platform: ClassVar[SocialPlatform]
    #: Name used in error messages and logs.
    display_name: ClassVar[str] = "platform"
    #: Optional transport override used by the test suite.
    transport: ClassVar[httpx.BaseTransport | None] = None

    # --- description ----------------------------------------------------
    @abc.abstractmethod
    def capability(self) -> PublisherCapability:
        """The platform's documented limits, read on every call."""

    def build_caption(
        self,
        *,
        base_caption: str | None,
        target_caption: str | None,
        title: str | None,
    ) -> str:
        """Assemble the text this platform should publish.

        The default joins the post's base caption and any per-target override.
        Platforms with their own conventions (Instagram hashtag spacing, TikTok's
        short title-first caption) override this.
        """
        parts: list[str] = []
        if base_caption and base_caption.strip():
            parts.append(base_caption.strip())
        if target_caption and target_caption.strip():
            override = target_caption.strip()
            if override != (base_caption or "").strip():
                parts.append(override)
        return "\n\n".join(parts)

    def build_title(self, *, title: str | None, base_caption: str | None) -> str | None:
        """The title this platform should publish, or ``None`` when unused."""
        if not self.capability().supports_title:
            return None
        cleaned = (title or "").strip()
        if cleaned:
            return cleaned[: self._title_limit()]
        # Fall back to the first line of the caption, which is what a viewer
        # would see as the title anyway, rather than failing the publish.
        fallback = (base_caption or "").strip().splitlines()
        return fallback[0][: self._title_limit()] if fallback else None

    def _title_limit(self) -> int:
        return 100

    # --- validation -----------------------------------------------------
    def validate_media(self, asset: MediaAsset) -> None:
        """Refuse a video the platform cannot accept, before uploading it.

        Raises:
            MediaValidationError: the container type, size, duration or
                resolution is outside the platform's documented limits.
        """
        cap = self.capability()
        if asset.mime_type not in cap.allowed_mime_types:
            raise MediaValidationError(
                f"{cap.display_name} does not accept {asset.mime_type} videos. "
                f"Supported types: {', '.join(cap.allowed_mime_types)}."
            )
        if asset.size_bytes > cap.max_size_bytes:
            raise MediaValidationError(
                f"This video is {_human_bytes(asset.size_bytes)}; "
                f"{cap.display_name} accepts at most {_human_bytes(cap.max_size_bytes)}."
            )
        if (
            cap.max_duration_seconds is not None
            and asset.duration_seconds is not None
            and asset.duration_seconds > cap.max_duration_seconds
        ):
            raise MediaValidationError(
                f"This video is {asset.duration_seconds:.0f}s long; "
                f"{cap.display_name} accepts at most {cap.max_duration_seconds}s."
            )
        if asset.width is not None and asset.height is not None:
            if asset.width < cap.min_width or asset.height < cap.min_height:
                raise MediaValidationError(
                    f"This video is {asset.width}x{asset.height}; {cap.display_name} "
                    f"needs at least {cap.min_width}x{cap.min_height}."
                )

    def validate_caption(self, *, caption: str, title: str | None) -> None:
        """Refuse a caption/title the platform cannot accept.

        Raises:
            CaptionValidationError: a required title is missing, or the caption
                exceeds the platform's character limit.
        """
        cap = self.capability()
        if cap.requires_title and not (title or "").strip():
            raise CaptionValidationError(f"{cap.display_name} requires a title.")
        if len(caption or "") > cap.max_caption_length:
            raise CaptionValidationError(
                f"The caption is {len(caption)} characters; {cap.display_name} "
                f"accepts at most {cap.max_caption_length}."
            )
        if cap.supports_title and title and len(title) > self._title_limit():
            raise CaptionValidationError(
                f"The title is {len(title)} characters; the limit is {self._title_limit()}."
            )

    # --- publishing -----------------------------------------------------
    @abc.abstractmethod
    def publish(self, request: PublishRequest) -> PublishResult:
        """Upload the video and drive it to a platform-confirmed final state.

        Implementations must never return a result merely because the upload was
        accepted: they poll the platform's processing status until it reports a
        terminal success, and raise on a terminal failure.
        """

    # --- HTTP helpers ---------------------------------------------------
    def _client(self, *, timeout: float | None = None) -> httpx.Client:
        return httpx.Client(
            transport=self.transport,
            timeout=timeout or settings.publishing_http_timeout_seconds,
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
        form: bool = False,
        timeout: float | None = None,
    ) -> httpx.Response:
        """Perform one platform call, translating transport faults.

        A connection error, DNS failure or timeout becomes a
        :class:`PublishTransientError` rather than propagating an ``httpx``
        exception, so the orchestration service only ever handles this module's
        taxonomy — and treats them as retryable.
        """
        try:
            with self._client(timeout=timeout) as client:
                return client.request(
                    method,
                    url,
                    data=data if form else None,
                    json=None if (form or content is not None) else data,
                    content=content,
                    params=params,
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            logger.warning(
                "publishing call timed out",
                extra={"platform": self.platform.value, "url": url},
            )
            raise PublishTransientError(
                f"The {self.display_name} API did not respond in time. "
                f"The upload will be retried."
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "publishing http call failed",
                extra={"platform": self.platform.value, "url": url, "error": str(exc)},
            )
            raise PublishTransientError(
                f"Could not reach the {self.display_name} API. The upload will be retried."
            ) from exc

    def _raise_for_status(
        self,
        response: httpx.Response,
        *,
        action: str,
        failure: str | None = None,
    ) -> None:
        """Classify a non-success response into the publishing taxonomy."""
        status_code = response.status_code
        reason = failure or self._describe(response)
        message = f"{self.display_name} could not {action}: {reason}"
        if status_code == 429:
            raise PublishRateLimitError(message, retry_after=_retry_after(response))
        if status_code in (401, 403):
            raise PublishAuthError(
                f"{self.display_name} rejected the credentials while trying to {action}. "
                f"Reconnect the account."
            )
        if status_code >= 500 or status_code in (408, 425):
            raise PublishTransientError(message)
        raise PublishPermanentError(message)

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
            raise PublishPermanentError(f"{self.display_name} could not {action}: {failure}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise PublishTransientError(
                f"{self.display_name} returned a non-JSON response while trying to "
                f"{action} (HTTP {response.status_code})."
            ) from exc
        if not isinstance(payload, dict):  # pragma: no cover - defensive
            raise PublishTransientError(
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

    def _poll(
        self,
        fetch: Callable[[], dict[str, Any]],
        *,
        describe: Callable[[dict[str, Any]], str],
        is_done: Callable[[str], bool],
        is_failed: Callable[[str], bool],
        action: str,
    ) -> dict[str, Any]:
        """Poll a processing status until it is terminal.

        Sleeping in a Celery worker for a few seconds between polls is
        deliberate: the platform is doing the encoding, and the alternative is a
        webhook endpoint no local deployment can receive.
        """
        attempts = settings.publishing_poll_max_attempts
        interval = settings.publishing_poll_interval_seconds
        for attempt in range(attempts):
            payload = fetch()
            state = describe(payload)
            if is_failed(state):
                raise PublishPermanentError(
                    f"{self.display_name} could not finish processing the video "
                    f"(status: {state})."
                )
            if is_done(state):
                return payload
            if attempt < attempts - 1:
                time.sleep(interval)
        raise PublishTransientError(
            f"{self.display_name} did not confirm the upload within "
            f"{attempts * interval} seconds. It will be retried."
        )


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


def _human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"  # pragma: no cover - unreachable


__all__ = [
    "CaptionValidationError",
    "MediaAsset",
    "MediaValidationError",
    "PublishAuthError",
    "PublishError",
    "PublishPermanentError",
    "PublishRateLimitError",
    "PublishRequest",
    "PublishResult",
    "PublishTransientError",
    "Publisher",
    "PublisherCapability",
]
