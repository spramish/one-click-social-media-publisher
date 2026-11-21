"""AI provider registry.

The generation service is written against
:class:`~app.services.ai.base.AIProvider` and never names a concrete vendor, so
the only place that maps a name to an implementation is :func:`get_provider`.
Adding a backend means adding one class and one line here.

This mirrors ``app.services.publishers`` and ``app.services.oauth``: one
stateless instance per provider, and the same "fail loudly on an unregistered
name" contract.
"""

from __future__ import annotations

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
from app.services.ai.ollama import OllamaProvider
from app.services.ai.openai import OpenAIProvider

#: The provider used when a caller does not name one.
DEFAULT_PROVIDER = "openai"

#: One instance per provider. Providers are stateless — they read their
#: credential and model from ``settings`` on every call, and the only mutable
#: attribute is the test-only ``transport`` — so sharing a single instance
#: across requests is safe.
_PROVIDERS: dict[str, AIProvider] = {
    OpenAIProvider.name: OpenAIProvider(),
    OllamaProvider.name: OllamaProvider(),
}


def get_provider(name: str | None = None) -> AIProvider:
    """Return the registered provider called ``name`` (default: OpenAI).

    Raises:
        AIError: the name has no registered implementation. This is a
            programming error rather than a user error, so it fails loudly.
    """
    key = (name or DEFAULT_PROVIDER).strip().lower()
    try:
        return _PROVIDERS[key]
    except KeyError as exc:  # pragma: no cover - guarded by DEFAULT_PROVIDER
        raise AIError(f"No AI provider is registered for '{key}'.") from exc


def supported_providers() -> tuple[str, ...]:
    """Every provider name the assistant can use."""
    return tuple(_PROVIDERS)


__all__ = [
    "DEFAULT_PROVIDER",
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
    "get_provider",
    "supported_providers",
]
