"""Subscription plans and per-workspace subscriptions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.enums import BillingInterval, SubscriptionStatus
from app.db.mixins import TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column

if TYPE_CHECKING:
    from app.models.workspace import Workspace


class Plan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A purchasable plan. Limits are ``NULL`` when the plan is unlimited."""

    __tablename__ = "plans"
    __table_args__ = (CheckConstraint("price_cents >= 0", name="price_non_negative"),)

    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    interval: Mapped[BillingInterval] = mapped_column(
        enum_column(BillingInterval, "billing_interval"),
        nullable=False,
        server_default=BillingInterval.MONTH.value,
    )
    features: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    max_publishes_per_month: Mapped[int | None] = mapped_column(Integer)
    max_social_accounts: Mapped[int | None] = mapped_column(Integer)
    max_workspace_members: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())

    subscriptions: Mapped[list["Subscription"]] = relationship(
        "Subscription",
        back_populates="plan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Plan code={self.code!r} price_cents={self.price_cents}>"


class Subscription(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """Current billing state of a workspace (exactly one row per workspace)."""

    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("workspace_id"),)

    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plans.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        enum_column(SubscriptionStatus, "subscription_status"),
        nullable=False,
        server_default=SubscriptionStatus.TRIALING.value,
    )
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    provider: Mapped[str | None] = mapped_column(String(40))
    provider_customer_id: Mapped[str | None] = mapped_column(String(190))
    provider_subscription_id: Mapped[str | None] = mapped_column(String(190))

    workspace: Mapped["Workspace"] = relationship("Workspace")
    plan: Mapped["Plan"] = relationship("Plan", back_populates="subscriptions")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Subscription workspace={self.workspace_id} status={self.status}>"
