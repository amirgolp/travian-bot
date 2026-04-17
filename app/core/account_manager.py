"""Per-account runtime — wires the browser session to a ControllerLoop.

Design: one process, many accounts, each running concurrently on its own
browser context + its own ControllerLoop. The AccountWorker is a thin shell
around BrowserSession + ControllerLoop. All business logic lives in the
controllers.
"""
from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime

from app.browser.humanize import (
    in_active_window,
    seconds_until_active,
)
from app.browser.login import login
from app.browser.session import BrowserSession
from app.core.config import get_settings
from app.core.logging import bind_account, get_logger
from app.core.reconciler import ControllerLoop
from app.db.session import SessionLocal
from app.models.account import Account, AccountStatus
from app.services.controllers import (
    BuildingController,
    FarmingController,
    HeroController,
    MaintenanceController,
    MapScanController,
    ReportsController,
    TrainingController,
    TroopsController,
    VillagesController,
    WorldSqlController,
)

log = get_logger("account_manager")


def build_controllers() -> list:
    """Return a fresh list of controller instances for one account.

    Order matters on first run: `VillagesController` populates `Village` rows
    from the sidebar, and every downstream controller filters by village_id.
    Put it first so a fresh account produces villages before anything else
    queries an empty table.
    """
    return [
        VillagesController(),
        HeroController(),
        TroopsController(),
        ReportsController(),
        MaintenanceController(),
        FarmingController(),
        BuildingController(),
        TrainingController(),
        WorldSqlController(),
        MapScanController(),
    ]


class AccountWorker:
    """Runs one account end-to-end until cancelled.

    Outer session loop:
      while running:
        wait until inside active_hours
        open BrowserSession → login
        run ControllerLoop for up to MAX_SESSION_MINUTES (jittered)
        close session
        sleep BREAK_MINUTES (jittered)
    """

    def __init__(self, account_id: int):
        self.account_id = account_id
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._loop: ControllerLoop | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"acct:{self.account_id}")

    async def stop(self) -> None:
        self._stop.set()
        if self._loop is not None:
            self._loop.stop()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=60)
            except asyncio.TimeoutError:
                self._task.cancel()

    async def _run(self) -> None:
        cfg = get_settings()
        consecutive_errors = 0
        # Bind the account label to all log lines emitted from this task.
        while not self._stop.is_set():
            account = await self._load_account()
            if account is None:
                log.warning("worker.account_missing", account_id=self.account_id)
                await asyncio.sleep(30)
                continue
            if account.status != AccountStatus.ACTIVE:
                log.info("worker.not_active", account=account.label, status=account.status.value)
                await asyncio.sleep(30)
                continue

            with bind_account(account.label, self.account_id):
                active_hours = account.active_hours or cfg.default_active_hours
                if not in_active_window(active_hours):
                    wait_s = seconds_until_active(active_hours)
                    log.info("worker.outside_active_window", sleep_s=int(wait_s))
                    await self._sleep_interruptible(min(wait_s, 3600))
                    continue

                session_minutes = random.uniform(
                    cfg.max_session_minutes * 0.55, cfg.max_session_minutes
                )
                log.info("worker.session.start", minutes=round(session_minutes, 1))
                deadline_mono = asyncio.get_event_loop().time() + session_minutes * 60

                try:
                    async with BrowserSession(account) as session:
                        await login(session.page, account)
                        self._loop = ControllerLoop(
                            account_id=self.account_id,
                            session=session,
                            controllers=build_controllers(),
                            disabled_names=_parse_disabled(account.disabled_controllers),
                        )
                        log.info(
                            "worker.controllers",
                            disabled=sorted(self._loop.disabled_names) or None,
                        )
                        await self._loop.run_until(deadline_mono)
                        log.info("worker.session.end.clean")
                    consecutive_errors = 0
                except Exception as e:
                    consecutive_errors += 1
                    log.exception(
                        "worker.session.error",
                        err=str(e), consecutive=consecutive_errors,
                    )
                    if consecutive_errors >= 5:
                        await self._mark_error(str(e))
                        return
                finally:
                    self._loop = None

                break_s = random.uniform(cfg.break_minutes_min, cfg.break_minutes_max) * 60
                log.info("worker.break", seconds=int(break_s))
                await self._sleep_interruptible(break_s)

    async def _load_account(self) -> Account | None:
        async with SessionLocal() as db:
            return await db.get(Account, self.account_id)

    async def _mark_error(self, reason: str) -> None:
        async with SessionLocal() as db:
            acc = await db.get(Account, self.account_id)
            if acc is not None:
                acc.status = AccountStatus.ERROR
                acc.notes = f"auto-disabled at {datetime.now().isoformat()}: {reason[:200]}"
                await db.commit()
                log.error("worker.auto_disabled", account_id=self.account_id, reason=reason)

    async def _sleep_interruptible(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(0.0, seconds))
        except asyncio.TimeoutError:
            return

    def controller_snapshot(self) -> list[dict] | None:
        return self._loop.snapshot() if self._loop else None

    def apply_toggles(self, disabled: set[str]) -> None:
        """Hot-reload the disabled-controllers set on a running worker."""
        if self._loop is not None:
            self._loop.disabled_names = set(disabled)
            log.info("worker.toggles.applied", account_id=self.account_id, disabled=sorted(disabled))


def _parse_disabled(raw: str | None) -> set[str]:
    if not raw:
        return set()
    try:
        val = json.loads(raw)
    except Exception:
        return set()
    return set(val) if isinstance(val, list) else set()


class AccountManager:
    def __init__(self) -> None:
        self._workers: dict[int, AccountWorker] = {}
        self._lock = asyncio.Lock()

    async def start(self, account_id: int) -> None:
        async with self._lock:
            if account_id in self._workers:
                log.debug("manager.start.already_running", account_id=account_id)
                return
            w = AccountWorker(account_id)
            self._workers[account_id] = w
            await w.start()
            log.info("manager.start_worker", account_id=account_id)

    async def stop(self, account_id: int) -> None:
        async with self._lock:
            w = self._workers.pop(account_id, None)
        if w:
            await w.stop()
            log.info("manager.stop_worker", account_id=account_id)

    async def stop_all(self) -> None:
        async with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        log.info("manager.stop_all", count=len(workers))
        await asyncio.gather(*(w.stop() for w in workers), return_exceptions=True)

    def status(self) -> dict[int, dict]:
        return {
            aid: {
                "running": bool(w._task and not w._task.done()),
                "controllers": w.controller_snapshot() or [],
            }
            for aid, w in self._workers.items()
        }

    def apply_toggles(self, account_id: int, disabled: set[str]) -> None:
        """Propagate a new disabled set to a running worker (if any)."""
        w = self._workers.get(account_id)
        if w is not None:
            w.apply_toggles(disabled)


_manager: AccountManager | None = None


def get_manager() -> AccountManager:
    global _manager
    if _manager is None:
        _manager = AccountManager()
    return _manager
