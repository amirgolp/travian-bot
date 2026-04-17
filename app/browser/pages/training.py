"""Troop-training page — shared across barracks / stable / workshop / residence.

Selectors calibrated from samples/legend/stable + barracks (2026-04-16):
  input.text[name="tN"]          — the per-unit count input
  <a href="#">MAX</a>            — the "max trainable given resources" link
                                   sits next to the input and its text is that
                                   maximum as a plain integer
  button.startTraining           — the green Train button

The page can show 0..3 unit inputs depending on which tech the player has
researched in that building. We only interact with the unit whose name
matches the goal's `tN` key; if the input isn't present, training is gated
(missing smithy level / missing building level) and we log and skip.
"""
from __future__ import annotations

import re

from playwright.async_api import Page

from app.browser.humanize import human_click, human_type, read_page, sleep_action
from app.core.logging import get_logger

log = get_logger("page.training")


class TrainingPage:
    class Selectors:
        UNIT_INPUT = 'input.text[name="{name}"]'
        START_BUTTON = "button.startTraining"
        # The "max trainable" link sits in the same `.cta` block as the input;
        # we grab the first `a` in that block and parse its integer text.
        MAX_LINK = 'a[href="#"]'

    def __init__(self, page: Page):
        self.page = page

    def _origin(self) -> str:
        return "/".join(self.page.url.split("/", 3)[:3])

    async def open(self, gid: int) -> None:
        url = f"{self._origin()}/build.php?gid={gid}"
        log.debug("training.open", gid=gid, url=url)
        await self.page.goto(url, wait_until="domcontentloaded")
        await read_page(self.page, words=30)

    async def max_trainable(self, troop_key: str) -> int:
        """Return the resource-constrained maximum for `troop_key` on this page.

        Reads the max-link's visible integer text. Returns 0 when the unit
        isn't offered here (gated by research / building level).
        """
        inp = self.page.locator(self.Selectors.UNIT_INPUT.format(name=troop_key)).first
        if await inp.count() == 0:
            return 0
        # Walk up to the `.cta` block so we scope the max link to this unit.
        cta = inp.locator("xpath=ancestor::div[contains(@class,'cta')][1]")
        link = cta.locator(self.Selectors.MAX_LINK).first
        if await link.count() == 0:
            return 0
        raw = (await link.text_content()) or ""
        m = re.search(r"\d+", raw.replace(",", "").replace(".", ""))
        return int(m.group(0)) if m else 0

    async def train(self, troop_key: str, count: int) -> bool:
        """Type `count` into the unit input and click Start Training.

        Returns True if we submitted the form. Does NOT verify success beyond
        the click — the caller re-polls troop counts on the next reconcile.
        """
        if count <= 0:
            return False
        inp = self.page.locator(self.Selectors.UNIT_INPUT.format(name=troop_key)).first
        if await inp.count() == 0:
            log.warning("training.unit_not_offered", troop=troop_key)
            return False

        # Clear then type — `human_type` clicks + types char-by-char.
        await inp.fill("")  # fast reset
        await human_type(self.page, inp, str(count))
        await sleep_action(scale=0.6)

        btn = self.page.locator(self.Selectors.START_BUTTON).first
        if await btn.count() == 0:
            log.warning("training.no_start_button", troop=troop_key)
            return False
        await human_click(self.page, btn)
        await sleep_action()
        log.info("training.submitted", troop=troop_key, count=count)
        return True
