"""Farming service — farmlist CRUD, dispatch, and maintenance.

Controllers (see app/services/controllers/*) drive the cadence; this module
just exposes the operations they need.

Slots point at MapTiles, not raw coords, so when a village gets occupied or an
oasis becomes uninteresting the fact lives on one row rather than being
sprinkled across many slots.
"""
from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.browser.pages.rally import RaidTarget, RallyPointPage
from app.browser.server import detect_server
from app.browser.session import BrowserSession
from app.core.logging import get_logger
from app.models.account import Account
from app.models.farmlist import Farmlist, FarmlistKind, FarmlistSlot
from app.models.map_tile import MapTile, TileType
from app.models.village import Village
from app.services.tile_details import get_oasis_animals_cached

log = get_logger("service.farming")

MAX_CONSECUTIVE_LOSSES = 3
# After this many back-to-back dispatch failures across any farmlists we stop
# the whole tick — the odds the next attempt suddenly works are near zero and
# we're just burning requests (and attracting attention from anti-bot heuristics).
MAX_CONSECUTIVE_SEND_FAILS = 3


# ----- CRUD -----

async def create_farmlist(
    db: AsyncSession,
    village_id: int,
    name: str,
    kind: FarmlistKind = FarmlistKind.MIXED,
    interval_seconds: int = 1800,
    default_troops: dict[str, int] | None = None,
) -> Farmlist:
    fl = Farmlist(
        village_id=village_id,
        name=name,
        kind=kind,
        interval_seconds=interval_seconds,
        default_troops_json=json.dumps(default_troops or {}),
    )
    db.add(fl)
    await db.flush()
    log.info("farmlist.create", village_id=village_id, name=name, kind=kind.value)
    return fl


async def get_or_create_farmlist(
    db: AsyncSession,
    village_id: int,
    name: str,
    kind: FarmlistKind,
    interval_seconds: int = 1800,
) -> Farmlist:
    existing = (
        await db.execute(
            select(Farmlist).where(Farmlist.village_id == village_id, Farmlist.name == name)
        )
    ).scalar_one_or_none()
    if existing is not None:
        log.debug("farmlist.exists", village_id=village_id, name=name)
        return existing
    return await create_farmlist(db, village_id, name, kind, interval_seconds)


async def add_slot_for_tile(
    db: AsyncSession,
    farmlist_id: int,
    tile_id: int,
    troops_override: dict[str, int] | None = None,
) -> FarmlistSlot | None:
    """Idempotent: returns the existing slot if this (list, tile) already exists."""
    existing = (
        await db.execute(
            select(FarmlistSlot).where(
                FarmlistSlot.farmlist_id == farmlist_id,
                FarmlistSlot.tile_id == tile_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        log.debug("farmlist.slot.exists", farmlist_id=farmlist_id, tile_id=tile_id)
        return existing
    slot = FarmlistSlot(
        farmlist_id=farmlist_id,
        tile_id=tile_id,
        troops_json=json.dumps(troops_override) if troops_override else "",
    )
    db.add(slot)
    await db.flush()
    log.info("farmlist.slot.add", farmlist_id=farmlist_id, tile_id=tile_id)
    return slot


# ----- Dispatch -----

async def run_due_farmlists(
    db: AsyncSession, session: BrowserSession, village: Village
) -> int:
    """Dispatch every due slot of every enabled farmlist on this village.

    Travian Legends' in-game farmlist tab (`/build.php?gid=16&tt=99` "Start
    all") is Gold-Club-only. Instead we drive the normal send-troops form
    (`tt=2`) once per enabled slot, reusing `RallyPointPage.send_raid()`.

    Each slot carries its own `last_raid_at`, so interval gating is per-slot:
    a slot raided recently is skipped even if other slots in the same list
    are due. `Farmlist.interval_seconds` is the minimum gap between consecutive
    raids on the SAME slot, scaled by world speed (3x world → 3x more often).

    Returns the number of raids actually submitted.
    """
    now = datetime.now(tz=timezone.utc)

    account = await db.get(Account, village.account_id)
    speed_mult = 1
    if account is not None:
        try:
            speed_mult = max(1, detect_server(account.server_url).speed_multiplier)
        except Exception as e:  # noqa: BLE001
            log.warning("farming.speed_detect_failed", account_id=account.id, err=str(e))

    # Eager-load slots + tiles so the inner loop has everything it needs
    # without kicking off lazy loads inside the async session.
    rows = (
        await db.execute(
            select(Farmlist)
            .where(Farmlist.village_id == village.id, Farmlist.enabled.is_(True))
            .options(selectinload(Farmlist.slots).selectinload(FarmlistSlot.tile))
        )
    ).scalars().all()
    log.debug(
        "farmlist.dispatch.scan",
        village_id=village.id, lists=len(rows), speed_x=speed_mult,
    )
    if not rows:
        return 0

    rally = RallyPointPage(session.page)
    # Live home-troop budget. TroopsController scrapes this each tick and
    # writes `village.troops_json`. Raids subtract from this running total as
    # we go so we don't over-commit the same troop across multiple slots.
    # Village reserves are deducted upfront so the running budget reflects
    # only what's actually available to dispatch — raids can't dip below the
    # reserve even on the first attempt.
    raw_home = _decode_troops(village.troops_json)
    reserve = _decode_troops(getattr(village, "troops_reserve_json", None))
    home_troops = {
        k: max(0, v - reserve.get(k, 0)) for k, v in raw_home.items()
    }
    log.debug(
        "farmlist.dispatch.home_troops",
        village_id=village.id, home=raw_home, reserve=reserve, budget=home_troops,
    )

    dispatched = 0
    consecutive_fails = 0
    for fl in rows:
        list_default_troops = _decode_troops(fl.default_troops_json)
        effective_interval = fl.interval_seconds / max(1, speed_mult)

        # Filter to the due + eligible slots and sort by distance. The sort
        # direction is chosen by the composition:
        #   any cavalry/siege (t4..t10) → farther targets first (cavalry is
        #                                  fast; burns less time / tile, so
        #                                  the marginal cost of a far raid is low)
        #   infantry-only                → closer targets first (infantry is
        #                                  slow — nearby raids recycle troops
        #                                  faster, keeping more lists fed)
        candidates: list[tuple[float, FarmlistSlot]] = []
        for slot in fl.slots:
            if not slot.enabled:
                continue
            if slot.tile is None:
                continue
            if not _slot_is_due(slot, now, effective_interval):
                continue
            dx = slot.tile.x - village.x
            dy = slot.tile.y - village.y
            d = (dx * dx + dy * dy) ** 0.5
            candidates.append((d, slot))

        # A composition dict like {t1: 3, t2: 4, t4: 2} is interpreted as
        # three independent raid TEMPLATES — {t1:3}, {t2:4}, {t4:2} — so every
        # dispatched raid is a single-troop-type squad. This matches how people
        # actually farm: a phalanx squad here, a TT squad there. We route fast
        # templates (t4+) toward farther slots and slow ones to nearer slots;
        # per-slot overrides still win if set.
        candidates.sort(key=lambda p: p[0])  # near → far
        n = len(candidates)
        # Median-based partition: the closer half of slots prefers slow
        # templates, the farther half prefers fast. With one unit class the
        # preferred side still wins; the other is a fallback.
        mid = n / 2

        for i, (_, slot) in enumerate(candidates):
            slot_override = _decode_troops(slot.troops_json)
            src_comp = slot_override or list_default_troops
            if not src_comp:
                log.warning(
                    "farmlist.slot.no_troops", slot_id=slot.id, farmlist_id=fl.id,
                    hint="set default_troops on the list or troops_json on the slot",
                )
                continue

            templates = [{k: v} for k, v in src_comp.items() if v > 0]
            # Prefer fast (t4+) on the far half, slow on the near half; fall
            # back to the other class when the preferred one is empty or
            # unaffordable. This lets a list with mixed infantry+cavalry use
            # the right horse for the right distance within one tick.
            is_far = i >= mid
            templates.sort(
                key=lambda t: 0 if _is_fast(t) == is_far else 1,
            )

            troops = None
            for tpl in templates:
                if all(home_troops.get(k, 0) >= v for k, v in tpl.items()):
                    troops = tpl
                    break
            if troops is None:
                # Log once per list — the default composition's cheapest
                # template is unaffordable, so farther slots won't fare better.
                shorts = {
                    list(t.keys())[0]: list(t.values())[0]
                    - home_troops.get(list(t.keys())[0], 0)
                    for t in templates
                }
                log.info(
                    "farmlist.slot.insufficient_troops",
                    slot_id=slot.id, need=src_comp,
                    short=[f"{k}:{v}" for k, v in shorts.items() if v > 0],
                )
                break

            # Skip unoccupied oases that still have animals — the nature
            # garrison would chew through a farm squad with nothing to show
            # for it. Fail open: a flaky tile-details endpoint mustn't stall
            # the farming loop, so `None` (fetch error) falls through to the
            # raid. Only unoccupied oases are checked — occupied oases hide
            # their garrison from this endpoint anyway.
            if slot.tile.type == TileType.OASIS and slot.tile.player_name is None:
                animals = await get_oasis_animals_cached(session, slot.tile)
                if animals:
                    log.info(
                        "farmlist.slot.skip_animals",
                        slot_id=slot.id, x=slot.tile.x, y=slot.tile.y,
                        animals=animals,
                    )
                    continue

            target = RaidTarget(x=slot.tile.x, y=slot.tile.y, troops=troops)
            log.info(
                "farmlist.slot.send",
                farmlist_id=fl.id, slot_id=slot.id,
                x=target.x, y=target.y, troops=troops,
            )
            try:
                ok = await rally.send_raid(target)
            except Exception as e:  # noqa: BLE001
                log.exception(
                    "farmlist.slot.send.error", slot_id=slot.id, err=str(e),
                )
                ok = False
            if not ok:
                log.warning("farmlist.slot.send.declined", slot_id=slot.id)
                consecutive_fails += 1
                if consecutive_fails >= MAX_CONSECUTIVE_SEND_FAILS:
                    log.warning(
                        "farmlist.dispatch.abort",
                        reason="consecutive send failures",
                        fails=consecutive_fails,
                        dispatched=dispatched,
                    )
                    return dispatched
                continue

            # Debit the running budget — the next slot sees the reduced count.
            for k, n in troops.items():
                home_troops[k] = home_troops.get(k, 0) - n
            slot.last_raid_at = now
            dispatched += 1
            consecutive_fails = 0
            # Humans don't fire raids back-to-back. Between-slot jitter
            # dominates the pattern multi-hunter sees.
            await asyncio.sleep(random.uniform(2.5, 8.0))

    return dispatched


def _is_fast(tpl: dict[str, int]) -> bool:
    """Fast = contains any t4..t10 (cavalry / siege / chief / settler).

    Driven by Travian's training-building conventions: t1..t3 are infantry
    (slow), t4+ ride / are transported. Classification per template lets us
    route cavalry raids to distant slots and infantry raids to nearby ones.
    """
    return any(k.startswith("t") and int(k[1:]) >= 4 for k in tpl)


def _decode_troops(raw: str | None) -> dict[str, int]:
    """Decode the troops_json column; tolerate empty/invalid content."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return {k: int(v) for k, v in data.items() if int(v) > 0}


def _slot_is_due(
    slot: FarmlistSlot, now: datetime, effective_interval: float,
) -> bool:
    """True iff this slot has never raided or its last raid is older than
    `effective_interval` seconds (± 15 % jitter so the pattern isn't periodic).
    """
    if slot.last_raid_at is None:
        return True
    elapsed = (now - slot.last_raid_at).total_seconds()
    jitter = effective_interval * random.uniform(-0.15, 0.15)
    return elapsed >= (effective_interval + jitter)


# ----- Maintenance -----

async def maintain_farmlists(db: AsyncSession, village_id: int) -> int:
    """Auto-disable slots whose consecutive-loss counter crossed the threshold.

    We eager-load `Farmlist.slots` via `selectinload` so the iteration below
    doesn't trigger a sync lazy-load inside an async session (which would blow
    up with MissingGreenlet — SQLAlchemy 2.0 requires explicit eager loading
    for async).
    """
    fls = (
        await db.execute(
            select(Farmlist)
            .where(Farmlist.village_id == village_id)
            .options(selectinload(Farmlist.slots))
        )
    ).scalars().all()
    disabled = 0
    for fl in fls:
        for s in fl.slots:
            if s.enabled and s.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
                s.enabled = False
                disabled += 1
                log.warning(
                    "farmlist.slot.auto_disable",
                    slot_id=s.id, tile_id=s.tile_id,
                    losses=s.consecutive_losses,
                )
    if disabled:
        log.info("farmlist.maintain.summary", village_id=village_id, disabled=disabled)
    return disabled


# ----- Auto-populate from MapTiles -----

async def sync_list_from_tiles(
    db: AsyncSession,
    village_id: int,
    list_name: str,
    kind: FarmlistKind,
    tile_query,  # a pre-built SQLA query returning MapTile rows
) -> tuple[int, int]:
    """Create the named list if missing, add any missing tiles as slots.
    Returns (added, total_slots)."""
    fl = await get_or_create_farmlist(db, village_id, list_name, kind)
    tiles = (await db.execute(tile_query)).scalars().all()
    existing_ids = {
        s.tile_id for s in (
            await db.execute(select(FarmlistSlot).where(FarmlistSlot.farmlist_id == fl.id))
        ).scalars().all()
    }
    added = 0
    for t in tiles:
        if t.id in existing_ids:
            continue
        await add_slot_for_tile(db, fl.id, t.id)
        added += 1
    log.info(
        "farmlist.sync",
        list=list_name, kind=kind.value,
        added=added, total=len(existing_ids) + added,
    )
    return added, len(existing_ids) + added


def tile_query_villages(server_code: str):
    """MapTiles that are enemy villages (not Natars, not mine)."""
    return (
        select(MapTile)
        .where(
            MapTile.server_code == server_code,
            MapTile.type == TileType.VILLAGE,
        )
    )


def tile_query_oases_natars(server_code: str):
    return (
        select(MapTile)
        .where(
            MapTile.server_code == server_code,
            MapTile.type.in_([TileType.OASIS, TileType.NATAR]),
        )
    )


def tile_query_oases_natars_near(
    server_code: str, cx: int, cy: int, radius: int,
):
    """Oases + Natars whose Chebyshev distance to (cx, cy) is ≤ radius.

    Filtering in SQL (not Python) keeps the slot-sync fast as the map_tiles
    table grows. Chebyshev (max of abs dx / abs dy) is the square-radius
    semantics the scan sweep uses, so the two stay in sync.
    """
    from sqlalchemy import func as _f
    return (
        select(MapTile)
        .where(
            MapTile.server_code == server_code,
            MapTile.type.in_([TileType.OASIS, TileType.NATAR]),
            _f.abs(MapTile.x - cx) <= radius,
            _f.abs(MapTile.y - cy) <= radius,
        )
    )
