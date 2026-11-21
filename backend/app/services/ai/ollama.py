"""Ollama provider — a locally hosted model with no key and no bill.

Ollama exposes its own HTTP API rather than the OpenAI protocol, so it gets its
own thin provider: one ``POST /api/chat`` per completion, using ``httpx`` and the
same injectable ``transport`` the rest of the assistant uses (so the test suite
stays offline and free).

Two differences from the OpenAI provider matter:

* **No credential.** Ollama runs on the operator's own machine, so nothing is
  required to authenticate. An optional key is still sent when one is provided
  (a remote Ollama behind a proxy), but it is never required and never stored.
* **Different error shape.** Ollama answers a failure with a top-level
  ``{"error": "..."}``, which the shared status classifier already understands.

Because a local model costs nothing per token but may be slow to load, the
timeout is deliberately generous (``OLLAMA_TIMEOUT_SECONDS``) and the sampling
options travel in Ollama's ``options`` object rather than at the top level.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.services.ai.base import (
    AICompletion,
    AIProvider,
    AIProviderError,
    AITransientError,
    CompletionRequest,
    ModelSpec,
)

logger = get_logger(__name__)


class OllamaProvider(AIProvider):
    """Local inference through Ollama's ``/api/chat`` endpoint."""

    name = "ollama"
    display_name = "Ollama"

    # --- configuration --------------------------------------------------
    def model(self) -> str:
        return settings.ollama_model

    def is_configured(self) -> bool:
        # A local server needs no credential; a configured host is the whole
        # requirement, and there is always a default host.
        return bool(settings.ollama_base_url.strip())

    def _resolve(self, spec: ModelSpec | None) -> ModelSpec:
        """The effective host/model/limits for this call."""
        if spec is not None:
            return spec
        return ModelSpec(
            name=self.name,
            kind=self.name,
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            api_key="",
            timeout_seconds=float(settings.ollama_timeout_seconds),
            max_output_tokens=settings.openai_max_output_tokens,
            temperature=settings.openai_temperature,
        )

    def _endpoint(self, spec: ModelSpec) -> str:
        return f"{spec.base_url.rstrip('/')}/api/chat"

    def _headers(self, spec: ModelSpec) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        # Only sent when the deployment put a key in front of its local server.
        if spec.api_key.strip():
            headers["Authorization"] = f"Bearer {spec.api_key}"
        return headers

    # --- generation -----------------------------------------------------
    def complete(
        self, request: CompletionRequest, spec: ModelSpec | None = None
    ) -> AICompletion:
        """Send one chat completion to the local model.

        Raises:
            AIRateLimitError: the local server is busy (rare, but possible).
            AITransientError: the server is unreachable or failed.
            AIProviderError: the request was rejected or the body was unusable.
        """
        resolved = self._resolve(spec)

        payload: dict[str, Any] = {
            "model": resolved.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],
            # Streaming would break the single-response contract.
            "stream": False,
            "options": {
                "temperature": (
                    request.temperature
                    if request.temperature is not None
                    else resolved.temperature
                ),
                "num_predict": request.max_output_tokens or resolved.max_output_tokens,
            },
        }

        response = self._request(
            "POST",
            self._endpoint(resolved),
            json=payload,
            headers=self._headers(resolved),
            timeout=resolved.timeout_seconds,
        )
        self._raise_for_status(response, action="generate content")

        try:
            body = response.json()
        except ValueError as exc:
            raise AITransientError(
                f"{self.display_name} returned a non-JSON response "
                f"(HTTP {response.status_code})."
            ) from exc
        if not isinstance(body, dict):  # pragma: no cover - defensive
            raise AIProviderError(f"{self.display_name} returned an unexpected response.")

        return self._parse(body, fallback_model=resolved.model)

    # --- parsing --------------------------------------------------------
    def _parse(self, body: dict[str, Any], *, fallback_model: str) -> AICompletion:
        message = body.get("message") or {}
        text = (message.get("content") or "").strip()
        if not text:
            raise AIProviderError(
                f"{self.display_name} returned an empty completion. Please try again."
            )
        # Ollama only reports a stop reason implicitly; a truncated generation is
        # visible in ``done_reason`` when the server is new enough to send it.
        done_reason = body.get("done_reason")
        if isinstance(done_reason, str) and "length" in done_reason:
            finish_reason: str | None = "length"
        else:
            finish_reason = "stop"
        return AICompletion(
            text=text,
            model=str(body.get("model") or fallback_model),
            provider=self.name,
            prompt_tokens=_as_int(body.get("prompt_eval_count")),
            completion_tokens=_as_int(body.get("eval_count")),
            finish_reason=finish_reason,
            raw=body,
        )


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


__all__ = ["OllamaProvider"]
