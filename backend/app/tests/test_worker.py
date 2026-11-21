"""Phase 14 tests — the Celery worker entry points.

Earlier phases verified the worker by hand: the beat schedules were read off
``celery inspect`` and the task bodies were only reached indirectly, through the
services they call. This module closes that gap by driving the tasks themselves.

Calling a Celery task object directly runs its body in-process — no broker is
involved — so the suite stays offline. Each DB-backed task is fed the test's own
rolled-back session by patching ``session_scope``, which is the only thing the
task adds on top of the service it delegates to.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

from app import worker
from app.core.config import settings
from app.services.analytics import RefreshResult

#: The names the deployment dispatches, whether from a route, a chain, or beat.
DISPATCHED_TASKS = {
    "health.ping",
    "publishing.publish_target",
    "scheduling.dispatch_due",
    "analytics.refresh_workspace",
    "notifications.sweep",
    "ai.health_sweep",
}

PERIODIC_TASKS = {
    "scheduling.dispatch_due",
    "notifications.sweep",
    "ai.health_sweep",
}


@contextmanager
def _use_session(session):
    """Stand in for ``session_scope()`` without opening a second connection."""
    yield session


# ---------------------------------------------------------------------------
# Registration and beat wiring
# ---------------------------------------------------------------------------
def test_every_dispatched_task_is_registered() -> None:
    assert DISPATCHED_TASKS <= set(worker.celery_app.tasks)


def test_the_beat_schedule_carries_all_three_periodic_sweeps() -> None:
    schedule = worker.celery_app.conf.beat_schedule
    assert PERIODIC_TASKS <= set(schedule)
    for key, entry in schedule.items():
        assert entry["task"] == key
        assert entry["schedule"] > 0


def test_the_sweep_intervals_come_from_settings() -> None:
    schedule = worker.celery_app.conf.beat_schedule
    assert schedule["scheduling.dispatch_due"]["schedule"] == float(
        settings.scheduling_sweep_interval_seconds
    )
    assert schedule["notifications.sweep"]["schedule"] == float(
        settings.notifications_sweep_interval_seconds
    )
    assert schedule["ai.health_sweep"]["schedule"] == float(
        settings.ai_health_check_interval_seconds
    )


def test_ping_reports_the_worker_is_wired() -> None:
    assert worker.ping() == "pong"


# ---------------------------------------------------------------------------
# Task bodies delegate to their service and return a JSON-serialisable tally
# ---------------------------------------------------------------------------
def test_dispatch_due_runs_the_scheduling_sweep(db, monkeypatch) -> None:
    monkeypatch.setattr(worker, "session_scope", lambda: _use_session(db))

    assert worker.dispatch_due() == {
        "claimed": 0,
        "dispatched": 0,
        "missed": 0,
        "failed": 0,
    }


def test_notifications_sweep_runs_every_sub_sweep(db, monkeypatch) -> None:
    monkeypatch.setattr(worker, "session_scope", lambda: _use_session(db))

    assert worker.notifications_sweep() == {
        "emails_sent": 0,
        "reminders_sent": 0,
        "token_warnings_sent": 0,
    }


def test_ai_health_sweep_probes_the_registry(db, monkeypatch) -> None:
    monkeypatch.setattr(worker, "session_scope", lambda: _use_session(db))

    assert worker.ai_health_sweep() == {"checked": 0, "healthy": 0, "unhealthy": 0}


def test_publish_target_coerces_the_job_id(db, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_run_job(session, job_id):
        seen["session"] = session
        seen["job_id"] = job_id
        return "succeeded"

    monkeypatch.setattr(worker.publishing, "run_job", fake_run_job)
    monkeypatch.setattr(worker, "session_scope", lambda: _use_session(db))

    job_id = uuid.uuid4()
    assert worker.publish_target(str(job_id)) == "succeeded"
    assert seen["job_id"] == job_id
    assert seen["session"] is db


def test_refresh_workspace_coerces_the_id_and_flattens_the_result(db, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_refresh(session, workspace_id):
        seen["session"] = session
        seen["workspace_id"] = workspace_id
        return RefreshResult(
            post_targets_refreshed=2,
            post_targets_skipped=1,
            accounts_refreshed=3,
            accounts_skipped=0,
            snapshots_written=5,
            errors=["instagram: token revoked"],
        )

    monkeypatch.setattr(worker.analytics, "refresh", fake_refresh)
    monkeypatch.setattr(worker, "session_scope", lambda: _use_session(db))

    workspace_id = uuid.uuid4()
    assert worker.refresh_workspace(str(workspace_id)) == {
        "post_targets_refreshed": 2,
        "post_targets_skipped": 1,
        "accounts_refreshed": 3,
        "accounts_skipped": 0,
        "snapshots_written": 5,
        "errors": ["instagram: token revoked"],
    }
    assert seen["workspace_id"] == workspace_id
    assert seen["session"] is db
