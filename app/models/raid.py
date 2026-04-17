from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.map_tile import MapTile


class RaidStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    DISPATCHED = "dispatched"
    RETURNED = "returned"
    FAILED = "failed"


class Raid(Base, TimestampMixin):
    """An outgoing raid — scheduled, dispatched, or resolved."""

    __tablename__ = "raids"

    id: Mapped[int] = mapped_column(primary_key=True)
    village_id: Mapped[int] = mapped_column(ForeignKey("villages.id", ondelete="CASCADE"), index=True)
    farmlist_id: Mapped[int | None] = mapped_column(
        ForeignKey("farmlists.id", ondelete="SET NULL"), default=None
    )
    slot_id: Mapped[int | None] = mapped_column(
        ForeignKey("farmlist_slots.id", ondelete="SET NULL"), default=None
    )
    tile_id: Mapped[int | None] = mapped_column(
        ForeignKey("map_tiles.id", ondelete="SET NULL"), default=None, index=True
    )

    target_x: Mapped[int]
    target_y: Mapped[int]
    troops_json: Mapped[str] = mapped_column(String(512), default="{}")
    status: Mapped[RaidStatus] = mapped_column(
        Enum(RaidStatus, name="raid_status"), default=RaidStatus.SCHEDULED
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    return_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    bounty_wood: Mapped[int] = mapped_column(default=0)
    bounty_clay: Mapped[int] = mapped_column(default=0)
    bounty_iron: Mapped[int] = mapped_column(default=0)
    bounty_crop: Mapped[int] = mapped_column(default=0)
    losses_json: Mapped[str] = mapped_column(String(512), default="{}")

    tile: Mapped["MapTile | None"] = relationship(back_populates="raids")
