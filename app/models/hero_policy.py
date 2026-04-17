"""Per-account hero policy: how the hero controller should pick adventures etc.

Lives on ``accounts`` because a hero is account-scoped in Travian (one hero
per world login, moving between villages). The policy is sourced from a
strategy YAML via ``apply_compiled_strategy`` but can also be edited directly
from the dashboard.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.account import Account


class HeroPolicy(Base, TimestampMixin):
    __tablename__ = "hero_policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), unique=True, index=True
    )

    # JSON-encoded list of AdventureReward dicts:
    #   [{"index": 1, "prefer": "horse", "post": "keep"}, ...]
    # Stored as text (not JSONB) to match the rest of this project's SQLite-
    # friendly conventions; parsed at read time by the hero controller.
    adventure_rewards_json: Mapped[str] = mapped_column(
        Text, default="[]", server_default="[]"
    )

    account: Mapped[Account] = relationship()
