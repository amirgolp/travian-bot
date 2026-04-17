"""MaintenanceController — disables chronically losing farm slots."""
from __future__ import annotations

from sqlalchemy import select

from app.core.logging import get_logger
from app.core.reconciler import Controller, ControllerContext, ReconcileResult
from app.db.session import SessionLocal
from app.models.village import Village
from app.services import farming

log = get_logger("ctrl.maintenance")


class MaintenanceController(Controller):
    name = "maintenance"
    resync_seconds = 1200.0  # every ~20 min

    async def reconcile(self, ctx: ControllerContext) -> ReconcileResult:
        async with SessionLocal() as db:
            villages = (
                await db.execute(select(Village).where(Village.account_id == ctx.account_id))
            ).scalars().all()
            total_disabled = 0
            for v in villages:
                total_disabled += await farming.maintain_farmlists(db, v.id)
            await db.commit()
            log.debug("reconcile.done", villages=len(villages), disabled=total_disabled)
        return ReconcileResult(message=f"disabled={total_disabled}")
