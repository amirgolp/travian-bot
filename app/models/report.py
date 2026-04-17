from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.map_tile import MapTile


class ReportType(str, enum.Enum):
    RAID_WIN = "raid_win"
    RAID_LOSS = "raid_loss"
    RAID_EMPTY = "raid_empty"
    DEFENSE = "defense"
    SCOUT = "scout"
    TRADE = "trade"
    OTHER = "other"


class Report(Base, TimestampMixin):
    """Parsed in-game report — primary input for farmlist maintenance."""

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    tile_id: Mapped[int | None] = mapped_column(
        ForeignKey("map_tiles.id", ondelete="SET NULL"), default=None, index=True
    )
    # The attacker-side village for this report. For our outgoing raids this
    # is one of our villages; used to match reports back to the specific
    # farmlist slot that fired (same (village_id, tile_id) pair).
    source_village_id: Mapped[int | None] = mapped_column(
        ForeignKey("villages.id", ondelete="SET NULL"), default=None, index=True
    )
    travian_report_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    type: Mapped[ReportType] = mapped_column(
        Enum(ReportType, name="report_type"), default=ReportType.OTHER
    )
    when: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    target_x: Mapped[int | None] = mapped_column(default=None)
    target_y: Mapped[int | None] = mapped_column(default=None)

    # Parsed bounty (populated by ReportsController)
    bounty_wood: Mapped[int] = mapped_column(default=0)
    bounty_clay: Mapped[int] = mapped_column(default=0)
    bounty_iron: Mapped[int] = mapped_column(default=0)
    bounty_crop: Mapped[int] = mapped_column(default=0)
    bounty_total: Mapped[int] = mapped_column(default=0)
    capacity_used_pct: Mapped[int | None] = mapped_column(default=None)  # e.g. 95 means troops came home 95% full

    raw_html: Mapped[str | None] = mapped_column(Text, default=None)
    parsed_json: Mapped[str | None] = mapped_column(Text, default=None)

    tile: Mapped["MapTile | None"] = relationship(back_populates="reports")
