from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.build import BuildOrder
    from app.models.farmlist import Farmlist


class Tribe(str, enum.Enum):
    ROMAN = "roman"
    GAUL = "gaul"
    TEUTON = "teuton"
    EGYPTIAN = "egyptian"
    HUN = "hun"
    SPARTAN = "spartan"
    VIKING = "viking"


class Village(Base, TimestampMixin):
    __tablename__ = "villages"
    __table_args__ = (UniqueConstraint("account_id", "travian_id", name="uq_village_traviand_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    travian_id: Mapped[int] = mapped_column(BigInteger)  # in-game newdid
    name: Mapped[str] = mapped_column(String(64))
    x: Mapped[int]
    y: Mapped[int]
    is_capital: Mapped[bool] = mapped_column(default=False)
    tribe: Mapped[Tribe | None] = mapped_column(Enum(Tribe, name="tribe"), default=None)

    # Last-known resource snapshot (updated opportunistically by the scraper)
    wood: Mapped[int] = mapped_column(default=0)
    clay: Mapped[int] = mapped_column(default=0)
    iron: Mapped[int] = mapped_column(default=0)
    crop: Mapped[int] = mapped_column(default=0)
    warehouse_cap: Mapped[int] = mapped_column(default=0)
    granary_cap: Mapped[int] = mapped_column(default=0)

    # Rally-point overview snapshot. Each JSON column is populated by the
    # TroopsController from /build.php?gid=16&tt=1.
    #   troops_json        : {"t1": 6, "t2": 0, ..., "t11": 0}  — own troops home
    #   movements_in_json  : [{"type": "return|attack|reinforce",
    #                          "source": {"x": N, "y": N, "name": str},
    #                          "troops": {"t1": 0...}, "arrival_in_seconds": N}]
    #   movements_out_json : same shape, for our outgoing raids / adventures
    #   troops_consumption : crop per hour that our home troops eat
    #   troops_observed_at : timestamp of the last successful scrape
    troops_json: Mapped[str] = mapped_column(String(2048), default="{}", server_default="{}")
    movements_in_json: Mapped[str] = mapped_column(
        Text, default="[]", server_default="[]"
    )
    movements_out_json: Mapped[str] = mapped_column(
        Text, default="[]", server_default="[]"
    )
    troops_consumption: Mapped[int] = mapped_column(default=0)
    troops_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # Snapshot of the in-game "currently upgrading" list scraped from dorf1's
    # `.buildingList`. Distinct from BuildOrder rows (those track what the bot
    # *wants*); this is what Travian is *actually doing* right now, including
    # upgrades the user started manually.
    #   [{"name": "Clay Pit", "level": 6, "finishes_in_seconds": 612}, ...]
    build_queue_json: Mapped[str] = mapped_column(
        Text, default="[]", server_default="[]"
    )
    # Per-troop minimums that stay home regardless of what farmlists ask for.
    # Applied as a deduction on the home-troop budget at the start of every
    # dispatch tick, so no single raid can drain the reserve. Shape:
    #   {"t1": 10, "t4": 20}   # keep 10 t1s + 20 t4s home
    troops_reserve_json: Mapped[str] = mapped_column(
        Text, default="{}", server_default="{}"
    )

    account: Mapped["Account"] = relationship(back_populates="villages")
    farmlists: Mapped[list["Farmlist"]] = relationship(
        back_populates="village", cascade="all, delete-orphan"
    )
    build_orders: Mapped[list["BuildOrder"]] = relationship(
        back_populates="village", cascade="all, delete-orphan"
    )
