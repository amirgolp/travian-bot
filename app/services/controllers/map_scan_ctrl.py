"""MapScanController — ~24 h oasis/natar refresh + oases farmlist sync.

Scan radius defaults to 25 tiles around each of the account's villages; we
keep it small because (a) stealth — humans don't pan the whole world — and
(b) raidable oases are local anyway.
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func

from app.browser.server import detect_server
from app.core.logging import get_logger
from app.core.reconciler import Controller, ControllerContext, ReconcileResult
from app.db.session import SessionLocal
from app.models.account import Account
from app.models.farmlist import FarmlistKind
from app.models.map_tile import MapTile, TileType
from app.models.village import Village
from app.services import farming, map_scan

log = get_logger("ctrl.map_scan")

MIN_RESYNC = timedelta(hours=24)
DEFAULT_LIST_NAME = "auto: oases+natars (map scan)"
SCAN_RADIUS = 30  # tiles around each village (Chebyshev: square 60×60)
# The /api/v1/map/position response is a fixed ~11×9 window at zoomLevel 2,
# so STEP must be ≤9 to avoid vertical gaps. At STEP=9 and radius 30 that's
# (~7×7=49) calls per village × 1.8-5.5s each ≈ 2-4 min per village.
STEP = 9


class MapScanController(Controller):
    name = "map_scan"
    resync_seconds = 3600.0

    async def should_run(self, ctx: ControllerContext) -> bool:  # noqa: ARG002
        # Always run — reconcile() decides whether the scan is due (expensive,
        # 24h cooldown) and always runs the farmlist sync (cheap DB read).
        # Gating should_run on `max(last_seen_at)` previously suppressed the
        # sync too, so new oases already in the DB never made it into the
        # farmlist until the scan cooldown expired.
        return True

    async def _scan_is_due(self, server_code: str) -> bool:
        # Use OASIS tiles as the canary for "has the ajax sweep actually run".
        # Natars also live in map_tiles but WorldSqlController stamps their
        # last_seen_at on every world.sql import (natars show up in map.sql),
        # so including NATAR here would make every fresh account look
        # "recently scanned" the moment world.sql finishes — and the sweep
        # would then be suppressed for 24 h. Oases, by contrast, never appear
        # in map.sql; they only land via this controller's ajax fetch.
        async with SessionLocal() as db:
            last = (
                await db.execute(
                    select(func.max(MapTile.last_seen_at)).where(
                        MapTile.server_code == server_code,
                        MapTile.type == TileType.OASIS,
                    )
                )
            ).scalar_one()
        if last is None:
            return True
        since = datetime.now(tz=timezone.utc) - last
        return since >= MIN_RESYNC

    async def reconcile(self, ctx: ControllerContext) -> ReconcileResult:
        async with SessionLocal() as db:
            acc = await db.get(Account, ctx.account_id)
            if acc is None:
                return ReconcileResult(message="no account")
            server = detect_server(acc.server_url)
            villages = (
                await db.execute(
                    select(Village).where(Village.account_id == ctx.account_id)
                )
            ).scalars().all()

            scan_due = await self._scan_is_due(server.code)
            log.info(
                "map_scan.reconcile.start",
                account_id=ctx.account_id, villages=len(villages),
                scan_due=scan_due,
            )

            total_new = total_upd = 0
            # Skip the ajax sweep entirely when the map data is still fresh
            # (<24h since last seen); the farmlist sync below still runs so
            # anything already in the DB that's missing from the list gets
            # added on every controller tick.
            for v in villages if scan_due else ():
                rects = map_scan.sweep_rectangles((v.x, v.y), SCAN_RADIUS, STEP)
                log.info(
                    "map_scan.sweep",
                    village=v.name, center=(v.x, v.y),
                    rects=len(rects), radius=SCAN_RADIUS,
                )
                # Adjacent windows share boundary columns/rows, and the server
                # returns a fixed-size box around the window center rather than
                # exactly our (tl, br) rectangle — so the same (x, y) tile often
                # comes back on 2-4 consecutive calls. Collapse by coordinate
                # before we touch the DB: one round-trip per unique tile
                # instead of up to 4.
                deduped: dict[tuple[int, int], object] = {}
                fetched_total = 0
                for tl, br in rects:
                    tiles = await map_scan.fetch_tiles(ctx.session, tl, br)
                    fetched_total += len(tiles)
                    for t in tiles:
                        # Later windows overwrite earlier — values are
                        # equivalent for the same (x, y) since the server
                        # sends one consistent shape per tile.
                        deduped[(t.x, t.y)] = t
                    # Be humane — space the ajax calls out.
                    await asyncio.sleep(random.uniform(1.8, 5.5))

                if deduped:
                    log.info(
                        "map_scan.sweep.collected",
                        village=v.name, fetched=fetched_total, unique=len(deduped),
                        dup_ratio=round(
                            1 - len(deduped) / fetched_total, 2
                        ) if fetched_total else 0,
                    )
                    new, upd = await map_scan.upsert_scanned(
                        db, server.code, deduped.values(),
                    )
                    total_new += new
                    total_upd += upd

            # Sync / populate oases farmlist for each village. Restrict the
            # slot set to tiles within SCAN_RADIUS of THIS village — long-
            # distance raids aren't useful, and scanning the whole world for
            # one list name is deferred to a later iteration.
            total_added = 0
            for v in villages:
                added, _ = await farming.sync_list_from_tiles(
                    db, v.id, DEFAULT_LIST_NAME, FarmlistKind.OASES_NATARS,
                    farming.tile_query_oases_natars_near(
                        server.code, v.x, v.y, SCAN_RADIUS,
                    ),
                )
                total_added += added

            await db.commit()

        msg = f"new_tiles={total_new} updated={total_upd} slots_added={total_added}"
        log.info("map_scan.done", account_id=ctx.account_id, summary=msg)
        return ReconcileResult(message=msg, requeue_after=MIN_RESYNC.total_seconds())
