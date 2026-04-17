"""TrainingController — drives TroopGoals."""
from __future__ import annotations

from app.core.logging import get_logger
from app.core.reconciler import Controller, ControllerContext, ReconcileResult
from app.db.session import SessionLocal
from app.services import training

log = get_logger("ctrl.training")


class TrainingController(Controller):
    name = "training"
    # ~10 min is a good cadence: training an individual unit takes minutes
    # anyway, and resources don't refill meaningfully faster than this.
    resync_seconds = 600.0

    async def reconcile(self, ctx: ControllerContext) -> ReconcileResult:
        async with SessionLocal() as db:
            submitted = await training.run_for_account(db, ctx.session, ctx.account_id)
            await db.commit()
        return ReconcileResult(message=f"submitted={submitted}")
