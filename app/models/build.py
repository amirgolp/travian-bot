from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.village import Village


class BuildOrderStatus(str, enum.Enum):
    QUEUED = "queued"
    BLOCKED = "blocked"      # prereqs not yet met
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BuildOrder(Base, TimestampMixin):
    """One step in the upgrade pipeline: bring `building_key` in `slot` to `target_level`."""

    __tablename__ = "build_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    village_id: Mapped[int] = mapped_column(ForeignKey("villages.id", ondelete="CASCADE"), index=True)

    building_key: Mapped[str] = mapped_column(String(64))  # matches key in data/buildings.yaml
    slot: Mapped[int | None] = mapped_column(default=None)  # slot id 1..40 (dorf1 1..18, dorf2 19..40). None = any free slot
    target_level: Mapped[int] = mapped_column(default=1)
    priority: Mapped[int] = mapped_column(default=100)  # lower = earlier

    status: Mapped[BuildOrderStatus] = mapped_column(
        Enum(BuildOrderStatus, name="build_order_status"), default=BuildOrderStatus.QUEUED
    )
    blocked_reason: Mapped[str | None] = mapped_column(String(255), default=None)
    completes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    village: Mapped["Village"] = relationship(back_populates="build_orders")


class BuildingSlot(Base, TimestampMixin):
    """Cached view of what building currently sits in each slot and at what level."""

    __tablename__ = "building_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    village_id: Mapped[int] = mapped_column(ForeignKey("villages.id", ondelete="CASCADE"), index=True)
    slot: Mapped[int]  # 1..40
    building_key: Mapped[str | None] = mapped_column(String(64), default=None)
    level: Mapped[int] = mapped_column(default=0)
