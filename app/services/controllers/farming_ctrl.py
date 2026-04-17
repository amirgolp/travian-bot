"""FarmingController — dispatch due farmlists."""
from __future__ import annotations

import random

from sqlalchemy import select

from app.core.logging import get_logger
from app.core.reconciler import Controller, ControllerContext, ReconcileResult
from app.db.session import SessionLocal
from app.models.village import Village
from app.services import farming

log = get_logger("ctrl.farming")


class FarmingController(Controller):
    name = "farming"
    # Resync cadence: every ~6 min. Individual farmlists have their own
    # interval_seconds which is checked inside the dispatch call.
    resync_seconds = 360.0

    async def reconcile(self, ctx: ControllerContext) -> ReconcileResult:
        async with SessionLocal() as db:
            villages = (
                await db.execute(select(Village).where(Village.account_id == ctx.account_id))
            ).scalars().all()
            log.debug("reconcile", account_id=ctx.account_id, villages=len(villages))

            # 15% of ticks we entirely skip — humans don't hit "start all" like clockwork.
            if random.random() < 0.15:
                log.info("farming.skip_human_jitter")
                return ReconcileResult(message="skip(humanized)")

            dispatched_total = 0
            for v in villages:
                try:
                    n = await farming.run_due_farmlists(db, ctx.session, v)
                    dispatched_total += n
                    if n:
                        log.info("village.dispatched", village_id=v.id, lists=n)
                except Exception as e:
                    log.exception("village.dispatch.error", village_id=v.id, err=str(e))
            await db.commit()
        return ReconcileResult(message=f"dispatched={dispatched_total}")
