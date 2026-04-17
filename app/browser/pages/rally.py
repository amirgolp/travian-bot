"""Rally Point pages — raid sending and the troops overview.

Key URLs:
  /build.php?gid=16              -> rally point landing
  /build.php?gid=16&tt=1         -> "Overview" tab: own troops + all movements
  /build.php?gid=16&tt=2         -> send troops

We intentionally do NOT drive `/build.php?gid=16&tt=99` (the in-game farmlist
tab): it is Gold-Club-only. Farm lists are maintained by this bot directly and
dispatched one slot at a time via the send-troops form above.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from playwright.async_api import Page

from app.browser.humanize import human_click, human_type, read_page
from app.core.logging import get_logger

log = get_logger("page.rally")


# ---- helpers shared with other pages but worth inlining here ----

_BIDI = "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
_BIDI_TRANS = str.maketrans("", "", _BIDI)


def _normalize(s: str | None) -> str:
    if not s:
        return ""
    return s.translate(_BIDI_TRANS).replace("\u2212", "-").replace("\u00a0", " ")


_COORD_RE = re.compile(r"\(?\s*(-?\d+)\s*[|,]\s*(-?\d+)\s*\)?")
_HMS_RE = re.compile(r"(\d+):(\d{1,2}):(\d{1,2})")


def _extract_coords(text: str) -> tuple[int | None, int | None]:
    m = _COORD_RE.search(_normalize(text))
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def _parse_hms(text: str) -> int:
    m = _HMS_RE.search(_normalize(text))
    if not m:
        return 0
    h, mm, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return h * 3600 + mm * 60 + s


@dataclass
class RaidTarget:
    x: int
    y: int
    troops: dict[str, int]     # { "t4": 12, "t5": 3 }


@dataclass
class Movement:
    """One row on the rally-point overview (incoming or outgoing).

    `direction` is one of: "out_raid", "out_attack", "out_reinforce",
    "in_return", "in_attack", "in_reinforce", "unknown".
    `is_attack` is a convenience: true if direction starts with "in_attack"
    or a hostile outgoing — the village-overview UI uses this to light up
    the "under attack" warning.
    """
    direction: str
    headline: str
    target_x: int | None
    target_y: int | None
    troops: dict[str, int] = field(default_factory=dict)
    arrival_in_seconds: int = 0
    is_attack: bool = False


@dataclass
class RallyOverview:
    own_troops: dict[str, int]
    consumption_per_hour: int
    movements_in: list[Movement] = field(default_factory=list)
    movements_out: list[Movement] = field(default_factory=list)

    @property
    def under_attack(self) -> bool:
        return any(m.is_attack for m in self.movements_in)


# --- direction classifier (class modifiers on .troop_details) ---
# Observed in samples/legend/rally-point-1:
#   no modifier        -> own troops home
#   outRaid            -> our outgoing raid
#   inReturn           -> our returning squad (raid or adventure)
# Inferred from the game (not in sample, but Travian is consistent):
#   outAttack, outReinforce, inAttack, inReinforce
async def _read_consumption(tbl) -> int:
    """Pull the crop/hour figure from the Own-troops table's last row."""
    trs = tbl.locator("tr")
    nrows = await trs.count()
    for r in range(nrows):
        cells = trs.nth(r).locator("th, td")
        ncells = await cells.count()
        if ncells == 0:
            continue
        label = _normalize(await cells.nth(0).text_content() or "").strip()
        if label == "Consumption" and ncells >= 2:
            val = _normalize(await cells.nth(1).text_content() or "")
            digits = "".join(c for c in val if c.isdigit())
            return int(digits) if digits else 0
    return 0


def _classify_troop_table(classes: list[str]) -> str:
    cls = set(classes)
    if "outRaid" in cls or "outRaidTypeRaid" in cls:
        return "out_raid"
    if "outAttack" in cls:
        return "out_attack"
    if "outReinforce" in cls or "outSupply" in cls:
        return "out_reinforce"
    # Hero going out solo — adventure dispatch or moving between villages.
    # Not an attack, so we bucket it in movements_out without lighting up
    # the under-attack flag.
    if "outHero" in cls:
        return "out_hero"
    if "inReturn" in cls:
        return "in_return"
    if "inAttack" in cls or "inRaid" in cls:
        return "in_attack"
    if "inReinforce" in cls or "inSupply" in cls:
        return "in_reinforce"
    if "inHero" in cls:
        return "in_hero"
    return "unknown"


class RallyPointPage:
    class Selectors:
        SEND_TROOPS_TAB_LINK = 'a[href*="tt=2"]'

        # Send troops (tt=2). The send form is two steps: fill + click #ok,
        # Travian re-renders a review page with a confirm button, then that
        # POST actually dispatches. The radio selecting attack mode is named
        # `eventType` (value 5=Reinforce checked by default, 3=Normal, 4=Raid)
        # — NOT `c` like older Travian. Getting this wrong silently "reinforces"
        # the raid target, which Travian rejects without an error the bot can
        # see (the Send button simply stays on the page).
        COORD_X_INPUT = 'input[name="x"]'
        COORD_Y_INPUT = 'input[name="y"]'
        MODE_RAID_RADIO = 'input[name="eventType"][value="4"]'
        TROOP_INPUT_TEMPLATE = 'input[name="troop[t{n}]"]'
        OK_BTN = 'button#ok, button[name="ok"][value="ok"]'
        # The review page mounts a form with id #troopSendForm (the initial
        # enter form has no id) — a clean discriminator to wait on.
        REVIEW_FORM = 'form#troopSendForm'
        # Confirm is #confirmSendTroops specifically; the review page ALSO has
        # a #back button with class textButtonV1 that appears first in DOM, so
        # a selector like `button[type=submit].textButtonV1` would match Back
        # and silently rewind us to the enter form.
        CONFIRM_BTN = 'button#confirmSendTroops'
        ERROR_MSG = '.error, .errorMessage, div#build_value .error'

    def __init__(self, page: Page):
        self.page = page

    def _origin(self) -> str:
        return "/".join(self.page.url.split("/", 3)[:3])

    async def open_overview(self) -> None:
        await self.page.goto(f"{self._origin()}/build.php?gid=16", wait_until="domcontentloaded")
        await read_page(self.page, words=40)

    async def open_overview_tab(self) -> None:
        """The "Overview" tab (tt=1) — shows own troops + all movements.

        `gid=16` alone redirects to the rally-point slot of the active village,
        so we don't need to track each village's slot id.
        """
        url = f"{self._origin()}/build.php?gid=16&tt=1"
        log.debug("rally.open_overview_tab", url=url)
        await self.page.goto(url, wait_until="domcontentloaded")
        await read_page(self.page, words=40)

    async def read_overview(self) -> RallyOverview:
        """Parse every `.troop_details` table on the current page.

        Assumes we're already on `build.php?gid=16&tt=1`. Call
        `open_overview_tab()` first if not.
        """
        tables = self.page.locator(".troop_details")
        count = await tables.count()
        log.debug("rally.overview.tables", count=count)

        own: dict[str, int] = {}
        consumption = 0
        movs_in: list[Movement] = []
        movs_out: list[Movement] = []

        for i in range(count):
            tbl = tables.nth(i)
            classes = ((await tbl.get_attribute("class")) or "").split()
            direction = _classify_troop_table(classes)
            mov = await self._parse_troop_table(tbl, direction)

            # The Own-troops table has class == exactly ["troop_details"] — no
            # in*/out* modifier. Anything else with "unknown" direction is an
            # unrecognised movement kind (log + skip).
            is_own = direction == "unknown" and set(classes) == {"troop_details"}
            if is_own:
                own = mov.troops
                consumption = await _read_consumption(tbl)
            elif direction.startswith("out_"):
                movs_out.append(mov)
            elif direction.startswith("in_"):
                movs_in.append(mov)
            else:
                log.warning("rally.troop_table.unclassified", classes=classes)

        log.info(
            "rally.overview",
            own_total=sum(own.values()),
            consumption=consumption,
            movements_in=len(movs_in),
            movements_out=len(movs_out),
        )
        return RallyOverview(
            own_troops=own,
            consumption_per_hour=consumption,
            movements_in=movs_in,
            movements_out=movs_out,
        )

    async def _parse_troop_table(self, tbl, direction: str) -> Movement:
        """Parse one .troop_details table → Movement. Expected rows:
          [0] village-name cell + headline ("Raid ...", "Return from ...", "Own troops")
          [1] coords (x|y) of home/target + 11 blank cells
          [2] "Troops" label + 11 integer cells
          [3] "Arrival in H:MM:SS ..." or "Consumption N per hour"
        """
        trs = tbl.locator("tr")
        nrows = await trs.count()

        # Headline — 2nd cell of row 0.
        headline = ""
        target_x = target_y = None
        if nrows >= 1:
            cells0 = trs.nth(0).locator("th, td")
            if await cells0.count() >= 2:
                headline = _normalize(await cells0.nth(1).text_content() or "")
                target_x, target_y = _extract_coords(headline)

        # Troops — row labeled "Troops"; positional t1..t11.
        troops: dict[str, int] = {}
        for r in range(nrows):
            cells = trs.nth(r).locator("th, td")
            ncells = await cells.count()
            if ncells == 0:
                continue
            label = _normalize(await cells.nth(0).text_content() or "").strip()
            if label == "Troops" and ncells >= 12:
                for i in range(1, min(ncells, 12)):
                    val_raw = _normalize(await cells.nth(i).text_content() or "0")
                    digits = "".join(c for c in val_raw if c.isdigit() or c == "-")
                    n = int(digits) if digits not in ("", "-") else 0
                    if n:
                        troops[f"t{i}"] = n
                break

        # Arrival — last row, "Arrival in H:MM:SS".
        arrival_s = 0
        if nrows >= 1:
            last = trs.nth(nrows - 1)
            arrival_s = _parse_hms(await last.text_content() or "")

        is_attack = direction == "in_attack"
        return Movement(
            direction=direction,
            headline=headline,
            target_x=target_x,
            target_y=target_y,
            troops=troops,
            arrival_in_seconds=arrival_s,
            is_attack=is_attack,
        )

    async def open_send_troops(self) -> None:
        await self.page.goto(f"{self._origin()}/build.php?gid=16&tt=2", wait_until="domcontentloaded")
        await read_page(self.page, words=30)

    async def send_raid(self, target: RaidTarget) -> bool:
        """Send a single raid via the "send troops" dialog.

        Two-step flow in Legends: fill fields → click `#ok` → Travian renders a
        review page → click the confirm button. We verify each transition
        (the raid radio is actually selected, the troop inputs actually hold
        the values we typed, no error banner appears, and the final URL leaves
        the enter-form state) because the form silently eats invalid submits.
        """
        await self.open_send_troops()
        s = self.Selectors

        # Wait for the form to be ready before typing — otherwise the first
        # keystroke can land before Travian's JS attaches its input filter and
        # the field stays empty, which the server then rejects with "0 troops".
        await self.page.locator(s.COORD_X_INPUT).first.wait_for(timeout=8000)

        await human_type(self.page, self.page.locator(s.COORD_X_INPUT).first, str(target.x))
        await human_type(self.page, self.page.locator(s.COORD_Y_INPUT).first, str(target.y))

        for tname, qty in target.troops.items():
            n = int("".join(c for c in tname if c.isdigit()))
            inp = self.page.locator(s.TROOP_INPUT_TEMPLATE.format(n=n)).first
            # Travian disables a troop input when 0 of that type are home.
            # Clicking a disabled input blocks for the full 30s Playwright
            # timeout — fail fast so the slot is declined in milliseconds.
            if not await inp.is_enabled():
                log.warning(
                    "rally.send_raid.input_disabled",
                    key=tname, want=qty,
                )
                return False
            await human_type(self.page, inp, str(qty))

        # Select Raid (eventType=4). The radio defaults to Reinforce (value=5),
        # which Travian refuses for hostile targets — treat a missing radio as
        # a fatal form mismatch rather than pushing through.
        radio = self.page.locator(s.MODE_RAID_RADIO).first
        if not await radio.count():
            log.warning("rally.send_raid.no_raid_radio",
                        hint="eventType radio layout changed?")
            return False
        try:
            await radio.check(timeout=3000)
        except Exception:
            await human_click(self.page, radio)

        # Sanity-check what's actually in the inputs before we submit.
        for tname, qty in target.troops.items():
            n = int("".join(c for c in tname if c.isdigit()))
            val = await self.page.locator(
                s.TROOP_INPUT_TEMPLATE.format(n=n),
            ).first.input_value()
            filled = int("".join(c for c in val if c.isdigit()) or 0)
            if filled != qty:
                log.warning(
                    "rally.send_raid.input_mismatch",
                    key=tname, want=qty, got=filled,
                )
                return False

        await human_click(self.page, self.page.locator(s.OK_BTN).first)

        # Wait for the review form to mount. The enter form and the review
        # form share URL (`?gid=16&tt=2`), so the only reliable transition
        # signal is the #troopSendForm element, which exists only on review.
        # If it never appears, the enter form refused our input — bail.
        try:
            await self.page.locator(s.REVIEW_FORM).wait_for(timeout=8000)
        except Exception:
            err = self.page.locator(s.ERROR_MSG)
            if await err.count():
                txt = (await err.first.text_content() or "").strip()
                log.warning("rally.send_raid.form_error",
                            msg=txt or "unknown")
            else:
                log.warning("rally.send_raid.no_review_form",
                            url=self.page.url)
            return False

        confirm = self.page.locator(s.CONFIRM_BTN).first
        if not await confirm.count():
            log.warning("rally.send_raid.no_confirm_button", url=self.page.url)
            return False
        await human_click(self.page, confirm)

        # The confirm button's onclick triggers an in-place form submit and
        # the page then navigates away. Wait for the review form to detach
        # — if it's still present after the grace period, the submit didn't
        # take (e.g. checksum mismatch, target invalid).
        try:
            await self.page.locator(s.REVIEW_FORM).wait_for(
                state="detached", timeout=8000,
            )
        except Exception:
            log.warning("rally.send_raid.confirm_stuck", url=self.page.url)
            return False
        return True
