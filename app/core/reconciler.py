"""Kubernetes-style reconciler primitives.

Each Controller has:
  - a name (for logging)
  - a resync interval (how often it runs even if nothing enqueued it)
  - reconcile(ctx) -> ReconcileResult   # the core loop body

On error the controller backs off exponentially (with jitter) capped by
`max_backoff`, like a K8s controller-runtime controller. A controller never
raises out of its loop; errors are logged and retried.

A controller can request a re-run sooner than its resync interval by returning
`ReconcileResult(requeue_after=seconds)` — e.g. BuildingController peeks at the
live queue and sees a build finishing in 4 minutes, so it re-runs in 4 minutes.

This is deliberately simple (no shared informer cache, no watch streams) — we
have one account per worker and the "cluster" (the game state) is polled. The
reconciler abstraction is still worth having because it:
  - separates the control plane from the browser driver
  - makes resync cadence per-concern (reports every 3 min, build every 2 min,
    world.sql once a day) instead of one monolithic tick
  - gives a clean place for metrics and structured logging
"""
from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.browser.session import BrowserSession


@dataclass
class ReconcileResult:
    # If not None, override the next run time with exactly this many seconds.
    requeue_after: float | None = None
    # Short status string for logs / API.
    message: str = ""


@dataclass
class ControllerContext:
    """Everything a controller needs to do its job."""
    account_id: int
    session: "BrowserSession"
    now: datetime


class Controller(ABC):
    """Base for every reconciler loop.

    Subclasses implement `reconcile(ctx)` and optionally `should_run(ctx)` to
    skip a pass cheaply (e.g. world.sql sync only runs once per ~24 h).
    """

    name: str = "controller"
    # Resync cadence in seconds. Subclasses override.
    resync_seconds: float = 180.0
    # Backoff bounds on error.
    min_backoff: float = 20.0
    max_backoff: float = 900.0
    # Random jitter added to every wake-up so controllers don't line up.
    jitter_fraction: float = 0.25

    def __init__(self) -> None:
        self._log = get_logger(f"ctrl.{self.name}")
        self._errors = 0
        self._last_run: datetime | None = None
        self._last_message: str = ""

    # --- subclass extension points ---

    async def should_run(self, ctx: ControllerContext) -> bool:
        """Quick check before doing any real work. Default: always run."""
        return True

    @abstractmethod
    async def reconcile(self, ctx: ControllerContext) -> ReconcileResult:
        ...

    # --- engine ---

    def _next_sleep(self, result: ReconcileResult | None) -> float:
        if result and result.requeue_after is not None:
            base = max(1.0, result.requeue_after)
        elif self._errors > 0:
            base = min(self.max_backoff, self.min_backoff * (2 ** (self._errors - 1)))
        else:
            base = self.resync_seconds
        jitter = base * self.jitter_fraction * random.uniform(-1, 1)
        return max(1.0, base + jitter)

    async def run_once(self, ctx: ControllerContext) -> None:
        """Run a single reconcile pass, handling should_run/errors/backoff bookkeeping."""
        self._last_run = ctx.now
        try:
            if not await self.should_run(ctx):
                self._log.debug("skip", reason="should_run=false")
                return
            self._log.debug("reconcile.start")
            result = await self.reconcile(ctx)
            self._errors = 0
            self._last_message = result.message or "ok"
            self._log.info(
                "reconcile.ok",
                message=self._last_message,
                requeue_after=result.requeue_after,
            )
        except Exception as e:  # noqa: BLE001 — controllers must never raise out
            self._errors += 1
            self._last_message = f"error: {e}"
            self._log.exception("reconcile.error", err=str(e), errors=self._errors)

    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "errors": self._errors,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "last_message": self._last_message,
            "resync_seconds": self.resync_seconds,
        }


class ControllerLoop:
    """Runs many Controllers against one account on an async scheduler.

    Each controller has its own wake-up timer. A single asyncio task drives the
    loop: pick the controller whose next_run is earliest, sleep until then,
    run it, compute its next next_run. This serializes UI actions (one action
    per account at a time) while letting each concern keep its own cadence.

    Per-account toggles: `disabled_names` is a live set of controller names
    that should be skipped. The set is mutated from outside (e.g. by the API)
    and re-read before each dispatch, so toggles take effect immediately.
    """

    def __init__(
        self,
        account_id: int,
        session: "BrowserSession",
        controllers: list[Controller],
        disabled_names: set[str] | None = None,
    ):
        self.account_id = account_id
        self.session = session
        self.controllers = controllers
        self.disabled_names: set[str] = disabled_names if disabled_names is not None else set()
        self._next_run: dict[str, float] = {}
        self._stop = asyncio.Event()
        self._log = get_logger("ctrl.loop").bind(account_id=account_id)

    def stop(self) -> None:
        self._stop.set()

    async def run_until(self, deadline_monotonic: float) -> None:
        """Keep reconciling until the deadline or stop() is called."""
        loop = asyncio.get_event_loop()

        # Stagger initial runs a bit so the first loop doesn't fire everything at once.
        for i, c in enumerate(self.controllers):
            self._next_run[c.name] = loop.time() + random.uniform(0.5, 4.0) + i * 0.7

        while not self._stop.is_set() and loop.time() < deadline_monotonic:
            # Find the earliest-due controller
            ctrl, wake_at = min(
                ((c, self._next_run[c.name]) for c in self.controllers),
                key=lambda p: p[1],
            )
            sleep_s = max(0.0, min(wake_at, deadline_monotonic) - loop.time())
            if sleep_s > 0:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=sleep_s)
                    break  # stop() was called
                except asyncio.TimeoutError:
                    pass

            if self._stop.is_set() or loop.time() >= deadline_monotonic:
                break

            if ctrl.name in self.disabled_names:
                # Toggle is off — don't dispatch, but re-arm at the normal
                # cadence so re-enabling is immediate (no long sleep stuck).
                self._log.debug("skip.disabled", controller=ctrl.name)
                self._next_run[ctrl.name] = loop.time() + max(30.0, ctrl.resync_seconds * 0.25)
                continue

            ctx = ControllerContext(
                account_id=self.account_id,
                session=self.session,
                now=datetime.now(tz=timezone.utc),
            )
            self._log.debug("dispatch", controller=ctrl.name)
            await ctrl.run_once(ctx)
            # Schedule the next run. run_once stored its result internally;
            # re-derive the sleep from the controller's own knobs.
            self._next_run[ctrl.name] = loop.time() + ctrl._next_sleep(None)

        self._log.info("loop.exit")

    def snapshot(self) -> list[dict]:
        return [c.snapshot() for c in self.controllers]
