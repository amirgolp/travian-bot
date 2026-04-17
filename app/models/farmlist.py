from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.map_tile import MapTile
    from app.models.village import Village


class FarmlistKind(str, enum.Enum):
    """What this list is for. Lets two separate pipelines populate it.

    VILLAGES — fed by WorldSqlController from nightly map.sql.
    OASES_NATARS — fed by MapScanController (in-game map scrape) — typically
                   refreshed every 24 h since oases change ownership.
    MIXED — user-curated, never auto-populated.
    """
    VILLAGES = "villages"
    OASES_NATARS = "oases_natars"
    MIXED = "mixed"


class Farmlist(Base, TimestampMixin):
    """A Rally Point farm list. Mirrors the in-game list."""

    __tablename__ = "farmlists"
    __table_args__ = (UniqueConstraint("village_id", "name", name="uq_farmlist_name_per_village"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    village_id: Mapped[int] = mapped_column(ForeignKey("villages.id", ondelete="CASCADE"), index=True)
    travian_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[FarmlistKind] = mapped_column(
        Enum(FarmlistKind, name="farmlist_kind"), default=FarmlistKind.MIXED
    )

    interval_seconds: Mapped[int] = mapped_column(default=1800)
    enabled: Mapped[bool] = mapped_column(default=True)

    # Default troop composition applied to auto-added slots.
    # Stored as JSON string like {"t4": 10}. Per-slot overrides live on FarmlistSlot.
    default_troops_json: Mapped[str] = mapped_column(String(512), default="{}")

    village: Mapped["Village"] = relationship(back_populates="farmlists")
    slots: Mapped[list["FarmlistSlot"]] = relationship(
        back_populates="farmlist", cascade="all, delete-orphan"
    )


class FarmlistSlot(Base, TimestampMixin):
    """One target in a farmlist — points to a MapTile."""

    __tablename__ = "farmlist_slots"
    __table_args__ = (
        UniqueConstraint("farmlist_id", "tile_id", name="uq_farmlist_slot_tile"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farmlist_id: Mapped[int] = mapped_column(
        ForeignKey("farmlists.id", ondelete="CASCADE"), index=True
    )
    tile_id: Mapped[int] = mapped_column(
        ForeignKey("map_tiles.id", ondelete="CASCADE"), index=True
    )
    # Override the list default.
    troops_json: Mapped[str] = mapped_column(String(512), default="")
    enabled: Mapped[bool] = mapped_column(default=True)
    consecutive_losses: Mapped[int] = mapped_column(default=0)
    last_raid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    farmlist: Mapped["Farmlist"] = relationship(back_populates="slots")
    tile: Mapped["MapTile"] = relationship(back_populates="farmlist_slots")
