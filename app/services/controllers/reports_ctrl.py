"""ReportsController — ingest reports, parse bounty, update tile aggregates."""
from __future__ import annotations

from sqlalchemy import select

from app.browser.server import detect_server
from app.core.logging import get_logger
from app.core.reconciler import Controller, ControllerContext, ReconcileResult
from app.db.session import SessionLocal
from app.models.account import Account
from app.services import reports

log = get_logger("ctrl.reports")


class ReportsController(Controller):
    name = "reports"
    # Raid round-trips are typically ~5-20 min. Re-ingest every ~3 min so we
    # get new reports promptly but don't hammer the reports page.
    resync_seconds = 180.0

    async def reconcile(self, ctx: ControllerContext) -> ReconcileResult:
        async with SessionLocal() as db:
            acc = await db.get(Account, ctx.account_id)
            if acc is None:
                log.warning("reports.no_account", account_id=ctx.account_id)
                return ReconcileResult(message="no account")
            server = detect_server(acc.server_url)
            stored = await reports.ingest_list(
                db, ctx.session, server.code, ctx.account_id, limit=40
            )
            await db.commit()
            log.info("reports.ingest.summary", stored=stored)
        return ReconcileResult(message=f"stored={stored}")
