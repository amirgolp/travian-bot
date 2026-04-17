"""VillagesController — keeps the Village table in sync with the sidebar.

Runs first on every session (resync 10 min) because every other controller
queries `Village` rows. On first login this is the thing that populates them.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.core.reconciler import Controller, ControllerContext, ReconcileResult
from app.db.session import SessionLocal
from app.services import villages

log = get_logger("ctrl.villages")


class VillagesController(Controller):
    name = "villages"
    # Short-ish cadence: catches a freshly-founded 2nd village quickly without
    # being noisy. The actual sidebar read is a single dorf1 navigation, so it's cheap.
    resync_seconds = 600.0

    async def reconcile(self, ctx: ControllerContext) -> ReconcileResult:
        async with SessionLocal() as db:
            new, updated = await villages.sync_sidebar(db, ctx.session, ctx.account_id)
            await db.commit()
        msg = f"new={new} updated={updated}"
        log.info("reconcile.villages.done", account_id=ctx.account_id, summary=msg)
        return ReconcileResult(message=msg)
