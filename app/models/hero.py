"""Hero state per account — populated by HeroController from hero.php* pages.

One row per account (upsert). Fields start None and fill in as the scraper
matures; the user can drop more samples to extend parsing.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class HeroStats(Base, TimestampMixin):
    __tablename__ = "hero_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), unique=True, index=True
    )

    # Core vitals
    health_pct: Mapped[int | None] = mapped_column(default=None)          # 0-100
    experience: Mapped[int | None] = mapped_column(default=None)
    speed_fph: Mapped[int | None] = mapped_column(default=None)            # fields per hour
    production_per_hour: Mapped[int | None] = mapped_column(default=None)  # +N resources/h
    fighting_strength: Mapped[int | None] = mapped_column(default=None)
    off_bonus_pct: Mapped[float | None] = mapped_column(default=None)
    def_bonus_pct: Mapped[float | None] = mapped_column(default=None)

    # Attribute points available to spend
    attribute_points: Mapped[int] = mapped_column(default=0)

    # Location / status
    home_village_id: Mapped[int | None] = mapped_column(default=None)  # travian_id
    status: Mapped[str | None] = mapped_column(String(32), default=None)  # "home" | "moving" | "mission" | "dead"

    # Adventures
    adventures_available: Mapped[int] = mapped_column(default=0)

    # Inventory snapshot. `equipment_json` is a list of dicts
    # `[{slot, empty, rarity, quality}, ...]`, `bag_count` is the number of
    # non-empty consumable cells in the bag. Enough to surface "hero is
    # fully kitted" / "hero has unused potions" on the dashboard without
    # requiring a per-server item name dictionary.
    equipment_json: Mapped[str] = mapped_column(
        Text, default="[]", server_default="[]"
    )
    bag_count: Mapped[int] = mapped_column(default=0)
    # Per-cell breakdown of the consumable grid. Each entry:
    #   {"item_type_id": 102, "count": 60,
    #    "name": "Ointment", "description": "+5 health when used"}
    # Non-equipment items (ointment, bandages, cages, tablets, ...).
    bag_items_json: Mapped[str] = mapped_column(
        Text, default="[]", server_default="[]"
    )

    # Observed at — set on every sync so the UI can show staleness.
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
