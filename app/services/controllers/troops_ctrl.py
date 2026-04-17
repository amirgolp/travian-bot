"""TroopsController — keep per-village troop counts + movements current."""
from __future__ import annotations

from sqlalchemy import select

from app.core.logging import get_logger
from app.core.reconciler import Controller, ControllerContext, ReconcileResult
from app.db.session import SessionLocal
from app.models.village import Village
from app.services import troops as troops_svc

log = get_logger("ctrl.troops")


class TroopsController(Controller):
    name = "troops"
    # ~7 min is a good trade-off: catches incoming attacks within a single
    # warning window (the in-game warning itself gives ~2 min for short range)
    # but doesn't thrash the rally-point page every minute.
    resync_seconds = 420.0

    async def reconcile(self, ctx: ControllerContext) -> ReconcileResult:
        async with SessionLocal() as db:
            refreshed = await troops_svc.sync_all_villages(db, ctx.session, ctx.account_id)
            # First-login race: VillagesController may still be populating the
            # Village table when our initial tick fires, so `refreshed=0` with
            # un-scraped villages is normal for ~10s after login. Requeue fast
            # in that case so the user doesn't wait a full 7 min for the very
            # first troops snapshot.
            unscraped = (
                await db.execute(
                    select(Village.id).where(
                        Village.account_id == ctx.account_id,
                        Village.troops_observed_at.is_(None),
                    )
                )
            ).scalars().all()
            await db.commit()
        if unscraped:
            return ReconcileResult(
                message=f"villages={refreshed} pending={len(unscraped)}",
                requeue_after=30.0,
            )
        return ReconcileResult(message=f"villages={refreshed}")
