"""Training service — close the gap between current troops and TroopGoals.

Strategy per village, per tick:
  1. Load the village's goals, ordered by priority (low number first).
  2. Read current counts from `village.troops_json` (what's actually home).
     That undercounts troops already in training queue + out on raids — we
     accept the slight overshoot for simplicity. If this becomes a problem
     we can subtract queued/in-transit amounts from the movements JSON.
  3. For each goal with `current < target`, navigate to the relevant training
     building, read how many the game will let us train right now (resources
     clamped), click up to `min(deficit, max)`.
  4. Stop after the first successful submit per building this tick. That way
     resources aren't double-spent across goals on the same tick (e.g. two
     Gaul cavalry goals both reading the stable's "max=10" and both trying
     to train 10 would fail the second click).
"""
from __future__ import annotations

import json
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.pages.training import TrainingPage
from app.browser.session import BrowserSession
from app.core.logging import get_logger
from app.models.troop_goal import TroopGoal
from app.models.village import Village
from app.services.troop_data import troop_info

log = get_logger("service.training")


def _current_counts(v: Village) -> dict[str, int]:
    try:
        data = json.loads(v.troops_json or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


async def run_for_village(
    db: AsyncSession, session: BrowserSession, village: Village
) -> str:
    goals = (
        await db.execute(
            select(TroopGoal)
            .where(TroopGoal.village_id == village.id, TroopGoal.paused.is_(False))
            .order_by(TroopGoal.priority.asc(), TroopGoal.id.asc())
        )
    ).scalars().all()
    if not goals:
        return "no goals"

    counts = _current_counts(village)
    tribe = village.tribe.value if village.tribe else None
    tp = TrainingPage(session.page)

    # Group goals by building so we open each training hall at most once.
    by_building: dict[str, list[TroopGoal]] = defaultdict(list)
    for g in goals:
        info = troop_info(tribe, g.troop_key)
        if info["gid"] is None:
            log.warning("training.unknown_building", key=g.troop_key, tribe=tribe)
            continue
        by_building[info["building"]].append(g)

    trained_any: list[str] = []
    for building, group in by_building.items():
        # Order within the building by priority.
        group.sort(key=lambda x: (x.priority, x.id))
        info0 = troop_info(tribe, group[0].troop_key)
        await tp.open(info0["gid"])
        for g in group:
            current = int(counts.get(g.troop_key, 0))
            deficit = g.target_count - current
            if deficit <= 0:
                log.debug(
                    "training.goal.met",
                    village_id=village.id, key=g.troop_key,
                    current=current, target=g.target_count,
                )
                continue
            max_now = await tp.max_trainable(g.troop_key)
            if max_now <= 0:
                log.info(
                    "training.max_zero",
                    village_id=village.id, key=g.troop_key,
                    reason="no resources or unit not unlocked",
                )
                continue
            batch = min(deficit, max_now)
            ok = await tp.train(g.troop_key, batch)
            if ok:
                trained_any.append(f"{g.troop_key}+{batch}")
                # Don't submit another goal against the same building this tick —
                # the first Train call just consumed resources.
                break
    if trained_any:
        log.info(
            "training.village.done",
            village_id=village.id, dispatched=trained_any,
        )
        return ",".join(trained_any)
    return "nothing to do"


async def run_for_account(
    db: AsyncSession, session: BrowserSession, account_id: int
) -> int:
    """Walk every village with active goals. Returns #villages that submitted."""
    villages = (
        await db.execute(select(Village).where(Village.account_id == account_id))
    ).scalars().all()
    submitted = 0
    for v in villages:
        # Cheap pre-check: skip if there are zero active goals.
        has_goals = (
            await db.execute(
                select(TroopGoal.id)
                .where(TroopGoal.village_id == v.id, TroopGoal.paused.is_(False))
                .limit(1)
            )
        ).scalar_one_or_none()
        if has_goals is None:
            continue
        from app.services.troops import _switch_active_village  # reuse
        await _switch_active_village(session, v.travian_id)
        status = await run_for_village(db, session, v)
        if status and status not in ("no goals", "nothing to do"):
            submitted += 1
    return submitted
