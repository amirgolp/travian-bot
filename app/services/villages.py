"""Upsert villages into the DB from the sidebar scrape."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.pages.dorf import Dorf1Page
from app.browser.pages.sidebar import SidebarVillage, SidebarVillages
from app.browser.session import BrowserSession
from app.core.logging import get_logger
from app.models.village import Village
from app.services.tribes import detect_tribe

log = get_logger("service.villages")


async def sync_sidebar(
    db: AsyncSession, session: BrowserSession, account_id: int
) -> tuple[int, int]:
    """Navigate to dorf1, read the sidebar, upsert villages. Returns (new, updated)."""
    # Make sure we're on a page that renders the sidebar.
    dorf1 = Dorf1Page(session.page)
    await dorf1.goto_dorf1()

    reader = SidebarVillages(session.page)
    entries: list[SidebarVillage] = await reader.read()
    if not entries:
        log.warning("villages.sync.empty_sidebar", account_id=account_id)
        return 0, 0

    existing = (
        await db.execute(select(Village).where(Village.account_id == account_id))
    ).scalars().all()
    by_did = {v.travian_id: v for v in existing}

    new = updated = 0
    for e in entries:
        v = by_did.get(e.travian_id)
        if v is None:
            v = Village(
                account_id=account_id,
                travian_id=e.travian_id,
                name=e.name,
                x=e.x, y=e.y,
                is_capital=e.is_capital,
            )
            db.add(v)
            new += 1
            log.info("village.new", did=e.travian_id, name=e.name, xy=(e.x, e.y))
        else:
            changed = (
                v.name != e.name or v.x != e.x or v.y != e.y
                or v.is_capital != e.is_capital
            )
            if changed:
                v.name = e.name
                v.x = e.x
                v.y = e.y
                v.is_capital = e.is_capital
                updated += 1
                log.debug("village.update", did=e.travian_id, name=e.name)

    await db.flush()

    # Detect tribe once from the dorf1 we're already on and stamp every village
    # that doesn't have one yet. All of an account's villages share the player's
    # tribe, so a single DOM read covers them all. Doing this in sync_sidebar
    # means the tribe is set the moment a village row exists — downstream UI
    # (troop name catalog on the farmlist page, troop goals, etc.) no longer
    # has to wait for the first TroopsController tick.
    tribeless = (
        await db.execute(
            select(Village).where(
                Village.account_id == account_id, Village.tribe.is_(None),
            )
        )
    ).scalars().all()
    if tribeless:
        detected = await detect_tribe(session)
        if detected is not None:
            for v in tribeless:
                v.tribe = detected
            await db.flush()
            log.info(
                "villages.sync.tribe_detected",
                account_id=account_id, tribe=detected.value, applied=len(tribeless),
            )
        else:
            log.debug("villages.sync.tribe_unknown", account_id=account_id)

    log.info("villages.sync.done", account_id=account_id, new=new, updated=updated)
    return new, updated
