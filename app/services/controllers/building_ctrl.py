"""BuildingController — reconciles the build queue."""
from __future__ import annotations

from sqlalchemy import select

from app.core.logging import get_logger
from app.core.reconciler import Controller, ControllerContext, ReconcileResult
from app.db.session import SessionLocal
from app.models.village import Village
from app.services import building

log = get_logger("ctrl.building")


class BuildingController(Controller):
    name = "building"
    # The game itself has seconds-to-minutes granularity on construction; we
    # don't need to beat the drum faster than every few minutes.
    resync_seconds = 240.0

    async def reconcile(self, ctx: ControllerContext) -> ReconcileResult:
        messages: list[str] = []
        async with SessionLocal() as db:
            villages = (
                await db.execute(select(Village).where(Village.account_id == ctx.account_id))
            ).scalars().all()
            log.debug("reconcile", account_id=ctx.account_id, villages=len(villages))
            for v in villages:
                try:
                    msg = await building.tick(db, ctx.session, v)
                    log.info("village.build.tick", village_id=v.id, status=msg)
                    messages.append(f"v{v.id}:{msg}")
                except Exception as e:
                    log.exception("village.build.error", village_id=v.id, err=str(e))
                    messages.append(f"v{v.id}:error")
            await db.commit()
        return ReconcileResult(message=", ".join(messages) or "no villages")
