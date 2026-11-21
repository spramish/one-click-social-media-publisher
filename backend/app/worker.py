"""Celery application.

Background jobs (publishing, scheduling, notifications) are dispatched here by
name. Publishing itself is implemented in
:mod:`app.services.publishing`; this module only owns the task entry points.
"""

import dataclasses
import uuid

from celery import Celery

from app.core.config import settings
from app.db.session import session_scope
from app.services import ai_router, analytics, notifications, publishing, scheduling

celery_app = Celery(
    "one_click_publisher",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

#: The due-sweep that turns "scheduled" into "published". A periodic task rather
#: than a per-post countdown, because the schedule lives in PostgreSQL: a broker
#: restart, a worker restart, or a machine that was asleep all simply mean the
#: sweep catches up on its next tick, and the ``FOR UPDATE SKIP LOCKED`` claim
#: guarantees each entry still fires exactly once.
celery_app.conf.beat_schedule = {
    "scheduling.dispatch_due": {
        "task": "scheduling.dispatch_due",
        "schedule": float(settings.scheduling_sweep_interval_seconds),
    },
    # One tick both emails pending notifications and raises the time-based ones
    # (upcoming-publish reminders, access-expiry warnings). Each sub-sweep is
    # idempotent, so the interval only decides how promptly a notice appears.
    "notifications.sweep": {
        "task": "notifications.sweep",
        "schedule": float(settings.notifications_sweep_interval_seconds),
    },
    # Availability monitoring. A tic that re-probes idle models so the registry
    # shows a current status; it never gates traffic, because the router always
    # walks the whole priority list and a recovered primary simply answers first
    # again.
    "ai.health_sweep": {
        "task": "ai.health_sweep",
        "schedule": float(settings.ai_health_check_interval_seconds),
    },
}


@celery_app.task(name="health.ping")
def ping() -> str:
    """Trivial task used to verify the worker + broker wiring."""
    return "pong"


@celery_app.task(name="publishing.publish_target")
def publish_target(job_id: str) -> str:
    """Execute one publishing job (one target, one attempt).

    The orchestration service owns every reliability concern — retries with
    exponential backoff, rate-limit handling, dead-lettering — so this task
    never re-raises: the attempt's outcome is recorded on the job row and, when
    retryable, the next attempt is re-dispatched with a countdown.

    Returns the job's resulting status name so the outcome is visible in the
    Celery result backend.
    """
    with session_scope() as db:
        return publishing.run_job(db, uuid.UUID(job_id))


@celery_app.task(name="scheduling.dispatch_due")
def dispatch_due() -> dict[str, int]:
    """Publish every scheduled post whose time has come.

    Claims the due entries atomically, then hands each one to the Phase 7
    publishing path, so a scheduled publish creates the same per-target jobs with
    the same idempotency keys and retry behaviour as a manual one. Returns the
    sweep's tallies for the Celery result backend.
    """
    with session_scope() as db:
        return scheduling.sweep_due(db).as_dict()


@celery_app.task(name="analytics.refresh_workspace")
def refresh_workspace(workspace_id: str) -> dict[str, object]:
    """Pull fresh metrics for one workspace's published targets and accounts.

    The same sweep the ``POST /analytics/{workspace_id}/refresh`` route runs,
    exposed so a deployment can move it off the request path. A failure on one
    destination is collected in ``errors`` rather than raised, so a single
    revoked grant never stops the rest of the workspace from updating.
    """
    with session_scope() as db:
        result = analytics.refresh(db, uuid.UUID(workspace_id))
        return dataclasses.asdict(result)


@celery_app.task(name="notifications.sweep")
def notifications_sweep() -> dict[str, int]:
    """Deliver notification emails and raise the time-based notices.

    Three idempotent sub-sweeps in one tick: email the notifications still
    awaiting a delivery decision, remind the owner of each post publishing
    within the lead window, and warn about connected accounts whose access
    token is about to expire. Returns the per-sub-sweep tallies.
    """
    with session_scope() as db:
        return notifications.sweep(db).as_dict()


@celery_app.task(name="ai.health_sweep")
def ai_health_sweep() -> dict[str, int]:
    """Re-probe every enabled AI model's availability.

    Ordinary routing already refreshes a busy model's health on each call, so
    this only covers the idle ones. It never gates traffic: the router always
    walks the whole priority list, which is exactly how a recovered primary
    takes over again with no operator action. Returns the sweep's tallies.
    """
    with session_scope() as db:
        return ai_router.health_sweep(db).as_dict()

