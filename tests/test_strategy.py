"""Strategy loader + compiler tests."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BuildOrder,
    HeroPolicy,
    StrategyGate,
    StrategyGateStatus,
    TroopGoal,
    Village,
)
from app.services.strategy import (
    BuildOrderRow,
    Strategy,
    TroopGoalRow,
    apply_compiled_strategy,
    compile_strategy,
    expected_reward,
    get_hero_policy,
    get_strategy,
    load_strategy,
    pending_gate_priority,
)
from app.services.strategy import (
    HeroPolicy as HeroPolicyModel,
)


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "strategy.yaml"
    p.write_text(textwrap.dedent(body).lstrip())
    return p


def test_bundled_x10_egyptian_eco_loads_and_compiles() -> None:
    strategy = get_strategy("x10_egyptian_eco_ark1")
    assert strategy.meta.tribe == "egyptian"
    assert strategy.meta.server_speed == 10

    compiled = compile_strategy(strategy)

    # First real build is Main Building 3 at step 3.
    mb3 = next(r for r in compiled.build_orders if r.building_key == "main_building")
    assert mb3.target_level == 3

    # "All wood to 1" at step 12 → 4 rows.
    wood_l1 = [
        r for r in compiled.build_orders
        if r.building_key == "woodcutter" and r.target_level == 1
    ]
    assert len(wood_l1) == 4

    # Priorities ascend with step order. Paired PDF rows (e.g. Warehouse + Granary
    # both at step 4) legitimately share a priority — semantic, not a bug.
    priorities = [r.priority for r in compiled.build_orders]
    assert priorities == sorted(priorities)

    # Gates are surfaced separately and keep their rule text.
    gate_kinds = {g.kind for g in compiled.gates}
    assert {"hero_mode", "clear_oases", "settle"} <= gate_kinds

    # Hero policy is carried through.
    assert compiled.hero_policy is not None
    adv6 = next(a for a in compiled.hero_policy.adventure_rewards if a.index == 6)
    assert adv6.prefer == "books"
    assert adv6.post == "convert_to_resources"

    # Troop plan compiles to a TroopGoal anchored after step 32.
    assert compiled.troop_goals == [
        TroopGoalRow(troop_key="t1", target_count=210, priority=1000 + 32 * 10 + 5),
    ]


def test_fields_step_expands_count_rows(tmp_path: Path) -> None:
    strategy = load_strategy(_write(tmp_path, """
        meta:
          name: tiny
          tribe: gaul
          server_speed: 1
        build:
          - step: 1
            fields: { type: cropland, count: 3, level: 2 }
    """))
    compiled = compile_strategy(strategy)
    assert compiled.build_orders == [
        BuildOrderRow(building_key="cropland", target_level=2, priority=1010, slot=None),
        BuildOrderRow(building_key="cropland", target_level=2, priority=1011, slot=None),
        BuildOrderRow(building_key="cropland", target_level=2, priority=1012, slot=None),
    ]


def test_at_most_one_payload_per_step(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="at most one"):
        load_strategy(_write(tmp_path, """
            meta: { name: bad, tribe: roman, server_speed: 1 }
            build:
              - step: 1
                building: { key: main_building, level: 1 }
                hero: { action: adventure }
        """))


def test_empty_step_without_note_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="empty step"):
        load_strategy(_write(tmp_path, """
            meta: { name: bad, tribe: roman, server_speed: 1 }
            build:
              - step: 1
        """))


def test_note_only_step_is_allowed_and_emits_nothing(tmp_path: Path) -> None:
    """Note-only rows let a strategy preserve the PDF's numbering for clarity."""
    strategy = load_strategy(_write(tmp_path, """
        meta: { name: ok, tribe: roman, server_speed: 1 }
        build:
          - step: 1
            building: { key: main_building, level: 1 }
          - step: 2
            note: "manual step tracked elsewhere"
    """))
    compiled = compile_strategy(strategy)
    assert len(compiled.build_orders) == 1
    assert compiled.build_orders[0].building_key == "main_building"


def test_unknown_building_key_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="unknown building key"):
        load_strategy(_write(tmp_path, """
            meta: { name: bad, tribe: roman, server_speed: 1 }
            build:
              - step: 1
                building: { key: mystery_shrine, level: 1 }
        """))


def test_troop_plan_must_reference_known_step(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="not in build"):
        load_strategy(_write(tmp_path, """
            meta: { name: bad, tribe: roman, server_speed: 1 }
            build:
              - step: 1
                building: { key: main_building, level: 1 }
            troops:
              - { after_step: 99, troop: t1, target: 10 }
        """))


def test_invalid_troop_key(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        load_strategy(_write(tmp_path, """
            meta: { name: bad, tribe: roman, server_speed: 1 }
            build:
              - step: 1
                building: { key: main_building, level: 1 }
            troops:
              - { after_step: 1, troop: t99, target: 10 }
        """))


def test_strategy_model_is_constructible_from_dict() -> None:
    """Sanity: the pydantic model accepts a plain dict, not just YAML."""
    s = Strategy.model_validate({
        "meta": {"name": "d", "tribe": "teuton", "server_speed": 2},
        "build": [{"step": 1, "hero": {"action": "adventure"}}],
    })
    compiled = compile_strategy(s)
    assert len(compiled.hero_actions) == 1
    assert compiled.hero_actions[0].action == "adventure"


async def test_apply_persists_build_orders_troop_goals_and_gates(
    db_session: AsyncSession, sample_village: Village
) -> None:
    strategy = get_strategy("x10_egyptian_eco_ark1")
    compiled = compile_strategy(strategy)

    result = await apply_compiled_strategy(db_session, sample_village.id, compiled)

    assert result.build_orders_inserted == len(compiled.build_orders)
    assert result.troop_goals_upserted == len(compiled.troop_goals)
    assert result.gates_inserted == len(compiled.gates)

    orders = (await db_session.scalars(
        select(BuildOrder).where(BuildOrder.village_id == sample_village.id)
        .order_by(BuildOrder.priority)
    )).all()
    assert len(orders) == len(compiled.build_orders)
    assert orders[0].building_key == "main_building"  # step 3 in the PDF

    gates = (await db_session.scalars(
        select(StrategyGate).where(StrategyGate.village_id == sample_village.id)
        .order_by(StrategyGate.step)
    )).all()
    assert [g.step for g in gates] == [26, 43, 60]
    assert all(g.status == StrategyGateStatus.PENDING for g in gates)

    goals = (await db_session.scalars(
        select(TroopGoal).where(TroopGoal.village_id == sample_village.id)
    )).all()
    assert len(goals) == 1
    assert goals[0].troop_key == "t1"
    assert goals[0].target_count == 210


async def test_apply_troop_goal_upserts_not_duplicates(
    db_session: AsyncSession, sample_village: Village
) -> None:
    """Re-applying the same strategy must not trip the troop-goal unique constraint."""
    strategy = get_strategy("x10_egyptian_eco_ark1")
    compiled = compile_strategy(strategy)

    await apply_compiled_strategy(db_session, sample_village.id, compiled)
    await apply_compiled_strategy(db_session, sample_village.id, compiled)

    goals = (await db_session.scalars(
        select(TroopGoal).where(TroopGoal.village_id == sample_village.id)
    )).all()
    assert len(goals) == 1  # upserted, not duplicated
    assert goals[0].target_count == 210


async def test_apply_updates_troop_goal_target_on_reapply(
    db_session: AsyncSession, sample_village: Village
) -> None:
    compiled = compile_strategy(Strategy.model_validate({
        "meta": {"name": "t", "tribe": "roman", "server_speed": 1},
        "build": [{"step": 1, "building": {"key": "main_building", "level": 1}}],
        "troops": [{"after_step": 1, "troop": "t1", "target": 50}],
    }))
    await apply_compiled_strategy(db_session, sample_village.id, compiled)

    compiled_v2 = compile_strategy(Strategy.model_validate({
        "meta": {"name": "t", "tribe": "roman", "server_speed": 1},
        "build": [{"step": 1, "building": {"key": "main_building", "level": 1}}],
        "troops": [{"after_step": 1, "troop": "t1", "target": 400}],
    }))
    await apply_compiled_strategy(db_session, sample_village.id, compiled_v2)

    goal = (await db_session.scalars(
        select(TroopGoal).where(TroopGoal.village_id == sample_village.id)
    )).one()
    assert goal.target_count == 400


async def test_apply_persists_hero_policy(
    db_session: AsyncSession, sample_village: Village
) -> None:
    import json

    strategy = get_strategy("x10_egyptian_eco_ark1")
    compiled = compile_strategy(strategy)
    result = await apply_compiled_strategy(db_session, sample_village.id, compiled)
    assert result.hero_policy_written is True

    policy = (await db_session.scalars(
        select(HeroPolicy).where(HeroPolicy.account_id == sample_village.account_id)
    )).one()
    rewards = json.loads(policy.adventure_rewards_json)
    assert len(rewards) == 10
    adv6 = next(r for r in rewards if r["index"] == 6)
    assert adv6["prefer"] == "books"
    assert adv6["post"] == "convert_to_resources"


async def test_apply_hero_policy_is_upserted_not_duplicated(
    db_session: AsyncSession, sample_village: Village
) -> None:
    strategy = get_strategy("x10_egyptian_eco_ark1")
    compiled = compile_strategy(strategy)
    await apply_compiled_strategy(db_session, sample_village.id, compiled)
    await apply_compiled_strategy(db_session, sample_village.id, compiled)

    policies = (await db_session.scalars(
        select(HeroPolicy).where(HeroPolicy.account_id == sample_village.account_id)
    )).all()
    assert len(policies) == 1  # unique(account_id) — upsert, not duplicate


async def test_pending_gate_priority_returns_lowest(
    db_session: AsyncSession, sample_village: Village
) -> None:
    strategy = get_strategy("x10_egyptian_eco_ark1")
    compiled = compile_strategy(strategy)
    await apply_compiled_strategy(db_session, sample_village.id, compiled)

    cutoff = await pending_gate_priority(db_session, sample_village.id)
    # Gate at step 26 → priority 1000 + 26*10 = 1260.
    assert cutoff == 1260


async def test_pending_gate_priority_ignores_resolved(
    db_session: AsyncSession, sample_village: Village
) -> None:
    strategy = get_strategy("x10_egyptian_eco_ark1")
    compiled = compile_strategy(strategy)
    await apply_compiled_strategy(db_session, sample_village.id, compiled)

    # Resolve the lowest-priority gate (step 26) — cutoff should advance to step 43.
    gate_26 = (await db_session.scalars(
        select(StrategyGate).where(
            StrategyGate.village_id == sample_village.id,
            StrategyGate.step == 26,
        )
    )).one()
    gate_26.status = StrategyGateStatus.RESOLVED
    await db_session.flush()

    cutoff = await pending_gate_priority(db_session, sample_village.id)
    assert cutoff == 1000 + 43 * 10


async def test_pending_gate_priority_none_when_no_gates(
    db_session: AsyncSession, sample_village: Village
) -> None:
    """A compiled strategy with no gate steps returns None → tick() dispatches freely."""
    compiled = compile_strategy(Strategy.model_validate({
        "meta": {"name": "no-gates", "tribe": "roman", "server_speed": 1},
        "build": [{"step": 1, "building": {"key": "main_building", "level": 1}}],
    }))
    await apply_compiled_strategy(db_session, sample_village.id, compiled)
    assert await pending_gate_priority(db_session, sample_village.id) is None


async def test_build_tick_filters_queued_by_gate_cutoff(
    db_session: AsyncSession, sample_village: Village
) -> None:
    """Orders at/past a pending gate's priority must be held off dispatch.

    We exercise the same select + filter that ``building.tick()`` runs, without
    the browser side — the test only cares that the cutoff eliminates the
    right rows from the dispatch candidate list.
    """
    strategy = get_strategy("x10_egyptian_eco_ark1")
    compiled = compile_strategy(strategy)
    await apply_compiled_strategy(db_session, sample_village.id, compiled)

    from app.models.build import BuildOrderStatus

    queued = (await db_session.scalars(
        select(BuildOrder)
        .where(
            BuildOrder.village_id == sample_village.id,
            BuildOrder.status.in_([BuildOrderStatus.QUEUED, BuildOrderStatus.BLOCKED]),
        )
        .order_by(BuildOrder.priority.asc(), BuildOrder.id.asc())
    )).all()

    cutoff = await pending_gate_priority(db_session, sample_village.id)
    assert cutoff is not None
    dispatchable = [o for o in queued if o.priority < cutoff]
    held = [o for o in queued if o.priority >= cutoff]
    assert len(dispatchable) > 0
    assert len(held) > 0
    assert all(o.priority < cutoff for o in dispatchable)
    assert all(o.priority >= cutoff for o in held)


async def test_get_hero_policy_returns_none_when_unset(
    db_session: AsyncSession, sample_village: Village
) -> None:
    assert await get_hero_policy(db_session, sample_village.account_id) is None


async def test_get_hero_policy_round_trips_from_apply(
    db_session: AsyncSession, sample_village: Village
) -> None:
    strategy = get_strategy("x10_egyptian_eco_ark1")
    await apply_compiled_strategy(db_session, sample_village.id, compile_strategy(strategy))

    policy = await get_hero_policy(db_session, sample_village.account_id)
    assert policy is not None
    assert isinstance(policy, HeroPolicyModel)
    assert len(policy.adventure_rewards) == 10

    # The parsed dataclass matches what went in.
    assert policy.adventure_rewards[0].prefer == "horse"
    assert policy.adventure_rewards[0].post == "keep"
    assert policy.adventure_rewards[5].prefer == "books"
    assert policy.adventure_rewards[5].post == "convert_to_resources"


def test_expected_reward_indexes_by_adventure_number() -> None:
    policy = HeroPolicyModel.model_validate({
        "adventure_rewards": [
            {"index": 1, "prefer": "horse"},
            {"index": 3, "prefer": "troops"},
        ],
    })
    assert expected_reward(policy, 1).prefer == "horse"
    assert expected_reward(policy, 3).prefer == "troops"
    assert expected_reward(policy, 2) is None  # gap → no preference
    assert expected_reward(policy, 99) is None  # past the plan → no preference
