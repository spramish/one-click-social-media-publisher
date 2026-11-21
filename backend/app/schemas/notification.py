"""Request/response schemas for in-app notifications and their preferences.

A notification is addressed to the signed-in user, so no schema carries a
credential and the workspace is optional (``NULL`` for account-level notices).

``is_read`` is derived from ``read_at`` rather than stored twice: one source of
truth, and the UI still gets the boolean it wants to render.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.db.enums import NotificationType
from app.models.notification import Notification, NotificationPreference


class NotificationPublic(BaseModel):
    """One notification as the bell and the inbox render it."""

    id: uuid.UUID
    workspace_id: uuid.UUID | None = None
    type: NotificationType
    title: str
    body: str | None = None
    payload: dict | None = None
    is_read: bool
    read_at: datetime | None = None
    created_at: datetime

    @classmethod
    def from_model(cls, notification: Notification) -> "NotificationPublic":
        return cls(
            id=notification.id,
            workspace_id=notification.workspace_id,
            type=notification.type,
            title=notification.title,
            body=notification.body,
            payload=notification.payload,
            is_read=notification.read_at is not None,
            read_at=notification.read_at,
            created_at=notification.created_at,
        )


class NotificationListPublic(BaseModel):
    """A page of notifications plus the figures the bell needs in one round trip."""

    items: list[NotificationPublic]
    total: int
    unread_count: int
    limit: int
    offset: int


class UnreadCountPublic(BaseModel):
    """The badge count."""

    count: int


class MarkAllReadResult(BaseModel):
    """How many notifications one "mark all read" changed."""

    updated: int


class NotificationPreferencePublic(BaseModel):
    """The user's email opt-outs."""

    email_publish_success: bool
    email_publish_failure: bool
    email_schedule_reminder: bool
    email_token_expiring: bool
    updated_at: datetime | None = None

    @classmethod
    def from_model(
        cls, preference: NotificationPreference
    ) -> "NotificationPreferencePublic":
        return cls(
            email_publish_success=preference.email_publish_success,
            email_publish_failure=preference.email_publish_failure,
            email_schedule_reminder=preference.email_schedule_reminder,
            email_token_expiring=preference.email_token_expiring,
            updated_at=preference.updated_at,
        )


class NotificationPreferenceUpdate(BaseModel):
    """A partial update — only the flags present are changed."""

    email_publish_success: bool | None = Field(default=None)
    email_publish_failure: bool | None = Field(default=None)
    email_schedule_reminder: bool | None = Field(default=None)
    email_token_expiring: bool | None = Field(default=None)


__all__ = [
    "MarkAllReadResult",
    "NotificationListPublic",
    "NotificationPreferencePublic",
    "NotificationPreferenceUpdate",
    "NotificationPublic",
    "UnreadCountPublic",
]
