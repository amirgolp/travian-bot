"""Sync every village's troops + movements from the Rally Point overview.

Legends shows one rally-point per village and auto-redirects `gid=16` to the
active village's rally-point slot. To refresh each village we must switch the
active village first — the left sidebar link carries `?newdid=<travian_id>`.
We already know each village's `travian_id` from VillagesController.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.pages.dorf import Dorf1Page, Dorf2Page
from app.browser.pages.rally import Movement, RallyPointPage
from app.browser.session import BrowserSession
from app.core.logging import get_logger
from app.models.village import Village
from app.services.building import sync_slots_from_scrape
from app.services.tribes import detect_tribe

log = get_logger("service.troops")


def _movement_to_dict(m: Movement) -> dict:
    return {
        "direction": m.direction,
        "headline": m.headline,
        "target_x": m.target_x,
        "target_y": m.target_y,
        "troops": m.troops,
        "arrival_in_seconds": m.arrival_in_seconds,
        "is_attack": m.is_attack,
    }


async def _switch_active_village(session: BrowserSession, travian_id: int) -> None:
    """Navigate via the `?newdid=` param so the next rally-point request is
    scoped to this village. Using a direct URL is safer than trying to click
    the sidebar entry (and faster, since we skip the dorf1 round-trip)."""
    origin = "/".join(session.page.url.split("/", 3)[:3])
    url = f"{origin}/dorf1.php?newdid={travian_id}"
    log.debug("troops.switch_village", newdid=travian_id, url=url)
    await session.page.goto(url, wait_until="domcontentloaded")


async def sync_all_villages(
    db: AsyncSession, session: BrowserSession, account_id: int
) -> int:
    """Scrape the rally-point overview for every known village on this account.
    Returns the count of villages successfully refreshed."""
    villages = (
        await db.execute(select(Village).where(Village.account_id == account_id))
    ).scalars().all()
    if not villages:
        log.debug("troops.no_villages", account_id=account_id)
        return 0

    rally = RallyPointPage(session.page)
    dorf = Dorf1Page(session.page)
    refreshed = 0
    for v in villages:
        try:
            # `_switch_active_village` lands on dorf1 for this village — the
            # stock bar is right there, so we read resources before clicking
            # through to the rally point. One page visit, two syncs.
            await _switch_active_village(session, v.travian_id)
            if v.tribe is None:
                detected = await detect_tribe(session)
                if detected is not None:
                    v.tribe = detected
                    log.info("troops.tribe.detected", village_id=v.id, tribe=detected.value)
            try:
                res = await dorf.read_resources()
                v.wood = res.wood
                v.clay = res.clay
                v.iron = res.iron
                v.crop = res.crop
                v.warehouse_cap = res.warehouse_cap
                v.granary_cap = res.granary_cap
            except Exception as e:  # noqa: BLE001
                log.exception("troops.sync.resources_error", village_id=v.id, err=str(e))
            try:
                # What Travian is *actually building right now* (including
                # upgrades the user kicked off manually in-game). Distinct
                # from BuildOrder rows, which only track the bot's queue.
                queue = await dorf.read_build_queue()
                v.build_queue_json = json.dumps([q.__dict__ for q in queue])
            except Exception as e:  # noqa: BLE001
                log.exception("troops.sync.build_queue_error", village_id=v.id, err=str(e))

            # Refresh BuildingSlot from the live DOM. This is what makes the
            # prereq checker see user-initiated upgrades — otherwise levels
            # stay at 0 for anything the bot didn't build itself, and every
            # blocked_reason becomes a phantom ("needs main_building lvl 5"
            # even when main_building is actually 12 in-game).
            try:
                dorf1_levels = await dorf.read_field_levels()
                dorf2 = Dorf2Page(session.page)
                await dorf2.goto_dorf2()
                dorf2_levels = await dorf2.read_slot_levels()
                await sync_slots_from_scrape(db, v.id, dorf1_levels, dorf2_levels)
            except Exception as e:  # noqa: BLE001
                log.exception("troops.sync.building_slots_error", village_id=v.id, err=str(e))

            await rally.open_overview_tab()
            ov = await rally.read_overview()

            v.troops_json = json.dumps(ov.own_troops, sort_keys=True)
            v.movements_in_json = json.dumps(
                [_movement_to_dict(m) for m in ov.movements_in]
            )
            v.movements_out_json = json.dumps(
                [_movement_to_dict(m) for m in ov.movements_out]
            )
            v.troops_consumption = ov.consumption_per_hour
            v.troops_observed_at = datetime.now(tz=timezone.utc)
            refreshed += 1
            log.info(
                "troops.sync.village",
                village_id=v.id, name=v.name,
                own_total=sum(ov.own_troops.values()),
                under_attack=ov.under_attack,
                movements_in=len(ov.movements_in),
                movements_out=len(ov.movements_out),
                wood=v.wood, clay=v.clay, iron=v.iron, crop=v.crop,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("troops.sync.village_error", village_id=v.id, err=str(e))

    await db.flush()
    log.info("troops.sync.done", account_id=account_id, refreshed=refreshed)
    return refreshed
