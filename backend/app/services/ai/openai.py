"""OpenAI chat-completions provider.

Calls the Chat Completions REST endpoint directly with ``httpx`` rather than
pulling in the vendor SDK: the surface used here is one request and one
response, the transport is already a dependency, and an injectable transport
keeps the test suite offline and free.

``OPENAI_BASE_URL`` can point at any OpenAI-compatible gateway (Azure OpenAI, a
local proxy) without a code change, so this provider is not tied to the hosted
service either.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.services.ai.base import (
    AICompletion,
    AIContentRejectedError,
    AINotConfiguredError,
    AIProvider,
    AIProviderError,
    AITransientError,
    CompletionRequest,
    ModelSpec,
)

logger = get_logger(__name__)


class OpenAIProvider(AIProvider):
    """The OpenAI (and OpenAI-compatible) implementation of :class:`AIProvider`.

    Without a ``spec`` it reads ``settings``, which is the single
    environment-configured model. With a ``spec`` — built by the Phase 12 router
    from an ``ai_models`` row — it talks to that model's own endpoint with that
    model's own credential, so several OpenAI-compatible providers (OpenAI,
    Groq, Together, OpenRouter, a local proxy) share this one class.
    """

    name = "openai"
    display_name = "OpenAI"

    # --- configuration --------------------------------------------------
    def model(self) -> str:
        return settings.openai_model

    def is_configured(self) -> bool:
        return bool(settings.openai_api_key.strip())

    def _resolve(self, spec: ModelSpec | None) -> ModelSpec:
        """The effective endpoint/model/limits for this call."""
        if spec is not None:
            return spec
        return ModelSpec(
            name=self.name,
            kind=self.name,
            model=settings.openai_model,
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            timeout_seconds=float(settings.openai_http_timeout_seconds),
            max_output_tokens=settings.openai_max_output_tokens,
            temperature=settings.openai_temperature,
        )

    def _endpoint(self, spec: ModelSpec) -> str:
        return f"{spec.base_url.rstrip('/')}/chat/completions"

    def _headers(self, spec: ModelSpec) -> dict[str, str]:
        # The key travels in the header only; it is never logged and never put
        # into an exception message.
        return {
            "Authorization": f"Bearer {spec.api_key}",
            "Content-Type": "application/json",
        }

    # --- generation -----------------------------------------------------
    def complete(
        self, request: CompletionRequest, spec: ModelSpec | None = None
    ) -> AICompletion:
        """Send one chat completion and return the model's text.

        Raises:
            AINotConfiguredError: no API key is set for this model.
            AIRateLimitError: the account is being throttled.
            AIContentRejectedError: the model refused for safety reasons.
            AITransientError: the provider is unreachable or failed server-side.
            AIProviderError: the provider rejected the request or returned an
                unusable body.
        """
        resolved = self._resolve(spec)
        if not resolved.api_key.strip():
            raise AINotConfiguredError(resolved.name or self.display_name)

        payload: dict[str, Any] = {
            "model": resolved.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.prompt},
            ],
            "max_tokens": request.max_output_tokens or resolved.max_output_tokens,
            "temperature": (
                request.temperature
                if request.temperature is not None
                else resolved.temperature
            ),
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
        choices = body.get("choices") or []
        if not choices:
            # Some OpenAI-compatible gateways put the failure in the body with a
            # 200 status; surface it rather than returning an empty success.
            error = body.get("error")
            if isinstance(error, dict) and error.get("message"):
                raise AIProviderError(f"{self.display_name} rejected the request: {error['message']}")
            raise AIProviderError(f"{self.display_name} returned no completion.")

        choice = choices[0] if isinstance(choices[0], dict) else {}
        finish_reason = choice.get("finish_reason")
        message = choice.get("message") or {}
        text = (message.get("content") or "").strip()

        if finish_reason == "content_filter":
            raise AIContentRejectedError(
                f"{self.display_name} declined to write this content. "
                f"Try rephrasing the brief."
            )
        if not text:
            raise AIProviderError(
                f"{self.display_name} returned an empty completion. Please try again."
            )

        usage = body.get("usage") or {}
        return AICompletion(
            text=text,
            model=str(body.get("model") or fallback_model),
            provider=self.name,
            prompt_tokens=_as_int(usage.get("prompt_tokens")),
            completion_tokens=_as_int(usage.get("completion_tokens")),
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            raw=body,
        )


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


__all__ = ["OpenAIProvider"]
