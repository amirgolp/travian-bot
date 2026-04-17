"""Strategy gates: checkpoints in a build plan that need human/heuristic input.

A strategy (see ``app/services/strategy.py``) can contain steps the reconciler
cannot resolve on its own — e.g. "ask your team leader where to settle", "clear
close oases", "switch hero to raid mode if raids are viable". Those become rows
in this table, priority-ordered alongside ``BuildOrder`` rows. The build
controller treats a ``pending`` gate at a given priority as a hard stop until
the gate is resolved (either automatically by a policy controller, or manually
from the dashboard).
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.village import Village


class StrategyGateKind(str, enum.Enum):
    MANUAL = "manual"              # free-text prompt → dashboard resolves
    CLEAR_OASES = "clear_oases"    # triggers oasis-scan + raid policy
    HERO_MODE = "hero_mode"        # switch hero dispatch policy
    SETTLE = "settle"              # pick a coordinate for the next village


class StrategyGateStatus(str, enum.Enum):
    PENDING = "pending"
    RESOLVED = "resolved"
    SKIPPED = "skipped"


class StrategyGate(Base, TimestampMixin):
    __tablename__ = "strategy_gates"

    id: Mapped[int] = mapped_column(primary_key=True)
    village_id: Mapped[int] = mapped_column(
        ForeignKey("villages.id", ondelete="CASCADE"), index=True
    )

    step: Mapped[int]  # mirrors the PDF row id for traceability
    kind: Mapped[StrategyGateKind] = mapped_column(
        Enum(StrategyGateKind, name="strategy_gate_kind")
    )
    # Sorts alongside BuildOrder.priority so the controller sees them in sequence.
    priority: Mapped[int] = mapped_column(default=100)

    prompt: Mapped[str | None] = mapped_column(String(512), default=None)
    rule: Mapped[str | None] = mapped_column(String(1024), default=None)

    status: Mapped[StrategyGateStatus] = mapped_column(
        Enum(StrategyGateStatus, name="strategy_gate_status"),
        default=StrategyGateStatus.PENDING,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    resolution_note: Mapped[str | None] = mapped_column(String(512), default=None)

    village: Mapped[Village] = relationship()
