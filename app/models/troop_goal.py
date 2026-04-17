"""Per-village "keep training until I have N" goals.

`troop_key` is the positional tribe-agnostic unit id used everywhere in this
project (`t1`..`t10`). For Gauls, `t4` is Theutates Thunder; for Romans, it's
Equites Caesaris. We don't translate — the UI resolves names from the village
tribe via a small lookup table in `app/data/troops.yaml`.
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class TroopGoal(Base, TimestampMixin):
    __tablename__ = "troop_goals"
    __table_args__ = (
        UniqueConstraint("village_id", "troop_key", name="uq_troop_goal"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    village_id: Mapped[int] = mapped_column(
        ForeignKey("villages.id", ondelete="CASCADE"), index=True
    )
    troop_key: Mapped[str] = mapped_column(String(8))  # "t1".."t10"
    target_count: Mapped[int] = mapped_column(default=0)
    # Lower priority number = train first when resources are tight.
    priority: Mapped[int] = mapped_column(default=100)
    paused: Mapped[bool] = mapped_column(default=False)
