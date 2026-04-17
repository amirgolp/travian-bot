"""HeroController — keep HeroStats fresh."""
from __future__ import annotations

from app.core.logging import get_logger
from app.core.reconciler import Controller, ControllerContext, ReconcileResult
from app.db.session import SessionLocal
from app.services import hero as hero_svc

log = get_logger("ctrl.hero")


class HeroController(Controller):
    name = "hero"
    # Every ~15 min: adventures expire in 24h, health regens on the hour,
    # so we don't need real-time polling.
    resync_seconds = 900.0

    async def reconcile(self, ctx: ControllerContext) -> ReconcileResult:
        async with SessionLocal() as db:
            stats = await hero_svc.sync_hero(db, ctx.session, ctx.account_id)
            await db.commit()
        msg = f"hp={stats.health_pct}% xp={stats.experience} adv={stats.adventures_available}"
        log.info("reconcile.hero.done", account_id=ctx.account_id, summary=msg)
        return ReconcileResult(message=msg)
