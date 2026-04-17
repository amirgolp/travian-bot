"""Hero pages — attributes, inventory (stub), adventures.

Selectors calibrated from samples/legend/hero-attributes and hero-adventures
(Legends rof.x3, 2026-04-16). The text layout on hero.php is label/value
oriented (e.g. "Health ‭‭64‬%‬"); we rely on regexes over the `#heroV2` text
rather than deep selector nesting because the exact element IDs differ across
skins while the labels are stable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from playwright.async_api import Page

from app.browser.humanize import read_page
from app.core.logging import get_logger

log = get_logger("page.hero")


# Strip bidi marks + fancy minus for robust numeric extraction.
_BIDI = "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
_TRANS = str.maketrans("", "", _BIDI)


def _norm(s: str | None) -> str:
    return (s or "").translate(_TRANS).replace("\u2212", "-")


def _int_after(text: str, *labels: str) -> int | None:
    """Find an integer appearing shortly after any of the given labels."""
    for label in labels:
        m = re.search(
            rf"{re.escape(label)}[^0-9\-]*(-?\d[\d,.]*)", text, re.IGNORECASE
        )
        if m:
            return int(m.group(1).replace(",", "").replace(".", ""))
    return None


def _pct_after(text: str, *labels: str) -> int | None:
    for label in labels:
        m = re.search(
            rf"{re.escape(label)}[^0-9\-]*(-?\d[\d,.]*)\s*%", text, re.IGNORECASE
        )
        if m:
            return int(m.group(1).replace(",", "").replace(".", ""))
    return None


@dataclass
class HeroAttributes:
    health_pct: int | None = None
    experience: int | None = None
    speed_fph: int | None = None
    production_per_hour: int | None = None
    fighting_strength: int | None = None
    off_bonus_pct: float | None = None
    def_bonus_pct: float | None = None
    attribute_points: int = 0


@dataclass
class AdventureSummary:
    count: int                   # parsed from the adventureList rows


@dataclass
class HeroEquipmentSlot:
    slot: str                    # helmet / body / shoes / leftHand / rightHand / horse
    empty: bool
    rarity: str | None = None    # "common" | "special" | "epic" | "unique"
    # Equipment level 1..5 parsed from `quality<N>` / `tier<N>`. Empty slots
    # report level=None; the game's `neutral` quality is treated as "no level".
    level: int | None = None
    # Travian's per-item type id — e.g. 91 = a specific body armor, 103 = a
    # horse variant. Stable across servers; can be mapped to item names via a
    # catalog file later without re-scraping.
    item_type_id: int | None = None
    # The unique instance id of the equipped item (from `data-id`) — useful
    # if we ever diff equipment changes between syncs.
    instance_id: int | None = None


@dataclass
class HeroBagItem:
    item_type_id: int | None     # parsed from inner `.item<N>` class
    count: int                   # digits inside the slot (quantity stackable)


@dataclass
class HeroInventory:
    equipment: list[HeroEquipmentSlot]
    bag_count: int               # count of non-empty bag/inventory grid cells
    bag_items: list[HeroBagItem] # per-cell consumable breakdown


class HeroPage:
    """/hero is the v2 layout. The four tabs (Inventory / Attributes /
    Appearance / Crafting) are JS-driven — the `<a class="tabItem">` anchors
    have no href, so we navigate to `/hero` *once* and click the right tab.
    """

    class Selectors:
        HERO_ROOT = "#heroV2"
        # All tab panels exist in the DOM at once; Legends toggles them via
        # classes, not removal. Scope attribute-tab reads to this container
        # so we never capture text from Inventory/Appearance/Crafting.
        # `#content` is AJAX-swapped per tab; its class flips between
        # `heroV2Inventory` / `heroV2Attributes` / `heroV2Appearance` /
        # `heroV2Crafting`. Scope attribute reads to this container so
        # we never capture the previous tab's still-rendered content.
        ATTRIBUTES_PANEL = ".heroV2Attributes"
        HEALTH_LINE = ".heroStatus"
        # `<a href="/build.php?newdid=..."><i class="heroHome"></i></a>` — the
        # anchor wraps the icon, so we need `a:has(i.heroHome)`, NOT `.heroHome a`.
        HOME_LINK = "a:has(i.heroHome)"
        ATTR_INPUT = "input[name='{name}']"     # power/offBonus/defBonus/resources
        ADVENTURE_ROW = ".adventureList tr"
        TAB_ITEM = ".tabItem"
        ACTIVE_TAB = ".tabItem.active"
        EQUIPMENT_SLOT = ".heroItem.heroItemV2[data-slot]"
        BAG_ITEM_NONEMPTY = ".heroItem.heroItemV2[data-slot='inventory']:not(.empty), " \
                            ".heroItem.heroItemV2[data-slot='bag']:not(.empty)"
        # Round green "Adventures" button top-right of the hero v2 layout.
        ADVENTURES_BUTTON = ".layoutButton.adventure"

    # Labels on the four tabs, in the order they appear.
    TAB_INVENTORY = "Inventory"
    TAB_ATTRIBUTES = "Attributes"
    TAB_APPEARANCE = "Appearance"
    TAB_CRAFTING = "Crafting"

    def __init__(self, page: Page):
        self.page = page

    def _origin(self) -> str:
        return "/".join(self.page.url.split("/", 3)[:3])

    async def _ensure_on_hero(self) -> None:
        """Navigate to /hero once; no-op if we're already there."""
        if "/hero" not in self.page.url:
            url = f"{self._origin()}/hero"
            log.debug("hero.goto", url=url)
            await self.page.goto(url, wait_until="domcontentloaded")
            await read_page(self.page, words=30)

    async def _switch_tab(self, label: str) -> None:
        """Click the tab whose text == label, then wait until it's marked active."""
        await self._ensure_on_hero()
        tab = self.page.locator(
            f'{self.Selectors.TAB_ITEM}:has-text("{label}")'
        ).first
        # If already active, skip the click to avoid a jarring UI flash.
        try:
            cls = await tab.get_attribute("class", timeout=2000) or ""
        except Exception:
            log.warning("hero.tab.missing", label=label)
            return
        if "active" in cls.split():
            log.debug("hero.tab.already_active", label=label)
            return
        from app.browser.humanize import human_click
        await human_click(self.page, tab)
        # Legends JS swaps `.active` synchronously, so a short wait is enough.
        try:
            await self.page.wait_for_selector(
                f'{self.Selectors.ACTIVE_TAB}:has-text("{label}")', timeout=4000
            )
        except Exception:
            log.warning("hero.tab.no_activation", label=label)

    async def open_attributes(self) -> None:
        """Switch to the Attributes tab and wait for its content to mount.

        Legends swaps `#content`'s class between `heroV2Inventory` /
        `heroV2Attributes` / ... when a tab anchor is clicked. The click is
        async — if we return before the new content arrives, the caller reads
        the PREVIOUS tab's text and regexes garbage out of it (saw xp=-68,
        health=None in the wild).
        """
        log.debug("hero.open.attributes")
        await self._switch_tab(self.TAB_ATTRIBUTES)
        # The class swap can land before the inner stats render; wait for a
        # descendant that only exists once the attributes content is mounted.
        try:
            await self.page.wait_for_function(
                """() => {
                    const el = document.querySelector('.heroV2Attributes, .heroAttributes');
                    return !!el && /Fighting strength/i.test(el.textContent || '');
                }""",
                timeout=8000,
            )
        except Exception:
            log.warning("hero.attributes.content_never_appeared")

    async def open_inventory(self) -> None:
        log.debug("hero.open.inventory")
        await self._switch_tab(self.TAB_INVENTORY)
        try:
            await self.page.wait_for_selector(
                ".heroV2Inventory", timeout=5000, state="attached",
            )
        except Exception:
            log.warning("hero.inventory.panel_never_mounted")

    async def open_adventures(self) -> None:
        """Adventures isn't a tabItem — it's the round green button."""
        log.debug("hero.open.adventures")
        await self._ensure_on_hero()
        btn = self.page.locator(self.Selectors.ADVENTURES_BUTTON).first
        if await btn.count() == 0:
            # Fallback: direct URL (we saw /hero/adventures as a valid link).
            await self.page.goto(
                f"{self._origin()}/hero/adventures", wait_until="domcontentloaded"
            )
            await read_page(self.page, words=30)
            return
        from app.browser.humanize import human_click
        await human_click(self.page, btn)
        try:
            await self.page.wait_for_selector(
                self.Selectors.ADVENTURE_ROW, timeout=4000
            )
        except Exception:
            log.debug("hero.adventures.rows_not_visible")

    async def _input_value(self, name: str) -> int:
        try:
            val = await self.page.locator(
                self.Selectors.ATTR_INPUT.format(name=name)
            ).first.input_value(timeout=1500)
            return int(val or "0")
        except Exception:
            return 0

    async def read_attributes(self) -> HeroAttributes:
        """Parse the Attributes panel via its structured DOM.

        Each stat sits in a `.stats` section: a `.name` label div (Health /
        Experience / ...) followed by a `.value` div further down. We walk
        those pairs in JS rather than regexing concatenated text, because
        `text_content()` drops element boundaries — on some skins label and
        value merge or shuffle, producing None or bogus numbers.

        Health is additionally read from the `.filling.primary` bar's
        `style="width: NN%"` — that's the authoritative source and
        survives any text mangling.
        """
        # Pair labels to values in document order. Some stats (Fighting strength,
        # Off/Def bonus, Resources) live in a different `.stats` container than
        # their numeric `.value` — a `nextElementSibling` walk misses those.
        js = """() => {
          const panel = document.querySelector('.heroV2Attributes, .heroAttributes');
          if (!panel) return null;
          const items = [];
          panel.querySelectorAll('.name, .value').forEach((el) => {
            items.push({
              type: el.classList.contains('name') ? 'name' : 'value',
              text: (el.textContent || '').trim(),
            });
          });
          const pairs = {};
          let label = null;
          for (const it of items) {
            if (it.type === 'name') {
              label = it.text;
            } else if (label && !(label in pairs)) {
              pairs[label] = it.text;
            }
          }
          const bar = panel.querySelector('.filling.primary');
          const healthBarPct = bar ? parseInt(bar.style.width) : null;
          const attrInput = (name) => {
            const el = panel.querySelector(`input[name="${name}"]`);
            return el ? el.value : null;
          };
          return {
            pairs,
            healthBarPct,
            power: attrInput('power'),
            offBonus: attrInput('offBonus'),
            defBonus: attrInput('defBonus'),
            productionPoints: attrInput('productionPoints'),
          };
        }"""
        data = await self.page.evaluate(js)
        if data is None:
            log.warning("hero.attributes.panel_missing")
            return HeroAttributes()

        pairs: dict[str, str] = data.get("pairs") or {}
        if not pairs:
            log.warning("hero.attributes.no_pairs", keys=list(data.keys()))

        def _digits(key: str) -> int | None:
            raw = _norm(pairs.get(key, ""))
            m = re.search(r"-?\d[\d,.]*", raw)
            if not m:
                return None
            return int(m.group(0).replace(",", "").replace(".", ""))

        # Prefer the progress-bar width for health; fall back to the .value
        # text if the bar is missing (e.g. a skin change).
        health = data.get("healthBarPct")
        if not isinstance(health, int):
            health = _digits("Health")
        xp = _digits("Experience")
        speed = _digits("Speed")
        fighting = _digits("Fighting strength")
        off_bonus = _digits("Off bonus")
        def_bonus = _digits("Def bonus")
        # "Points available" shows on the panel ONLY when unspent points exist.
        # When zero, the row disappears — default to 0.
        points = _digits("Points available") or 0
        # Production: not a labeled stat. The `productionPoints` input carries
        # the allocated attribute points; Travian's formula is 6× points per hr.
        prod_points_raw = data.get("productionPoints")
        try:
            prod = int(prod_points_raw) * 6 if prod_points_raw is not None else None
        except (TypeError, ValueError):
            prod = None

        res = HeroAttributes(
            health_pct=health,
            experience=xp,
            speed_fph=speed,
            production_per_hour=prod,
            fighting_strength=fighting,
            off_bonus_pct=float(off_bonus) if off_bonus is not None else None,
            def_bonus_pct=float(def_bonus) if def_bonus is not None else None,
            attribute_points=int(points),
        )
        log.debug("hero.attributes.read", **res.__dict__)
        return res

    async def read_adventures(self) -> AdventureSummary:
        """Count rows in .adventureList (first row is the header)."""
        rows = self.page.locator(self.Selectors.ADVENTURE_ROW)
        total = await rows.count()
        # Subtract the header row if it exists (text starts with "Place").
        header_offset = 0
        if total > 0:
            first = await rows.nth(0).text_content()
            if first and "Place" in first and "Distance" in first:
                header_offset = 1
        count = max(0, total - header_offset)
        log.debug("hero.adventures.read", rows=total, count=count)
        return AdventureSummary(count=count)

    # --- dispatch ---

    # The Explore button is a green rectangle textButtonV2 with visible "Explore".
    _EXPLORE_BUTTON = (
        "button.textButtonV2.buttonFramed.rectangle.withText.green:has-text('Explore')"
    )

    async def _available_explore_buttons(self):
        """Locator for every enabled Explore button currently on screen.

        Legends marks unclickable adventures by ADDING the CSS class `disabled`
        to the button — the HTML `disabled` attribute is not set. Filtering
        by `:not([disabled])` (attribute) lets the disabled ones slip through
        and we ended up clicking a no-op; `:not(.disabled)` is what we want.
        """
        return self.page.locator(f"{self._EXPLORE_BUTTON}:not(.disabled)")

    async def send_first_adventure(self) -> bool:
        """Click Explore on the first available adventure. Assumes we're on
        the adventures tab already (call `open_adventures()` first).

        Returns True if a click was submitted. Logs at INFO when no enabled
        buttons exist even though an adventure *row* is visible — that's a
        hint the row is already in-progress (hero en route).
        """
        buttons = await self._available_explore_buttons()
        count = await buttons.count()
        if count == 0:
            total = await self.page.locator(self._EXPLORE_BUTTON).count()
            log.info(
                "hero.adventure.no_explore_enabled",
                rows_total=total,
                hint="all Explore buttons are .disabled — hero likely already on an adventure",
            )
            return False
        from app.browser.humanize import human_click
        await human_click(self.page, buttons.first)
        log.info("hero.adventure.dispatched", total_visible=count)
        return True

    async def read_home_village_did(self) -> int | None:
        """Parse the 'hero is in X' home link to extract newdid."""
        link = self.page.locator(self.Selectors.HOME_LINK).first
        if await link.count() == 0:
            return None
        href = await link.get_attribute("href") or ""
        m = re.search(r"newdid=(\d+)", href)
        return int(m.group(1)) if m else None

    async def read_inventory(self) -> HeroInventory:
        """Parse equipment slots + bag item count on the Inventory tab.

        Each equipped `.heroItem[data-slot=<slot>]` carries:
          * `quality<N>` / `data-tier="tier<N>"`  → level 1..5
          * rarity class (common | special | epic | unique)
          * `data-id`                              → item instance id
          * inner `<div class="item item<TYPE>">`  → item type id (maps to
            a human name via app/data/hero_items.yaml)
        """
        await self.open_inventory()
        equipment: list[HeroEquipmentSlot] = []
        slots_known = ("helmet", "body", "shoes", "leftHand", "rightHand", "horse")
        for slot in slots_known:
            loc = self.page.locator(
                f'.heroItem.heroItemV2[data-slot="{slot}"]'
            ).first
            if await loc.count() == 0:
                log.debug("hero.inventory.slot.missing", slot=slot)
                continue
            classes = ((await loc.get_attribute("class")) or "").split()
            is_empty = "empty" in classes
            rarity = next(
                (c for c in classes if c in ("common", "special", "epic", "unique")),
                None,
            )
            # `quality<N>` on the outer; fall back to data-tier="tier<N>".
            level = None
            for c in classes:
                if c.startswith("quality") and c[7:].isdigit():
                    n = int(c[7:])
                    if n > 0:
                        level = n
                    break
            if level is None:
                tier = await loc.get_attribute("data-tier") or ""
                m = re.search(r"tier(\d+)", tier)
                if m:
                    level = int(m.group(1))

            instance_id: int | None = None
            did = await loc.get_attribute("data-id") or ""
            if did.isdigit():
                instance_id = int(did)

            item_type_id: int | None = None
            if not is_empty:
                inner_cls = await loc.locator(".item").first.get_attribute(
                    "class", timeout=1000,
                ) if await loc.locator(".item").count() else None
                if inner_cls:
                    m = re.search(r"\bitem(\d+)\b", inner_cls)
                    if m:
                        item_type_id = int(m.group(1))

            equipment.append(HeroEquipmentSlot(
                slot=slot, empty=is_empty, rarity=rarity,
                level=level, item_type_id=item_type_id,
                instance_id=instance_id,
            ))

        # Bag / inventory grid — consumables. Each non-empty cell has an
        # `.item<N>` class on an inner div and a visible integer count.
        bag_items: list[HeroBagItem] = []
        try:
            cells = self.page.locator(self.Selectors.BAG_ITEM_NONEMPTY)
            n = await cells.count()
            for i in range(n):
                cell = cells.nth(i)
                inner_cls = ""
                if await cell.locator(".item").count() > 0:
                    inner_cls = await cell.locator(".item").first.get_attribute(
                        "class", timeout=1000,
                    ) or ""
                m = re.search(r"\bitem(\d+)\b", inner_cls)
                type_id = int(m.group(1)) if m else None
                txt = _norm(await cell.text_content(timeout=1000) or "")
                digits = "".join(c for c in txt if c.isdigit())
                count = int(digits) if digits else 1
                bag_items.append(HeroBagItem(item_type_id=type_id, count=count))
        except Exception as e:  # noqa: BLE001
            log.exception("hero.inventory.bag_parse_error", err=str(e))

        bag_count = len(bag_items)
        log.debug(
            "hero.inventory.read",
            equipped=sum(1 for e in equipment if not e.empty),
            slots=len(equipment), bag=bag_count,
        )
        return HeroInventory(
            equipment=equipment, bag_count=bag_count, bag_items=bag_items,
        )

    async def watch_adventure_bonuses(self) -> tuple[int, int]:
        """Watch the two adventure-related video bonuses ("-25 % duration" and
        "increased danger for more XP") if they're in the `watchReady` state.

        Returns `(ready_count, watched_count)`.

        Observed Legends flow (2026):
          1. Click `Watch video` (purple textButtonV2) on the adventure page.
          2. Dialog `.dialog.videoFeature.videoFeatureVideoDialog` opens
             containing `iframe#videoArea` (cross-origin ad server).
          3. After ~3-4 s the iframe's HTML5 `<video>` mounts with a play
             overlay; user must click the video to start playback.
          4. Ad plays (~15-90 s). A "Skip Ad" link may appear after a few
             seconds — clicking it ends the ad early and still grants the bonus.
          5. On completion the parent `.videoFeatureBonusBox` loses the
             `watchReady` class (server-side grant signal).

        Success signal is the watchReady-count dropping on the host page.
        """
        import asyncio
        import time

        from app.browser.humanize import human_click, sleep_action

        MAX_WAIT_S = 150.0
        # Generous window — the IMA play button can take 15-20s to mount when
        # the ad request lags. 10s was causing us to give up before the
        # trigger appeared at all.
        PLAY_TRIGGER_WAIT_S = 30.0
        POLL_INTERVAL_S = 1.5

        await self.open_adventures()
        ready_sel = ".videoFeatureBonusBox.watchReady"
        initial_ready = await self.page.locator(ready_sel).count()
        log.info("hero.adventure_bonuses.ready", count=initial_ready)
        if initial_ready == 0:
            return 0, 0

        watched = 0
        remaining = initial_ready
        for _ in range(initial_ready):
            btn = self.page.locator(
                f"{ready_sel} button.textButtonV2"
            ).first
            try:
                if await btn.count() == 0 or not await btn.is_visible(timeout=500):
                    break
            except Exception:
                break

            start = time.monotonic()
            log.info("video_bonus.watch.start")
            try:
                await human_click(self.page, btn)
            except Exception as e:  # noqa: BLE001
                log.warning("video_bonus.click_src_failed", err=str(e))
                break

            # Wait for the ad iframe to mount, then try to start playback by
            # clicking into it. The video lives cross-origin under media.oadts.com
            # (and sometimes a nested adscale iframe), so we walk all frames
            # looking for a <video> we can click — HTML5 videos start playing
            # on user-gesture click, which is what the ad server is waiting for.
            await asyncio.sleep(3.5)
            await self._try_play_ad_video(timeout_s=PLAY_TRIGGER_WAIT_S)

            # Poll for grant signal; simultaneously hunt for a "Skip Ad" link
            # inside any frame and click it when visible (it unlocks a few
            # seconds into the ad).
            deadline = start + MAX_WAIT_S
            granted = False
            skip_clicked = False
            while time.monotonic() < deadline:
                now_count = await self.page.locator(ready_sel).count()
                if now_count < remaining:
                    granted = True
                    remaining = now_count
                    break
                if not skip_clicked:
                    skip_clicked = await self._try_click_skip_ad()
                await asyncio.sleep(POLL_INTERVAL_S)

            # Tidy up any leftover modal so the next iteration sees a clean page.
            try:
                closer = self.page.locator(
                    ".dialog.videoFeature .dialogCancelButton, "
                    ".videoFeatureVideoDialog .dialogCancelButton"
                ).first
                if await closer.count() > 0 and await closer.is_visible(timeout=500):
                    await human_click(self.page, closer)
            except Exception:
                pass
            await sleep_action()

            elapsed = time.monotonic() - start
            if granted:
                log.info(
                    "video_bonus.watch.ok",
                    seconds=round(elapsed, 1), skip_used=skip_clicked,
                )
                watched += 1
            else:
                log.warning(
                    "video_bonus.watch.timeout",
                    seconds=round(elapsed, 1), skip_used=skip_clicked,
                )
                break

        log.info(
            "hero.adventure_bonuses.summary",
            ready=initial_ready, watched=watched,
        )
        return initial_ready, watched

    async def _try_play_ad_video(self, timeout_s: float) -> bool:
        """Click whatever starts the ad player. Returns True on a successful click.

        The ad is served in a cross-origin iframe (`#videoArea`, src
        `media.oadts.com/...`) using Google's IMA SDK. The play trigger is
        `div.atg-gima-big-play-button-outer` INSIDE that iframe — not on the
        host page — so we scan `page.frames` for it rather than querying the
        top-level document. IMA overlays routinely fail Playwright's default
        actionability checks even when they're clickable, so we escalate
        through three strategies: normal click → force click → JS
        dispatchEvent. The HTML5-<video>-in-iframe legacy path is kept as a
        final fallback for older skins.
        """
        import asyncio
        import time

        IMA_PLAY = ".atg-gima-big-play-button-outer, .atg-gima-big-play-button"

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            for frame in self.page.frames:
                try:
                    loc = frame.locator(IMA_PLAY).first
                    if await loc.count() == 0:
                        continue
                    # Strategy A: normal click through the frame.
                    try:
                        await loc.click(timeout=1500)
                        log.info(
                            "video_bonus.ad_play.clicked",
                            mode="ima_click", frame_url=frame.url[:80],
                        )
                        return True
                    except Exception as e:  # noqa: BLE001
                        log.debug("video_bonus.ad_play.click_failed", err=str(e))
                    # Strategy B: force click (bypass actionability checks).
                    try:
                        await loc.click(timeout=1500, force=True)
                        log.info(
                            "video_bonus.ad_play.clicked",
                            mode="ima_force", frame_url=frame.url[:80],
                        )
                        return True
                    except Exception as e:  # noqa: BLE001
                        log.debug("video_bonus.ad_play.force_failed", err=str(e))
                    # Strategy C: JS dispatchEvent. Works even when Playwright
                    # refuses to click, because the IMA listener is attached
                    # via addEventListener and accepts synthetic events.
                    try:
                        await loc.evaluate(
                            "el => el.dispatchEvent(new MouseEvent('click', "
                            "{bubbles: true, cancelable: true, view: window}))"
                        )
                        log.info(
                            "video_bonus.ad_play.clicked",
                            mode="ima_js", frame_url=frame.url[:80],
                        )
                        return True
                    except Exception as e:  # noqa: BLE001
                        log.debug("video_bonus.ad_play.js_failed", err=str(e))
                except Exception:
                    continue

                # Legacy skin fallback: HTML5 <video> inside this frame.
                try:
                    video = frame.locator("video").first
                    if await video.count() == 0:
                        continue
                    await video.click(timeout=1500)
                    log.info(
                        "video_bonus.ad_play.clicked",
                        mode="iframe_video", frame_url=frame.url[:80],
                    )
                    return True
                except Exception:
                    continue
            await asyncio.sleep(0.5)

        # Nothing worked — dump what we could see so the next iteration tells
        # us something actionable (was the iframe attached at all? did the
        # button mount inside one of the frames?).
        frame_summary = []
        for frame in self.page.frames:
            try:
                n = await frame.locator(IMA_PLAY).count()
            except Exception:
                n = -1
            frame_summary.append({"url": frame.url[:80], "ima_count": n})
        log.warning(
            "video_bonus.ad_play.no_trigger_found",
            frames=len(self.page.frames), per_frame=frame_summary,
        )
        return False

    async def _try_click_skip_ad(self) -> bool:
        """Look for a visible "Skip Ad" / "Skip" control in any frame and click it.

        Returns True on a successful click. The button is inside the ad-network
        iframe, not on the host page, so we scan all frames.
        """
        import re as _re
        skip_re = _re.compile(r"skip\s*ad", _re.IGNORECASE)
        for frame in self.page.frames:
            try:
                # Text match first (covers "Skip Ad" / "Skip ad"). Fall back to
                # common class names ad SDKs use.
                candidate = frame.get_by_text(skip_re).first
                if await candidate.count() == 0:
                    candidate = frame.locator(
                        "[class*='skip'], [id*='skip'], button:has-text('Skip')"
                    ).first
                if await candidate.count() == 0:
                    continue
                if not await candidate.is_visible(timeout=300):
                    continue
                await candidate.click(timeout=1500)
                log.info("video_bonus.skip_ad.clicked", frame_url=frame.url[:80])
                return True
            except Exception:
                continue
        return False
