"""Strategy schema, loader, and compiler.

A *strategy* is a YAML encoding of a human-authored build guide (see
``samples/legend/X10 Egyptian Eco - Ark1.pdf`` for the first one we modelled).
The file lives under ``app/data/strategies/`` and is consumed in two stages:

    raw yaml  ──load_strategy──▶  Strategy (pydantic)  ──compile_strategy──▶  CompiledStrategy

``CompiledStrategy`` carries the DB-row kwargs (``BuildOrderRow``,
``TroopGoalRow``), any ``Gate`` checkpoints that need human/heuristic
resolution, and the hero policy that the hero controller will read. A thin DB
adapter (not in this module) takes a ``CompiledStrategy`` plus a ``village_id``
and inserts the actual ORM rows.

The split keeps the compiler pure: every test can round-trip YAML → typed rows
without touching the database or the reconciler.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.build import BuildOrder
from app.models.hero_policy import HeroPolicy as HeroPolicyRow
from app.models.strategy_gate import StrategyGate, StrategyGateKind, StrategyGateStatus
from app.models.troop_goal import TroopGoal
from app.models.village import Village
from app.services.building_data import load_buildings

FieldType = Literal["woodcutter", "clay_pit", "iron_mine", "cropland"]
GateKind = Literal["manual", "clear_oases", "hero_mode", "settle"]
HeroActionKind = Literal["adventure", "toggle_plus"]
AdventurePrize = Literal[
    "horse", "resources", "troops", "silver", "ointments", "books", "cages", "xp"
]


class BuildingStep(BaseModel):
    key: str
    level: int = Field(ge=1)


class FieldsStep(BaseModel):
    """Upgrade ``count`` slots of a given resource type to ``level``.

    The compiler emits one ``BuildOrderRow`` per slot with ``slot=None``;
    the building controller picks a free matching slot at dispatch time.
    """

    type: FieldType
    count: int = Field(ge=1, le=18)
    level: int = Field(ge=1, le=20)


class HeroStep(BaseModel):
    action: HeroActionKind
    detail: str | None = None


class GateStep(BaseModel):
    kind: GateKind
    prompt: str | None = None
    rule: str | None = None


class Step(BaseModel):
    """One row in the PDF table. Exactly one of the four payload fields is set."""

    step: int = Field(ge=1)
    note: str | None = None
    building: BuildingStep | None = None
    fields: FieldsStep | None = None
    hero: HeroStep | None = None
    gate: GateStep | None = None

    @model_validator(mode="after")
    def _at_most_one_payload(self) -> Step:
        payloads = (self.building, self.fields, self.hero, self.gate)
        n = sum(p is not None for p in payloads)
        if n > 1:
            raise ValueError(
                f"step {self.step}: at most one of building/fields/hero/gate allowed, got {n}"
            )
        if n == 0 and not self.note:
            raise ValueError(
                f"step {self.step}: empty step — set one of building/fields/hero/gate, or a note"
            )
        return self


class AdventureReward(BaseModel):
    index: int = Field(ge=1)
    prefer: AdventurePrize
    # "books" usually get converted to resource-boost attributes after Adv 6.
    post: Literal["keep", "convert_to_resources"] = "keep"


class HeroPolicy(BaseModel):
    adventure_rewards: list[AdventureReward] = Field(default_factory=list)


class TroopPlan(BaseModel):
    """Opens a TroopGoal once the named build step has been reached."""

    after_step: int = Field(ge=1)
    troop: str = Field(pattern=r"^t([1-9]|10)$")
    target: int = Field(ge=0)
    note: str | None = None


class StrategyMeta(BaseModel):
    name: str
    tribe: Literal["roman", "gaul", "teuton", "egyptian", "hun", "spartan", "viking"]
    server_speed: int = Field(ge=1)
    goal: str | None = None
    source: str | None = None


class Strategy(BaseModel):
    meta: StrategyMeta
    build: list[Step]
    hero: HeroPolicy | None = None
    troops: list[TroopPlan] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_references(self) -> Strategy:
        known_buildings = set(load_buildings().keys())
        for s in self.build:
            if s.building is not None and s.building.key not in known_buildings:
                raise ValueError(
                    f"step {s.step}: unknown building key {s.building.key!r} "
                    f"(not in app/data/buildings.yaml)"
                )
        step_ids = {s.step for s in self.build}
        for t in self.troops:
            if t.after_step not in step_ids:
                raise ValueError(
                    f"troop plan for {t.troop} references step {t.after_step} "
                    f"which is not in build[]"
                )
        return self


_STRATEGIES_DIR = Path(__file__).resolve().parents[1] / "data" / "strategies"


def load_strategy(path: str | Path) -> Strategy:
    """Load and validate a strategy YAML. Relative paths resolve under data/strategies/."""
    p = Path(path)
    if not p.is_absolute():
        p = _STRATEGIES_DIR / p
    with p.open() as fh:
        raw = yaml.safe_load(fh)
    return Strategy.model_validate(raw)


@lru_cache(maxsize=16)
def get_strategy(name: str) -> Strategy:
    """Look up a bundled strategy by stem (``.yaml`` extension optional)."""
    fname = name if name.endswith(".yaml") else f"{name}.yaml"
    return load_strategy(fname)


@dataclass(frozen=True)
class BuildOrderRow:
    """Kwargs for ``BuildOrder(...)``. ``slot=None`` lets the controller pick."""

    building_key: str
    target_level: int
    priority: int
    slot: int | None = None


@dataclass(frozen=True)
class TroopGoalRow:
    """Kwargs for ``TroopGoal(...)``."""

    troop_key: str
    target_count: int
    priority: int


@dataclass(frozen=True)
class HeroAction:
    priority: int
    action: HeroActionKind
    detail: str | None


@dataclass(frozen=True)
class Gate:
    """A step the reconciler cannot resolve on its own (human/heuristic input)."""

    step: int
    kind: GateKind
    priority: int
    prompt: str | None
    rule: str | None


@dataclass
class CompiledStrategy:
    build_orders: list[BuildOrderRow] = field(default_factory=list)
    troop_goals: list[TroopGoalRow] = field(default_factory=list)
    hero_actions: list[HeroAction] = field(default_factory=list)
    gates: list[Gate] = field(default_factory=list)
    hero_policy: HeroPolicy | None = None


def compile_strategy(
    strategy: Strategy,
    *,
    priority_base: int = 1000,
    step_width: int = 10,
) -> CompiledStrategy:
    """Expand a ``Strategy`` into DB-row kwargs + gates.

    Priority layout: each PDF step occupies ``[base + step*width, base + step*width + width)``.
    Multi-row steps (e.g. "all wood to 1" → 4 rows) use sub-offsets inside their block;
    manual inserts later can slot into the gaps.
    """
    out = CompiledStrategy(hero_policy=strategy.hero)
    for s in strategy.build:
        slot_base = priority_base + s.step * step_width
        if s.building is not None:
            out.build_orders.append(
                BuildOrderRow(
                    building_key=s.building.key,
                    target_level=s.building.level,
                    priority=slot_base,
                )
            )
        elif s.fields is not None:
            for i in range(s.fields.count):
                out.build_orders.append(
                    BuildOrderRow(
                        building_key=s.fields.type,
                        target_level=s.fields.level,
                        priority=slot_base + i,
                    )
                )
        elif s.hero is not None:
            out.hero_actions.append(
                HeroAction(priority=slot_base, action=s.hero.action, detail=s.hero.detail)
            )
        elif s.gate is not None:
            out.gates.append(
                Gate(
                    step=s.step,
                    kind=s.gate.kind,
                    priority=slot_base,
                    prompt=s.gate.prompt,
                    rule=s.gate.rule,
                )
            )

    for t in strategy.troops:
        out.troop_goals.append(
            TroopGoalRow(
                troop_key=t.troop,
                target_count=t.target,
                priority=priority_base + t.after_step * step_width + 5,
            )
        )
    return out


@dataclass(frozen=True)
class ApplyResult:
    build_orders_inserted: int
    troop_goals_upserted: int
    gates_inserted: int
    hero_policy_written: bool = False


async def apply_compiled_strategy(
    session: AsyncSession,
    village_id: int,
    compiled: CompiledStrategy,
) -> ApplyResult:
    """Persist a ``CompiledStrategy`` against one village.

    - ``BuildOrder`` rows are **appended** (never cleared) so an existing queue
      is preserved; the caller is responsible for wiping stale rows if needed.
    - ``TroopGoal`` rows **upsert** on ``(village_id, troop_key)`` — re-applying
      the same strategy updates targets in place instead of failing the unique
      constraint.
    - ``StrategyGate`` rows are always inserted fresh; gates are per-application.
    - ``hero_policy`` is upserted to a per-account ``HeroPolicy`` row
      (resolved from ``village.account_id``) when present.
    - ``hero_actions`` (inline hero dispatches from ``action: adventure`` /
      ``toggle_plus`` steps) are **not** persisted — they are one-shot signals
      that the caller feeds to the hero controller in-memory.
    """
    for row in compiled.build_orders:
        session.add(
            BuildOrder(
                village_id=village_id,
                building_key=row.building_key,
                target_level=row.target_level,
                slot=row.slot,
                priority=row.priority,
            )
        )

    existing_goals = {
        g.troop_key: g
        for g in (
            await session.scalars(
                select(TroopGoal).where(TroopGoal.village_id == village_id)
            )
        ).all()
    }
    for row in compiled.troop_goals:
        if (existing := existing_goals.get(row.troop_key)) is not None:
            existing.target_count = row.target_count
            existing.priority = row.priority
        else:
            session.add(
                TroopGoal(
                    village_id=village_id,
                    troop_key=row.troop_key,
                    target_count=row.target_count,
                    priority=row.priority,
                )
            )

    for gate in compiled.gates:
        session.add(
            StrategyGate(
                village_id=village_id,
                step=gate.step,
                kind=StrategyGateKind(gate.kind),
                priority=gate.priority,
                prompt=gate.prompt,
                rule=gate.rule,
                status=StrategyGateStatus.PENDING,
            )
        )

    hero_policy_written = False
    if compiled.hero_policy is not None:
        village = await session.get(Village, village_id)
        if village is None:
            raise ValueError(f"village {village_id} not found")
        existing_hp = (
            await session.scalars(
                select(HeroPolicyRow).where(HeroPolicyRow.account_id == village.account_id)
            )
        ).one_or_none()
        rewards_json = json.dumps(
            [r.model_dump() for r in compiled.hero_policy.adventure_rewards],
            separators=(",", ":"),
        )
        if existing_hp is None:
            session.add(
                HeroPolicyRow(
                    account_id=village.account_id,
                    adventure_rewards_json=rewards_json,
                )
            )
        else:
            existing_hp.adventure_rewards_json = rewards_json
        hero_policy_written = True

    await session.flush()
    return ApplyResult(
        build_orders_inserted=len(compiled.build_orders),
        troop_goals_upserted=len(compiled.troop_goals),
        gates_inserted=len(compiled.gates),
        hero_policy_written=hero_policy_written,
    )


async def get_hero_policy(
    session: AsyncSession, account_id: int
) -> HeroPolicy | None:
    """Return the persisted ``HeroPolicy`` for an account, or None if none applied.

    Reads the JSON column written by ``apply_compiled_strategy`` and reconstructs
    the pydantic model so callers (e.g. the hero controller) can work in the
    same type as the YAML did.
    """
    row = (
        await session.scalars(
            select(HeroPolicyRow).where(HeroPolicyRow.account_id == account_id)
        )
    ).one_or_none()
    if row is None:
        return None
    return HeroPolicy.model_validate(
        {"adventure_rewards": json.loads(row.adventure_rewards_json)}
    )


def expected_reward(
    policy: HeroPolicy, adventure_number: int
) -> AdventureReward | None:
    """Return the reward entry for the Nth adventure (1-indexed), or None.

    Adventures beyond the encoded plan return None — the hero controller
    should treat that as "no preference, any reward is fine".
    """
    for entry in policy.adventure_rewards:
        if entry.index == adventure_number:
            return entry
    return None


async def pending_gate_priority(session: AsyncSession, village_id: int) -> int | None:
    """Return the priority of the lowest-priority PENDING gate for a village, or None.

    The build controller uses this as a cutoff: any BuildOrder with ``priority``
    ≥ this value is waiting on the gate and must not dispatch until the gate is
    resolved or skipped.
    """
    row = (
        await session.scalars(
            select(StrategyGate)
            .where(
                StrategyGate.village_id == village_id,
                StrategyGate.status == StrategyGateStatus.PENDING,
            )
            .order_by(StrategyGate.priority.asc())
            .limit(1)
        )
    ).one_or_none()
    return row.priority if row is not None else None
