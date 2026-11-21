"""Phase 15 tests — opt-in error tracking.

Sentry must never be initialised implicitly: a developer or CI run without a DSN
has to stay completely offline, and a configured one has to receive the DSN and
the sampling rate that came from settings — not a hard-coded guess.
"""

from __future__ import annotations

import pytest

from app.core import observability
from app.core.config import settings


def test_error_tracking_is_off_without_a_dsn(monkeypatch) -> None:
    monkeypatch.setattr(settings, "sentry_dsn", None)

    assert observability.configure_error_tracking() is False


def test_error_tracking_passes_the_configured_dsn_through(monkeypatch) -> None:
    sentry_sdk = pytest.importorskip("sentry_sdk")
    captured: dict[str, object] = {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: captured.update(kwargs))
    monkeypatch.setattr(settings, "sentry_dsn", "https://key@example.ingest.sentry.io/1")
    monkeypatch.setattr(settings, "sentry_traces_sample_rate", 0.25)

    assert observability.configure_error_tracking() is True
    assert captured["dsn"] == "https://key@example.ingest.sentry.io/1"
    assert captured["traces_sample_rate"] == 0.25
    # Credentials, tokens, and upload bodies must never leave the process.
    assert captured["send_default_pii"] is False
