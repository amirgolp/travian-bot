"""dorf1.php (resource fields) and dorf2.php (village centre) pages."""
from __future__ import annotations

from dataclasses import dataclass

from playwright.async_api import Page

from app.browser.humanize import read_page, sleep_action
from app.core.logging import get_logger

log = get_logger("page.dorf")


@dataclass
class Resources:
    wood: int
    clay: int
    iron: int
    crop: int
    warehouse_cap: int
    granary_cap: int
    production_per_hour: dict[str, int]


@dataclass
class BuildQueueEntry:
    name: str
    level: int
    finishes_in_seconds: int


@dataclass
class BuildingLevel:
    """One village slot's current state parsed from dorf1/dorf2 DOM."""
    slot: int           # `aid` / `data-aid` — 1..18 on dorf1, 19..40 on dorf2
    gid: int            # 0 = empty slot, otherwise building gid
    level: int          # 0 = empty or under-construction (no level class)


class DorfPage:
    """Shared read methods for dorf1 / dorf2 — they render the same top bar."""

    # Selectors calibrated from samples/legend/dorf1 + dorf2 (Legends rof.x3 on
    # 2026-04-16). The stock bar uses numeric ids #l1..#l4 for wood/clay/iron/crop;
    # capacity sits inside `.warehouse .capacity` / `.granary .capacity` (not the
    # old `#stockBarWarehouse` id). Build queue is an `<li>` inside `.buildingList`.
    class Selectors:
        STOCK_BAR = "#stockBar"
        WOOD = "#l1"
        CLAY = "#l2"
        IRON = "#l3"
        CROP = "#l4"
        WAREHOUSE_CAP = ".warehouse .capacity"
        GRANARY_CAP = ".granary .capacity"
        BUILD_QUEUE_ROWS = ".buildingList li"

    def __init__(self, page: Page):
        self.page = page

    async def goto_dorf1(self) -> None:
        url = self._url("/dorf1.php")
        log.debug("dorf1.goto", url=url)
        await self.page.goto(url, wait_until="domcontentloaded")
        await sleep_action()

    async def goto_dorf2(self) -> None:
        url = self._url("/dorf2.php")
        log.debug("dorf2.goto", url=url)
        await self.page.goto(url, wait_until="domcontentloaded")
        await sleep_action()

    def _url(self, path: str) -> str:
        origin = "/".join(self.page.url.split("/", 3)[:3])  # scheme://host
        return origin + path

    async def _text_int(self, selector: str) -> int:
        try:
            raw = await self.page.locator(selector).first.text_content(timeout=2500)
        except Exception:
            return 0
        return int("".join(c for c in (raw or "") if c.isdigit()) or "0")

    async def read_resources(self) -> Resources:
        s = self.Selectors
        wood = await self._text_int(s.WOOD)
        clay = await self._text_int(s.CLAY)
        iron = await self._text_int(s.IRON)
        crop = await self._text_int(s.CROP)
        warehouse = await self._text_int(s.WAREHOUSE_CAP)
        granary = await self._text_int(s.GRANARY_CAP)
        log.debug(
            "resources.read",
            wood=wood, clay=clay, iron=iron, crop=crop,
            warehouse=warehouse, granary=granary,
        )
        return Resources(wood, clay, iron, crop, warehouse, granary, {})

    async def read_build_queue(self) -> list[BuildQueueEntry]:
        """Read the right-rail in-progress list (`.buildingList li`).

        Each li contains:
          <div.name>    "Clay PitLevel 5" (building name + span.lvl appended)
          <span.lvl>    "Level 5"
          <span.timer>  "0:10:32"
        """
        rows = self.page.locator(self.Selectors.BUILD_QUEUE_ROWS)
        count = await rows.count()
        out: list[BuildQueueEntry] = []
        for i in range(count):
            row = rows.nth(i)
            level_txt = (await row.locator(".lvl").first.text_content() or "").strip()
            level = int("".join(c for c in level_txt if c.isdigit()) or "0")
            # .name contains the building name concatenated with span.lvl text;
            # strip the level suffix to get just the name.
            full_name = (await row.locator(".name").first.text_content() or "").strip()
            name = full_name.replace(level_txt, "").strip()
            timer_txt = (await row.locator(".timer").first.text_content() or "").strip()
            out.append(BuildQueueEntry(name=name, level=level, finishes_in_seconds=_hms_to_s(timer_txt)))
        log.debug("build_queue.read", entries=len(out), rows=count)
        return out


def _hms_to_s(txt: str) -> int:
    parts = [p for p in txt.split(":") if p.isdigit()]
    if len(parts) == 3:
        h, m, s = map(int, parts)
        return h * 3600 + m * 60 + s
    if len(parts) == 2:
        m, s = map(int, parts)
        return m * 60 + s
    return 0


class Dorf1Page(DorfPage):
    """Resource fields (18 outer slots)."""

    class Selectors(DorfPage.Selectors):
        FIELD_LINK = '#rx a[href*="build.php?id="]'  # each field is a clickable link

    async def read_tribe_id(self) -> int | None:
        """Read the `tribe<N>` class off whatever dorf1 element carries it.

        The resource-field map block on dorf1 has classes like
        `resourceField3 tribe3` — only one element in the page carries a
        `tribe<N>` class, so a blanket `[class*=tribe]` selector finds it
        regardless of which skin/layout is active. Returns Travian's internal
        tribe id (1=roman, 2=teuton, 3=gaul, 6=egyptian, 7=hun, 8=spartan,
        9=viking) or None when the class isn't present yet.
        """
        try:
            cls = await self.page.locator('[class*="tribe"]').first.get_attribute(
                "class", timeout=1500,
            ) or ""
        except Exception:
            return None
        import re as _re
        # Match only exact `tribeN` tokens; skip unrelated strings like
        # `contribution` that might also contain the substring.
        m = _re.search(r"\btribe(\d+)\b", cls)
        return int(m.group(1)) if m else None

    async def visit_field(self, slot: int) -> None:
        # dorf1 slot ids are 1..18 mapped 1:1 to ?id= in the URL.
        origin = "/".join(self.page.url.split("/", 3)[:3])
        await self.page.goto(f"{origin}/build.php?id={slot}", wait_until="domcontentloaded")
        await read_page(self.page, words=40)

    async def read_field_levels(self) -> list[BuildingLevel]:
        """Parse the 18 resource-field slots on dorf1.

        Each field is an `<a class="resourceField gidN buildingSlotM levelL">`
        carrying the building gid (`gidN`), slot id (`buildingSlotM`, 1..18)
        and current level (`levelL`). Slots with no building at all simply
        don't exist — dorf1 slots are fixed resource fields. A level of 0
        means the field is yet to be upgraded.
        """
        fields = await self.page.locator("a.resourceField").evaluate_all(
            "els => els.map(e => e.getAttribute('class') || '')",
        )
        out: list[BuildingLevel] = []
        for cls in fields:
            import re as _re
            slot_m = _re.search(r"buildingSlot(\d+)", cls)
            gid_m = _re.search(r"\bgid(\d+)\b", cls)
            lvl_m = _re.search(r"\blevel(\d+)\b", cls)
            if not slot_m or not gid_m:
                continue
            out.append(BuildingLevel(
                slot=int(slot_m.group(1)),
                gid=int(gid_m.group(1)),
                level=int(lvl_m.group(1)) if lvl_m else 0,
            ))
        log.debug("dorf1.fields.read", count=len(out))
        return out


class Dorf2Page(DorfPage):
    """Village centre (20 inner slots, 19..38/40)."""

    async def visit_slot(self, slot: int) -> None:
        origin = "/".join(self.page.url.split("/", 3)[:3])
        await self.page.goto(f"{origin}/build.php?id={slot}", wait_until="domcontentloaded")
        await read_page(self.page, words=40)

    async def read_slot_levels(self) -> list[BuildingLevel]:
        """Parse village-centre slots on dorf2.

        `.buildingSlot` carries `aid<N>` (slot id 19..40) and `g<GID>` (gid,
        0 = empty slot); the level text lives on a sibling `.level.aid<N>`
        div. Slots with `g0` report gid=0 and level=0 so the caller can
        null out BuildingSlot rows for freed slots.
        """
        data = await self.page.evaluate(
            """() => {
              const out = [];
              document.querySelectorAll('.buildingSlot').forEach((el) => {
                const cls = el.className || '';
                const aid = (cls.match(/\\baid(\\d+)\\b/) || [])[1];
                const gid = (cls.match(/\\bg(\\d+)\\b/) || [])[1];
                if (!aid) return;
                // Level text sits on a sibling `.level.aidN` element.
                let level = 0;
                const lvl = document.querySelector(`.level.aid${aid}`);
                if (lvl) {
                  const txt = (lvl.textContent || '').trim();
                  const n = parseInt(txt, 10);
                  if (!isNaN(n)) level = n;
                }
                out.push({ slot: parseInt(aid, 10), gid: parseInt(gid || '0', 10), level });
              });
              return out;
            }"""
        )
        out: list[BuildingLevel] = [
            BuildingLevel(slot=row["slot"], gid=row["gid"], level=row["level"])
            for row in (data or [])
        ]
        log.debug("dorf2.slots.read", count=len(out))
        return out
