"""Shared Meta (Facebook Graph) metrics machinery.

Facebook Page and Instagram professional-account metrics both ride on the same
Graph API host and version segment, so the URL builder and the two parsing
helpers every Meta response needs live here. Everything else differs enough that
each provider keeps its own flow.

Meta is the platform whose response shapes are the most irregular: a count may
arrive as a bare number, as ``{"count": n}``, or as
``{"summary": {"total_count": n}}`` depending on the field and API version, and
an application-level error can ride inside an HTTP ``200``. Both quirks are
handled here so the concrete providers stay readable.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.services.metrics.base import MetricsError, MetricsProvider, as_int

#: Graph API host. The version segment comes from ``META_GRAPH_VERSION``.
GRAPH_SOURCE = "https://graph.facebook.com"


class MetaMetricsProvider(MetricsProvider):
    """Graph-API base for the Facebook and Instagram metrics providers."""

    def graph_version(self) -> str:
        return settings.meta_graph_version

    def graph_url(self, path: str) -> str:
        """Absolute Graph API URL for ``path`` (version prefix applied)."""
        return f"{GRAPH_SOURCE}/{self.graph_version()}/{path.lstrip('/')}"

    def _meta_failure(self, payload: dict[str, Any]) -> str | None:
        """Return Meta's message when the payload is an error envelope.

        A Graph API error frequently arrives inside a ``200`` response, so a
        caller checks the payload after parsing rather than trusting the status
        code alone.
        """
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

    def _ensure_ok(self, payload: dict[str, Any], *, action: str) -> None:
        """Raise when a Graph API error rode inside an HTTP ``200`` response."""
        failure = self._meta_failure(payload)
        if failure:
            raise MetricsError(f"{self.display_name} could not {action}: {failure}")

    def _total(self, node: Any) -> int | None:
        """Read a Meta count from a ``summary.total_count``, ``count``, or number.

        Raises nothing: an absent or unparseable count is returned as ``None``,
        which is how the API distinguishes "not provided" from a real zero.
        """
        if not isinstance(node, dict):
            return as_int(node)
        summary = node.get("summary")
        if isinstance(summary, dict) and "total_count" in summary:
            return as_int(summary.get("total_count"))
        if "count" in node:
            return as_int(node.get("count"))
        return None


__all__ = ["GRAPH_SOURCE", "MetaMetricsProvider"]
