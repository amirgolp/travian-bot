"""Building / resource-field upgrade pipeline.

The pipeline is a priority-ordered queue of BuildOrder rows per village.
Each tick, the solver:

  1. Refreshes the in-game build queue (what's actively under construction).
  2. Looks at queued orders in priority order.
  3. For the next order, checks prereqs against the current BuildingSlot cache
     and flags blockers (e.g. "needs Main Building lvl 5") without removing
     the order from the queue.
  4. If buildable and there's a free builder slot, navigates to the slot and
     clicks Upgrade / Construct.

Why this shape:
- Building order is user-intent, not something to re-derive from scratch.
  The user edits the queue via API; the solver *serves* that queue, it doesn't
  plan for them.
- Prereqs come from data/buildings.yaml, which is human-editable — so adding
  a new building or fixing a wrong prereq doesn't need a code change.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.pages.build import BuildPage
from app.browser.pages.dorf import Dorf1Page
from app.browser.session import BrowserSession
from app.core.logging import get_logger
from app.models.build import BuildingSlot, BuildOrder, BuildOrderStatus
from app.models.village import Village
from app.services.building_data import BuildingDef, Prereq, get, load_buildings

log = get_logger("service.building")

# Travian Legends default: 1 builder slot, +1 with Travian Plus, +1 with a Romans capital.
# We read effective capacity from the live build queue instead of guessing.
DEFAULT_BUILDER_SLOTS = 2


async def queue_upgrade(
    db: AsyncSession,
    village_id: int,
    building_key: str,
    target_level: int,
    slot: int | None = None,
    priority: int = 100,
) -> BuildOrder:
    """Add an upgrade step to the queue. Does NOT expand it into per-level steps —
    the solver re-queues itself each level by comparing target_level to current."""
    # Validate the key exists so we fail fast on typos.
    get(building_key)
    order = BuildOrder(
        village_id=village_id,
        building_key=building_key,
        target_level=target_level,
        slot=slot,
        priority=priority,
        status=BuildOrderStatus.QUEUED,
    )
    db.add(order)
    await db.flush()
    log.info("build.queue", village=village_id, key=building_key, to=target_level, slot=slot)
    return order


async def cancel_order(db: AsyncSession, order_id: int) -> None:
    order = await db.get(BuildOrder, order_id)
    if order is None:
        return
    order.status = BuildOrderStatus.CANCELLED
    await db.flush()


async def reorder(db: AsyncSession, village_id: int, ordered_ids: list[int]) -> None:
    """Rewrite priorities in the order the IDs arrive. Missing IDs keep their current priority."""
    for idx, oid in enumerate(ordered_ids):
        order = await db.get(BuildOrder, oid)
        if order and order.village_id == village_id:
            order.priority = idx * 10
    await db.flush()


async def sync_slots_from_scrape(
    db: AsyncSession, village_id: int,
    dorf1_levels: list, dorf2_levels: list,
) -> int:
    """Upsert `BuildingSlot` rows from a fresh dorf1+dorf2 scrape.

    This is how real in-game levels (including ones the user manually
    upgraded in the browser) make it into the bot's cache. Without this,
    `_current_level` returns 0 for every building that the bot didn't
    upgrade itself, so every prereq check fails even when the prereq is
    clearly met in-game — the "needs main_building lvl 5" phantom blocker.

    `dorf1_levels` / `dorf2_levels` are lists of `BuildingLevel` dataclasses
    (slot, gid, level). A `gid == 0` slot is empty — we zero-out any cached
    row for that slot to mirror a demolished / unbuilt state.
    """
    from app.services.building_data import by_gid as _by_gid  # local: avoid cycle
    gid_map = _by_gid()
    existing = (
        await db.execute(
            select(BuildingSlot).where(BuildingSlot.village_id == village_id)
        )
    ).scalars().all()
    by_slot = {s.slot: s for s in existing}
    touched = 0
    for lv in [*dorf1_levels, *dorf2_levels]:
        row = by_slot.get(lv.slot)
        defn = gid_map.get(lv.gid) if lv.gid else None
        key = defn.key if defn else None
        if row is None:
            row = BuildingSlot(
                village_id=village_id, slot=lv.slot,
                building_key=key, level=lv.level,
            )
            db.add(row)
            touched += 1
        else:
            if row.building_key != key or row.level != lv.level:
                row.building_key = key
                row.level = lv.level
                touched += 1
    await db.flush()
    log.info(
        "building.slots.synced",
        village_id=village_id,
        dorf1=len(dorf1_levels), dorf2=len(dorf2_levels), changed=touched,
    )
    return touched


async def _current_level(db: AsyncSession, village_id: int, building_key: str) -> int:
    rows = (
        await db.execute(
            select(BuildingSlot).where(
                BuildingSlot.village_id == village_id,
                BuildingSlot.building_key == building_key,
            )
        )
    ).scalars().all()
    return max((r.level for r in rows), default=0)


async def _first_unmet_prereq(
    db: AsyncSession, village_id: int, defn: BuildingDef
) -> Prereq | None:
    for p in defn.prereqs:
        lvl = await _current_level(db, village_id, p.key)
        if lvl < p.level:
            return p
    return None


async def _refresh_order_statuses(
    db: AsyncSession, village_id: int, live_occupied: int = 0
) -> None:
    """Re-evaluate every QUEUED/BLOCKED/IN_PROGRESS order's status against the
    latest `BuildingSlot` snapshot — without attempting a dispatch. Decoupled
    from `tick()` so it still runs when the in-game builder is full.

    Order transitions handled here:
      * any         -> DONE     when current level already ≥ target
      * QUEUED      -> BLOCKED  with a fresh `blocked_reason` on unmet prereq
      * BLOCKED     -> QUEUED   (blocked_reason cleared) when prereq is met
      * IN_PROGRESS -> QUEUED   when the in-game build queue is empty, meaning
                                the previous step has completed and we need to
                                dispatch the next level toward `target_level`.
                                Gated on `live_occupied == 0` so we don't
                                thrash dispatching while the upgrade is still
                                running (the sync-scraped BuildingSlot reports
                                the pre-upgrade level during the build).
    """
    defs = load_buildings()
    rows = (
        await db.execute(
            select(BuildOrder).where(
                BuildOrder.village_id == village_id,
                BuildOrder.status.in_(
                    [
                        BuildOrderStatus.QUEUED,
                        BuildOrderStatus.BLOCKED,
                        BuildOrderStatus.IN_PROGRESS,
                    ]
                ),
            )
        )
    ).scalars().all()
    for order in rows:
        defn = defs.get(order.building_key)
        if defn is None:
            order.status = BuildOrderStatus.FAILED
            order.blocked_reason = f"unknown building {order.building_key!r}"
            continue
        current = await _current_level(db, village_id, order.building_key)
        if current >= order.target_level:
            order.status = BuildOrderStatus.DONE
            order.blocked_reason = None
            continue
        if order.status == BuildOrderStatus.IN_PROGRESS:
            if live_occupied > 0:
                continue  # previous step still running — leave alone
            log.info(
                "build.order.next_step",
                order_id=order.id, key=order.building_key,
                current=current, target=order.target_level,
            )
            order.status = BuildOrderStatus.QUEUED
            order.blocked_reason = None
        missing = await _first_unmet_prereq(db, village_id, defn)
        if missing is not None:
            order.status = BuildOrderStatus.BLOCKED
            order.blocked_reason = f"needs {missing.key} lvl {missing.level}"
            continue
        if order.status == BuildOrderStatus.BLOCKED:
            log.info(
                "build.order.unblocked",
                order_id=order.id, key=order.building_key,
                was=order.blocked_reason,
            )
            order.status = BuildOrderStatus.QUEUED
            order.blocked_reason = None
    await db.flush()


async def _find_or_choose_slot(
    db: AsyncSession, village_id: int, defn: BuildingDef, preferred: int | None
) -> int | None:
    """Pick a slot id that either already holds this building (for upgrades) or
    is empty and in the right placement (for construction).
    Resource fields (dorf1) have fixed slots (1..18) that are always the same
    building, so this really only matters for dorf2."""
    slots = (
        await db.execute(
            select(BuildingSlot).where(BuildingSlot.village_id == village_id)
        )
    ).scalars().all()

    # 1) Upgrading an existing instance: find the slot that holds this building.
    for s in slots:
        if s.building_key == defn.key:
            if preferred is None or s.slot == preferred:
                return s.slot

    # 2) New construction in the user's preferred slot, if it's empty and in range.
    if preferred is not None:
        target = next((s for s in slots if s.slot == preferred), None)
        if target and target.building_key is None:
            return preferred

    # 3) Otherwise first empty dorf2 slot (19..40). dorf1 is fixed by data.
    if defn.placement in ("dorf2", "both"):
        for s in sorted(slots, key=lambda x: x.slot):
            if 19 <= s.slot <= 40 and s.building_key is None:
                return s.slot

    return None


async def refresh_build_queue(
    db: AsyncSession, session: BrowserSession, village: Village
) -> int:
    """Pull the live in-progress queue from dorf1 and return its length."""
    dorf1 = Dorf1Page(session.page)
    await dorf1.goto_dorf1()
    live = await dorf1.read_build_queue()
    # We don't persist the live queue — it's ephemeral. What matters for the
    # solver is *how many slots are occupied right now*.
    return len(live)


async def tick(db: AsyncSession, session: BrowserSession, village: Village) -> str:
    """One solver pass for one village. Returns a short status string."""
    log.debug("build.tick.start", village_id=village.id, village=village.name)
    # Look up the account once to honor its watch_video_bonuses preference.
    from app.models.account import Account  # local import avoids a cycle
    account = await db.get(Account, village.account_id)
    watch_videos = bool(getattr(account, "watch_video_bonuses", True)) if account else True

    occupied = await refresh_build_queue(db, session, village)
    log.debug("build.live_queue", village_id=village.id, occupied=occupied)

    # Refresh QUEUED/BLOCKED/IN_PROGRESS statuses against the latest
    # BuildingSlot snapshot BEFORE short-circuiting on a busy builder.
    # Otherwise a stale `blocked_reason` lingers forever while the in-game
    # queue is full — the user sees "needs main_building lvl 5" long after
    # main_building is 12. The `occupied` hand-off lets multi-level orders
    # step forward: when the live queue drains, IN_PROGRESS goes back to
    # QUEUED so the next level dispatches.
    await _refresh_order_statuses(db, village.id, live_occupied=occupied)

    if occupied >= DEFAULT_BUILDER_SLOTS:
        log.info("build.busy", village_id=village.id, occupied=occupied)
        return "busy"

    queued = (
        await db.execute(
            select(BuildOrder)
            .where(
                BuildOrder.village_id == village.id,
                BuildOrder.status.in_(
                    [BuildOrderStatus.QUEUED, BuildOrderStatus.BLOCKED]
                ),
            )
            .order_by(BuildOrder.priority.asc(), BuildOrder.id.asc())
        )
    ).scalars().all()
    log.debug("build.queue.size", village_id=village.id, orders=len(queued))

    # Honor strategy gates: any pending gate stops the queue at its priority.
    # Orders past the gate stay QUEUED but won't dispatch until the gate is
    # resolved/skipped from the dashboard (or a policy controller clears it).
    from app.services.strategy import pending_gate_priority  # local: avoid cycle
    gate_cutoff = await pending_gate_priority(db, village.id)
    if gate_cutoff is not None:
        before = len(queued)
        queued = [o for o in queued if o.priority < gate_cutoff]
        if len(queued) < before:
            log.info(
                "build.gate.holding",
                village_id=village.id,
                cutoff=gate_cutoff,
                held=before - len(queued),
            )

    defs = load_buildings()
    for order in queued:
        defn = defs.get(order.building_key)
        if defn is None:
            order.status = BuildOrderStatus.FAILED
            order.blocked_reason = f"unknown building {order.building_key!r}"
            log.error("build.unknown_key", order_id=order.id, key=order.building_key)
            continue

        current = await _current_level(db, village.id, order.building_key)
        if current >= order.target_level:
            order.status = BuildOrderStatus.DONE
            log.info(
                "build.order.done",
                order_id=order.id, key=order.building_key, level=current,
            )
            continue

        missing = await _first_unmet_prereq(db, village.id, defn)
        if missing is not None:
            if order.status != BuildOrderStatus.BLOCKED:
                log.warning(
                    "build.order.blocked",
                    order_id=order.id, key=order.building_key,
                    needs=f"{missing.key} lvl {missing.level}",
                )
            order.status = BuildOrderStatus.BLOCKED
            order.blocked_reason = f"needs {missing.key} lvl {missing.level}"
            continue

        # Prereqs are now satisfied — clear any stale BLOCKED state from a
        # previous tick where BuildingSlot was behind reality.
        if order.status == BuildOrderStatus.BLOCKED:
            log.info(
                "build.order.unblocked",
                order_id=order.id, key=order.building_key,
                was=order.blocked_reason,
            )
            order.status = BuildOrderStatus.QUEUED
            order.blocked_reason = None

        chosen_slot = await _find_or_choose_slot(db, village.id, defn, order.slot)
        if chosen_slot is None:
            order.status = BuildOrderStatus.BLOCKED
            order.blocked_reason = "no free slot"
            log.warning("build.no_slot", order_id=order.id, key=order.building_key)
            continue

        log.info(
            "build.attempt",
            order_id=order.id, key=order.building_key,
            slot=chosen_slot, current=current, target=order.target_level,
        )
        bp = BuildPage(session.page, watch_videos=watch_videos)
        await bp.open_slot(chosen_slot)
        ok = await bp.upgrade_here()
        if not ok and current == 0:
            log.debug("build.fallback.construct", slot=chosen_slot, key=defn.key)
            ok = await bp.construct(chosen_slot, defn.name)

        if ok:
            order.status = BuildOrderStatus.IN_PROGRESS
            order.completes_at = datetime.now(tz=UTC)
            existing = (
                await db.execute(
                    select(BuildingSlot).where(
                        BuildingSlot.village_id == village.id,
                        BuildingSlot.slot == chosen_slot,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                db.add(BuildingSlot(
                    village_id=village.id, slot=chosen_slot,
                    building_key=defn.key, level=current + 1,
                ))
            else:
                existing.building_key = defn.key
                existing.level = current + 1
            await db.flush()
            msg = f"dispatched {defn.key}->{current + 1} @ slot {chosen_slot}"
            log.info("build.dispatched", order_id=order.id, summary=msg)
            return msg
        else:
            order.status = BuildOrderStatus.BLOCKED
            order.blocked_reason = "UI refused upgrade (resources? builders?)"
            log.warning("build.ui_refused", order_id=order.id, slot=chosen_slot)

    await db.flush()
    log.debug("build.tick.idle", village_id=village.id)
    return "idle"
