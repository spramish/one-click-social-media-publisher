"""Shared Meta (Facebook Graph) publishing machinery.

Facebook Page publishing and Instagram professional-account publishing both
ride on the same Graph API host and version segment, so the URL builder lives
here. Everything else differs enough that each publisher keeps its own flow.

Both Meta destinations are **pull-based**: the API is given the video's public
URL and downloads it itself, so no bytes leave the worker.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.services.publishers.base import Publisher

#: Graph API host. The version segment comes from ``META_GRAPH_VERSION``.
GRAPH_SOURCE = "https://graph.facebook.com"


class MetaPublisher(Publisher):
    """Graph-API base for the Facebook and Instagram publishers."""

    def graph_version(self) -> str:
        return settings.meta_graph_version

    def graph_url(self, path: str) -> str:
        """Absolute Graph API URL for ``path`` (version prefix applied)."""
        return f"{GRAPH_SOURCE}/{self.graph_version()}/{path.lstrip('/')}"

    def _meta_failure(self, payload: dict[str, Any]) -> str | None:
        """Return Meta's message when the payload is an error envelope."""
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("type") or error.get("code")
            description = error.get("error_user_msg")
            if message and description and description != message:
                return f"{message} ({description})"
            if message:
                return str(message)
        if isinstance(error, str) and error:
            return error
        return None


__all__ = ["GRAPH_SOURCE", "MetaPublisher"]
