"""Request/response schemas for content scheduling.

The shape of a schedule is deliberately small: *when* it fires (in UTC, plus the
IANA zone the user actually chose), *what state* it is in, and *which post* it
belongs to. The destinations are not repeated here — they live on the post's
targets, which the schedule shares with a manual publish.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.db.enums import PostStatus, ScheduleStatus
from app.models.scheduling import ScheduledPost
from app.schemas.post import TargetSelectionRequest
from app.services import scheduling as scheduling_service


class SchedulePublic(BaseModel):
    """One calendar entry: a post and the moment it will be published.

    ``scheduled_for`` is the UTC instant that is stored; ``local_time`` is that
    same instant rendered in ``timezone``, so a client can show the time the user
    picked without re-implementing tz math.
    """

    id: uuid.UUID
    workspace_id: uuid.UUID
    post_id: uuid.UUID
    scheduled_for: datetime
    timezone: str
    local_time: datetime
    status: ScheduleStatus
    dispatched_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancelled_by_id: uuid.UUID | None = None
    post_status: PostStatus
    post_title: str | None = None
    media_file_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_schedule(cls, schedule: ScheduledPost) -> "SchedulePublic":
        post = schedule.post
        return cls(
            id=schedule.id,
            workspace_id=schedule.workspace_id,
            post_id=schedule.post_id,
            scheduled_for=schedule.scheduled_for,
            timezone=schedule.timezone,
            local_time=scheduling_service.local_time(schedule),
            status=schedule.status,
            dispatched_at=schedule.dispatched_at,
            cancelled_at=schedule.cancelled_at,
            cancelled_by_id=schedule.cancelled_by_id,
            post_status=post.status,
            post_title=post.title,
            media_file_id=post.media_file_id,
            created_at=schedule.created_at,
            updated_at=schedule.updated_at,
        )


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
class ScheduleCreateRequest(BaseModel):
    """Queue a composed post for a future time, to the chosen destinations."""

    post_id: uuid.UUID
    #: The moment to publish. A naive value is read in ``timezone``; an aware one
    #: is converted to UTC. Either way UTC is what gets stored.
    scheduled_for: datetime
    #: An IANA zone name (``Europe/London``). Defaults to the server's zone.
    timezone: str | None = Field(default=None, max_length=64)
    targets: list[TargetSelectionRequest] = Field(min_length=1)


class ScheduleUpdateRequest(BaseModel):
    """Move a queued post to a different time."""

    scheduled_for: datetime
    timezone: str | None = Field(default=None, max_length=64)


__all__ = [
    "ScheduleCreateRequest",
    "SchedulePublic",
    "ScheduleUpdateRequest",
]
