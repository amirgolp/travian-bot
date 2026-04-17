"""A single tile on the game map: a village, an oasis, a Natar, or an unset slot.

Why a dedicated model:
- Farmlist slots should target an *entity*, not bare (x, y) coords. If a village
  changes hands or an oasis gets occupied, we need one place to update.
- Raid reports need to attach to the target so we can answer "how has this oasis
  performed as a farm over time" — that drives farmlist maintenance.
- World sync (map.sql) and in-game map scan (oases/natars) both write here.

Uniqueness is (server_code, x, y) — a tile is identified by the map position on
a specific gameworld, not by a Travian id which isn't stable across sources.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.farmlist import FarmlistSlot
    from app.models.raid import Raid
    from app.models.report import Report


class TileType(str, enum.Enum):
    VILLAGE = "village"       # owned by another player
    NATAR = "natar"           # Natars NPC village (uid=1 / tribe=5)
    OASIS = "oasis"           # unoccupied oasis
    OWN_VILLAGE = "own"       # one of my villages (rarely a raid target, but useful)
    UNKNOWN = "unknown"


class MapTile(Base, TimestampMixin):
    __tablename__ = "map_tiles"
    __table_args__ = (
        UniqueConstraint("server_code", "x", "y", name="uq_map_tile_coord"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    server_code: Mapped[str] = mapped_column(String(64), index=True)  # e.g. "legends-international-ts1-x1"
    x: Mapped[int] = mapped_column(index=True)
    y: Mapped[int] = mapped_column(index=True)

    type: Mapped[TileType] = mapped_column(
        Enum(TileType, name="tile_type"), default=TileType.UNKNOWN, index=True
    )

    # Descriptive metadata (sourced from map.sql / in-game scrape)
    name: Mapped[str | None] = mapped_column(String(128), default=None)
    tribe: Mapped[int | None] = mapped_column(default=None)         # 1=Roman..7, 5=Natars
    population: Mapped[int | None] = mapped_column(default=None)
    village_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    player_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    player_name: Mapped[str | None] = mapped_column(String(128), default=None)
    alliance_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    alliance_name: Mapped[str | None] = mapped_column(String(128), default=None)

    # Oasis-specific (populated by MapScanController)
    oasis_type: Mapped[str | None] = mapped_column(String(32), default=None)  # e.g. "wood_25"

    # Scan-sourced observer hints — what the map tooltip shows the scanning
    # player about *their own* last raid on this tile. Lossy and per-observer;
    # ReportsController remains authoritative for last_raid_at / outcome. Kept
    # separate so UI can surface "fresh loot" signals even when reports haven't
    # been ingested yet.
    scan_bounty_tier: Mapped[int | None] = mapped_column(default=None)       # 0 empty / 1 half / 2 full
    scan_bounty_pct: Mapped[int | None] = mapped_column(default=None)        # current/max * 100
    scan_last_raid_outcome: Mapped[int | None] = mapped_column(default=None) # 1..7 (see b.riN keys)
    scan_last_raid_text: Mapped[str | None] = mapped_column(String(32), default=None)  # raw "today, HH:MM" / "DD.MM.YY, HH:MM"

    # Raid aggregates — updated by ReportsController.
    raid_count: Mapped[int] = mapped_column(default=0)
    win_count: Mapped[int] = mapped_column(default=0)
    loss_count: Mapped[int] = mapped_column(default=0)
    empty_count: Mapped[int] = mapped_column(default=0)
    total_bounty: Mapped[int] = mapped_column(default=0)
    last_raid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Outcome of the most recent raid — distinct from the lifetime counters
    # above so the UI can colour rows by *current* state ("last raid was
    # empty") rather than a wash of historical totals. Populated from
    # `_apply_to_tile` on every report ingest.
    last_raid_outcome: Mapped[str | None] = mapped_column(String(16), default=None)
    last_raid_capacity_pct: Mapped[int | None] = mapped_column(default=None)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # Pre-raid oasis animal cache. `{"u35": 7, "u36": 5}` means animals present;
    # `"{}"` means known-clean. NULL means never checked. Populated by
    # farming dispatch via /api/v1/map/tile-details; TTL'd by
    # `animals_checked_at` so we don't re-fetch every tick.
    animals_json: Mapped[str | None] = mapped_column(String(256), default=None)
    animals_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # Relationships
    reports: Mapped[list["Report"]] = relationship(back_populates="tile")
    raids: Mapped[list["Raid"]] = relationship(back_populates="tile")
    farmlist_slots: Mapped[list["FarmlistSlot"]] = relationship(back_populates="tile")
