"""build.php — upgrade / construct a building.

Two flavors:
  /build.php?id=SLOT             -> view / upgrade existing
  /build.php?id=SLOT&category=N  -> construct new (empty slot)
"""
from __future__ import annotations

from dataclasses import dataclass

from playwright.async_api import Page

from app.browser.humanize import human_click, read_page, sleep_action
from app.browser.video_bonus import watch_all_available
from app.core.logging import get_logger

log = get_logger("page.build")


@dataclass
class ConstructOption:
    building_key: str      # e.g. "warehouse", "barracks"
    can_build: bool        # false if prereqs/resources missing
    missing_reason: str | None  # human readable, when can_build is false


class BuildPage:
    class Selectors:
        # Important: exclude `.videoFeatureButton` (purple "Upgrade 25 % faster"
        # button that triggers a video ad). The normal green Upgrade sits
        # alongside it on every building's page — we want the green one.
        UPGRADE_BUTTON = (
            ".upgradeButtonsContainer button.green:not(.videoFeatureButton), "
            ".section1 button.green:not(.videoFeatureButton)"
        )
        CURRENT_LEVEL = ".level"
        BUILD_CATEGORY_TABS = ".contractLink, #contract .tabs a"
        BUILDABLE_BUTTON = ".buildingWrapper button.green:not(.videoFeatureButton)"
        BUILDING_NAME = ".buildingWrapper h2"
        ERROR_TEXT = ".errorMessage, .inlineHelp.red"
        # Kept for reference / deliberate future use. We do NOT click these
        # during normal automation — consistently taking video bonuses is a
        # strong behavioral tell for multi-hunter.
        VIDEO_SPEEDUP_BUTTON = "button.videoFeatureButton"

    def __init__(self, page: Page, *, watch_videos: bool = True):
        self.page = page
        # Whether to watch "+25 % faster" video bonuses before each upgrade.
        # Wired from account.watch_video_bonuses via the building service.
        self.watch_videos = watch_videos

    def _origin(self) -> str:
        return "/".join(self.page.url.split("/", 3)[:3])

    async def open_slot(self, slot: int) -> None:
        url = f"{self._origin()}/build.php?id={slot}"
        log.debug("build.open_slot", slot=slot, url=url)
        await self.page.goto(url, wait_until="domcontentloaded")
        await read_page(self.page, words=30)

    async def upgrade_here(self) -> bool:
        """If the slot has a built building, click Upgrade. Returns True on success.

        When `watch_videos` is on, we watch the purple "Upgrade 25 % faster"
        bonus first. Important wrinkle: that button's onclick is
        `VideoFeature.openVideo({type: 'buildingUpgrade', ...})` — completing
        the watch IS the upgrade dispatch, with a 25 % speedup applied. After
        a successful watch the green Upgrade button legitimately disappears
        because the upgrade has already started, so we treat that state as
        success rather than as a UI refusal.
        """
        btn = self.page.locator(self.Selectors.UPGRADE_BUTTON).first
        if await btn.count() == 0:
            log.debug("build.upgrade_here.no_button")
            return False
        if self.watch_videos:
            watched = await watch_all_available(self.page, limit=1)
            # Re-query: the DOM can re-render after the modal closes.
            btn = self.page.locator(self.Selectors.UPGRADE_BUTTON).first
            if await btn.count() == 0:
                if watched > 0:
                    log.info("build.upgrade_here.via_video")
                    return True
                log.warning("build.upgrade_here.gone_after_video")
                return False
        await human_click(self.page, btn)
        await sleep_action()
        log.info("build.upgrade_here.clicked")
        return True

    async def construct(self, slot: int, building_key: str) -> bool:
        """Build `building_key` in an empty slot. Returns True on success."""
        await self.open_slot(slot)
        candidate = self.page.locator(
            f'{self.Selectors.BUILDING_NAME}:has-text("{building_key}")'
        ).first
        if await candidate.count() == 0:
            log.warning("build.construct.not_offered", slot=slot, building=building_key)
            return False
        wrapper = candidate.locator("xpath=ancestor::div[contains(@class,\"buildingWrapper\")][1]")
        btn = wrapper.locator("button.green:not(.videoFeatureButton)").first
        if await btn.count() == 0:
            log.warning("build.construct.no_green_button", slot=slot, building=building_key)
            return False
        if self.watch_videos:
            watched = await watch_all_available(self.page, scope=wrapper, limit=1)
            btn = wrapper.locator("button.green:not(.videoFeatureButton)").first
            if await btn.count() == 0:
                if watched > 0:
                    log.info("build.construct.via_video", slot=slot, building=building_key)
                    return True
                log.warning("build.construct.gone_after_video", slot=slot, building=building_key)
                return False
        await human_click(self.page, btn)
        await sleep_action()
        log.info("build.construct.clicked", slot=slot, building=building_key)
        return True
