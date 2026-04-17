"""WorldSqlController — nightly map.sql sync + villages-farmlist refresh.

Runs at most once every ~22 hours per server. The actual cadence is checked
in `should_run` so we don't care if the controller wakes up every hour; only
real sync attempts hit the network.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func

from app.browser.server import detect_server
from app.core.logging import get_logger
from app.core.reconciler import Controller, ControllerContext, ReconcileResult
from app.db.session import SessionLocal
from app.models.account import Account
from app.models.farmlist import FarmlistKind
from app.models.map_tile import MapTile
from app.models.village import Village
from app.services import farming, world_sql

log = get_logger("ctrl.world_sql")

MIN_RESYNC = timedelta(hours=22)
DEFAULT_LIST_NAME = "auto: villages (world.sql)"


class WorldSqlController(Controller):
    name = "world_sql"
    resync_seconds = 3600.0  # wake hourly; actually work daily

    async def should_run(self, ctx: ControllerContext) -> bool:
        async with SessionLocal() as db:
            acc = await db.get(Account, ctx.account_id)
            if not acc:
                return False
            server = detect_server(acc.server_url).code
            last = (
                await db.execute(
                    select(func.max(MapTile.last_seen_at)).where(MapTile.server_code == server)
                )
            ).scalar_one()
        if last is None:
            log.info("world_sql.first_run", account_id=ctx.account_id)
            return True
        since = datetime.now(tz=timezone.utc) - last
        run = since >= MIN_RESYNC
        log.debug("world_sql.since_last", hours=since.total_seconds() / 3600, run=run)
        return run

    async def reconcile(self, ctx: ControllerContext) -> ReconcileResult:
        async with SessionLocal() as db:
            acc = await db.get(Account, ctx.account_id)
            if acc is None:
                return ReconcileResult(message="no account")
            server = detect_server(acc.server_url)

            new, updated = await world_sql.sync_world_sql(db, server.code, acc.server_url)
            await db.flush()

            # Refresh / populate the villages farmlist for each owned village.
            villages = (
                await db.execute(select(Village).where(Village.account_id == ctx.account_id))
            ).scalars().all()
            total_added = 0
            for v in villages:
                added, _ = await farming.sync_list_from_tiles(
                    db, v.id, DEFAULT_LIST_NAME, FarmlistKind.VILLAGES,
                    farming.tile_query_villages(server.code),
                )
                total_added += added
            await db.commit()

        msg = f"new_tiles={new} updated={updated} slots_added={total_added}"
        log.info("world_sql.reconcile.done", account_id=ctx.account_id, summary=msg)
        return ReconcileResult(message=msg, requeue_after=MIN_RESYNC.total_seconds())
