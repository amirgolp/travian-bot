"""Per-account Playwright session.

One BrowserSession = one persistent context for one account. Contexts are never
shared — each account has its own cookies, localStorage, fingerprint seed, and
profile directory on disk.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from playwright.async_api import (
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from app.browser.fingerprint import Fingerprint, fingerprint_for
from app.browser.stealth import build_init_script
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.account import Account

log = get_logger("browser.session")

_pw_singleton: Playwright | None = None
_pw_lock = asyncio.Lock()


async def _get_playwright() -> Playwright:
    global _pw_singleton
    async with _pw_lock:
        if _pw_singleton is None:
            _pw_singleton = await async_playwright().start()
        return _pw_singleton


class BrowserSession:
    """Owns the BrowserContext + main Page for one account.

    Usage:
        async with BrowserSession(account) as sess:
            await sess.page.goto(account.server_url)
    """

    def __init__(self, account: Account):
        self.account = account
        self.fingerprint: Fingerprint = fingerprint_for(account.label)
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        # Per-account async lock — callers serialize UI actions on this.
        self.lock = asyncio.Lock()

    def profile_dir(self) -> Path:
        root = get_settings().browser_profiles_dir
        p = root / self.account.label
        p.mkdir(parents=True, exist_ok=True)
        return p

    async def start(self) -> None:
        if self.context is not None:
            log.debug("session.already_started", account=self.account.label)
            return
        log.info(
            "session.starting",
            account=self.account.label,
            profile=str(self.profile_dir()),
        )
        pw = await _get_playwright()
        fp = self.fingerprint
        log.debug(
            "session.fingerprint",
            account=self.account.label,
            ua=fp.user_agent,
            viewport=fp.viewport,
            timezone=fp.timezone,
            locale=fp.locale,
        )

        # launch_persistent_context writes cookies/localStorage/IndexedDB to disk,
        # so the session survives restarts without re-login. That matters both
        # for stealth (no login event per restart) and for user convenience.
        self.context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir()),
            headless=get_settings().headless,
            user_agent=fp.user_agent,
            viewport={"width": fp.viewport[0], "height": fp.viewport[1]},
            screen={"width": fp.screen[0], "height": fp.screen[1]},
            locale=self.account.locale or fp.locale,
            timezone_id=self.account.timezone or fp.timezone,
            color_scheme="light",
            device_scale_factor=1.0,
            is_mobile=False,
            has_touch=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            ignore_default_args=["--enable-automation"],
        )

        await self.context.add_init_script(build_init_script(fp))

        # Playwright's persistent_context may already have a default page.
        pages = self.context.pages
        self.page = pages[0] if pages else await self.context.new_page()
        log.info("session.ready", account=self.account.label)

    async def stop(self) -> None:
        if self.context is not None:
            try:
                await self.context.close()
            except Exception as e:  # noqa: BLE001
                log.warning("session.close.error", account=self.account.label, err=str(e))
            finally:
                self.context = None
                self.page = None
                log.info("session.stopped", account=self.account.label)

    async def __aenter__(self) -> "BrowserSession":
        await self.start()
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.stop()
