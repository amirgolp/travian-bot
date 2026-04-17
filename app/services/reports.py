"""Report ingestion and parsing helpers.

The controller owns cadence; this module owns the operations:
  - list_recent_reports     — pull the list page, return summaries
  - parse_report_detail     — fetch one report's HTML, extract bounty/target
  - upsert_report_for_tile  — persist + update aggregates on the MapTile
  - bump_slot_loss_counters — walk recent raid outcomes, update slot counters

Parsing is intentionally tolerant: if a selector misses we log a warning and
leave the field as None, rather than crashing the controller. The user can
improve selectors by dropping an HTML sample into `samples/reports/` and we
refine here.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.pages.reports import ReportSummary, ReportsPage
from app.browser.session import BrowserSession
from app.core.logging import get_logger
from app.models.farmlist import Farmlist, FarmlistSlot
from app.models.map_tile import MapTile, TileType
from app.models.report import Report, ReportType
from app.models.village import Village

log = get_logger("service.reports")


@dataclass
class ParsedReport:
    target_x: int | None
    target_y: int | None
    bounty_wood: int = 0
    bounty_clay: int = 0
    bounty_iron: int = 0
    bounty_crop: int = 0
    capacity_used_pct: int | None = None
    # Raw "subject" line from the row. Text-before-verb is the attacker's
    # village name; we don't parse it here — `ingest_list` resolves the name
    # to a Village row by prefix-matching the account's known villages, which
    # handles multi-word names + localized action verbs robustly.
    subject: str | None = None


@dataclass
class ParsedReportDetail:
    """What the detail-page scrape yields beyond the list-row summary."""
    bounty_wood: int = 0
    bounty_clay: int = 0
    bounty_iron: int = 0
    bounty_crop: int = 0
    capacity_used: int | None = None   # carried amount
    capacity_total: int | None = None  # carry cap
    capacity_used_pct: int | None = None
    # Positional troop counts (index 0 = t1 … index 10 = t11); missing columns
    # stay as None so we can tell "no data" from "zero sent".
    attacker_sent: list[int] | None = None
    attacker_losses: list[int] | None = None
    defender_losses: list[int] | None = None


# ---- HTML parsing helpers (tolerant) ----

# Travian wraps coords in LRM/RLM/LRE/PDF bidi marks for RTL locales; also uses
# the typographic minus sign U+2212. Normalize both so the simple regex hits.
_BIDI_CHARS = "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
_BIDI_TRANS = str.maketrans("", "", _BIDI_CHARS)


def _normalize(s: str) -> str:
    return s.translate(_BIDI_TRANS).replace("\u2212", "-").replace("\u00a0", " ")


_INT_RE = re.compile(r"-?\d[\d,.]*")
# `(x|y)` is the canonical form; accept both `,` and `|` as separator.
_COORD_RE = re.compile(r"\(?\s*(-?\d+)\s*[,|\u2758]\s*(-?\d+)\s*\)?")


def _to_int(s: str | None) -> int:
    if not s:
        return 0
    m = _INT_RE.search(_normalize(s))
    if not m:
        return 0
    return int(m.group(0).replace(",", "").replace(".", ""))


def parse_report_html(html: str) -> ParsedReport:
    """Pull target coords + resource bounty out of a single report's HTML.

    Designed to tolerate minor skin differences. The user should drop real
    report HTML into `samples/reports/` so the selectors can be hardened.
    """
    soup = BeautifulSoup(html, "lxml")

    # Target coords: first look at the subject link in list rows
    # (text looks like: "00 raids Unoccupied oasis (-84|9)"); fall back to a
    # coord-y widget on detail pages.
    target_x = target_y = None
    subject_text: str | None = None
    text_probes: list[str] = []
    subj = soup.select_one('a[href^="?id="][href*="&s=1"]')
    if subj:
        subject_text = _normalize(subj.get_text(" ", strip=True))
        text_probes.append(subject_text)
    coords_el = soup.find(class_=re.compile(r"coordinates|targetCoords|attacked"))
    if coords_el:
        text_probes.append(coords_el.get_text(" ", strip=True))
    for probe in text_probes:
        m = _COORD_RE.search(_normalize(probe))
        if m:
            target_x, target_y = int(m.group(1)), int(m.group(2))
            break

    # Bounty: find the resource row in the infobox.
    wood = clay = iron = crop = 0
    for res_row in soup.select(".infos .resource, .booty .resource, #attacker .resources span"):
        classes = " ".join(res_row.get("class") or [])
        val = _to_int(res_row.get_text())
        if "r1" in classes:
            wood = val
        elif "r2" in classes:
            clay = val
        elif "r3" in classes:
            iron = val
        elif "r4" in classes:
            crop = val

    # Capacity used: usually "1234/1500" somewhere in the booty block.
    pct = None
    cap_txt = soup.find(string=re.compile(r"/\s*\d"))
    if cap_txt:
        m = re.search(r"(\d[\d,.]*)\s*/\s*(\d[\d,.]*)", cap_txt)
        if m:
            used = _to_int(m.group(1))
            total = _to_int(m.group(2))
            if total > 0:
                pct = int(used * 100 / total)

    return ParsedReport(
        target_x=target_x, target_y=target_y,
        bounty_wood=wood, bounty_clay=clay, bounty_iron=iron, bounty_crop=crop,
        capacity_used_pct=pct,
        subject=subject_text,
    )


def parse_report_detail(html: str) -> ParsedReportDetail:
    """Parse the detail page for bounty + carry + losses.

    Legends markup (calibrated from samples/legend/reports-detail-*.html):
      - `.additionalInformation .resourceWrapper:first-of-type .resources`
        carries 4 wood/clay/iron/crop spans in order.
      - `.additionalInformation .carry` has `N/M` (amount / total capacity).
      - `.role.attacker table` and `.role.defender table`:
          row 0 = troop icons, row 1 = troops sent, row 2 = troops lost.
      - A second `.resourceWrapper` block shows hero "additional resources"
        from killing animals on adventures — we ignore it, the first wrapper
        is the actual raid bounty.
    """
    soup = BeautifulSoup(html, "lxml")
    out = ParsedReportDetail()

    info = soup.select_one(".additionalInformation")
    if info:
        wrap = info.select_one(".resourceWrapper")
        if wrap:
            vals = [
                _to_int(r.get_text()) for r in wrap.select(".resources, .resource")
            ]
            # Sometimes there's a trailing 5th "total" or label; take the first 4.
            if len(vals) >= 4:
                out.bounty_wood, out.bounty_clay, out.bounty_iron, out.bounty_crop = vals[:4]
        carry = info.select_one(".carry")
        if carry:
            m = re.search(r"(\d[\d,.]*)\s*/\s*(\d[\d,.]*)", _normalize(carry.get_text()))
            if m:
                out.capacity_used = _to_int(m.group(1))
                out.capacity_total = _to_int(m.group(2))
                if out.capacity_total > 0:
                    out.capacity_used_pct = int(out.capacity_used * 100 / out.capacity_total)

    for role, attr in (("attacker", "attacker"), ("defender", "defender")):
        side = soup.select_one(f".role.{role}")
        if not side:
            continue
        tbl = side.find("table")
        if not tbl:
            continue
        trs = tbl.find_all("tr")
        # Row 0 is header icons (no numbers), row 1 = sent, row 2 = lost.
        def _row_ints(tr) -> list[int]:
            return [
                _to_int(td.get_text())
                for td in tr.find_all(["td", "th"])
                if td.get_text(strip=True).lstrip("-").isdigit()
            ]
        if attr == "attacker":
            if len(trs) >= 2:
                out.attacker_sent = _row_ints(trs[1])
            if len(trs) >= 3:
                out.attacker_losses = _row_ints(trs[2])
        else:
            if len(trs) >= 3:
                out.defender_losses = _row_ints(trs[2])

    return out


def _resolve_source_village(
    subject: str | None, account_villages: list[Village]
) -> int | None:
    """Pick the `Village` whose name is the longest prefix of the subject.

    Row subjects look like `"00 raids Unoccupied oasis (-84|9)"` — the text
    up to (but not including) the action verb is the attacker village's
    name. Verbs are localized ("raids" / "attacks" / "razziert" / ...), so
    instead of splitting on the verb we try every village name we own as a
    prefix and return the longest match. This is robust to multi-word
    village names and languages we haven't encoded.
    """
    if not subject:
        return None
    prefix = subject.strip()
    best: tuple[int, int] | None = None  # (len, village_id)
    for v in account_villages:
        if not v.name:
            continue
        if prefix.startswith(v.name) and (
            len(prefix) == len(v.name) or not prefix[len(v.name)].isalnum()
        ):
            if best is None or len(v.name) > best[0]:
                best = (len(v.name), v.id)
    return best[1] if best else None


# ---- Persistence ----

async def _find_tile(
    db: AsyncSession, server_code: str, x: int, y: int
) -> MapTile | None:
    return (
        await db.execute(
            select(MapTile).where(
                MapTile.server_code == server_code, MapTile.x == x, MapTile.y == y,
            )
        )
    ).scalar_one_or_none()


async def _get_or_create_tile(
    db: AsyncSession, server_code: str, x: int, y: int, type_hint: TileType
) -> MapTile:
    tile = await _find_tile(db, server_code, x, y)
    if tile is None:
        tile = MapTile(server_code=server_code, x=x, y=y, type=type_hint)
        db.add(tile)
        await db.flush()
        log.debug("tile.create", server=server_code, x=x, y=y, type=type_hint.value)
    return tile


async def ingest_list(
    db: AsyncSession,
    session: BrowserSession,
    server_code: str,
    account_id: int,
    limit: int = 40,
) -> int:
    """Scrape the report list page and persist new rows (no deep parse yet)."""
    page = ReportsPage(session.page)
    await page.open()
    summaries: list[ReportSummary] = await page.list_recent(limit=limit)
    log.debug("reports.list", count=len(summaries))

    # Load this account's villages once; used to resolve the attacker name
    # in each row to a Village.id.
    account_villages = (
        await db.execute(select(Village).where(Village.account_id == account_id))
    ).scalars().all()

    stored = 0
    for s in summaries:
        existing = (
            await db.execute(
                select(Report).where(
                    Report.account_id == account_id,
                    Report.travian_report_id == s.travian_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue

        rtype = {
            "win": ReportType.RAID_WIN,
            "loss": ReportType.RAID_LOSS,
            "empty": ReportType.RAID_EMPTY,
            "defense": ReportType.DEFENSE,
            "scout": ReportType.SCOUT,
        }.get(s.outcome, ReportType.OTHER)

        # Parse the row first for coords + subject.
        parsed = parse_report_html(s.raw_html)
        tile = None
        if parsed.target_x is not None and parsed.target_y is not None:
            type_hint = TileType.UNKNOWN
            tile = await _get_or_create_tile(
                db, server_code, parsed.target_x, parsed.target_y, type_hint
            )

        # Deep-read the detail page for raid outcomes so we capture bounty,
        # carry capacity, and losses. Skip non-raid noise (adventures, chat,
        # world events) to keep the extra page loads targeted.
        detail: ParsedReportDetail | None = None
        if (
            s.detail_href
            and rtype in (ReportType.RAID_WIN, ReportType.RAID_LOSS, ReportType.RAID_EMPTY)
        ):
            try:
                detail_html = await page.read_detail(s.detail_href)
                detail = parse_report_detail(detail_html)
                # Promote detail numbers over the list-row fallback.
                parsed.bounty_wood = detail.bounty_wood or parsed.bounty_wood
                parsed.bounty_clay = detail.bounty_clay or parsed.bounty_clay
                parsed.bounty_iron = detail.bounty_iron or parsed.bounty_iron
                parsed.bounty_crop = detail.bounty_crop or parsed.bounty_crop
                if detail.capacity_used_pct is not None:
                    parsed.capacity_used_pct = detail.capacity_used_pct
                # If the outcome was "win" but bounty is zero AND capacity was
                # non-zero, the oasis was empty on arrival → reclassify so the
                # UI colours / slot-loss counters match reality.
                bounty_sum = (
                    detail.bounty_wood + detail.bounty_clay
                    + detail.bounty_iron + detail.bounty_crop
                )
                if rtype == ReportType.RAID_WIN and bounty_sum == 0 and (
                    detail.capacity_total or 0
                ) > 0:
                    rtype = ReportType.RAID_EMPTY
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "report.detail.parse_failed",
                    report_id=s.travian_id, err=str(e),
                )

        bounty_total = (
            parsed.bounty_wood + parsed.bounty_clay + parsed.bounty_iron + parsed.bounty_crop
        )

        source_vid = _resolve_source_village(parsed.subject, list(account_villages))
        if source_vid is None and rtype in (
            ReportType.RAID_WIN, ReportType.RAID_LOSS, ReportType.RAID_EMPTY,
        ):
            # We can still store the report and update tile aggregates, but
            # without a source village we can't bump the right farmlist slot's
            # loss counter. Surface it so the user can see when subject parsing
            # drifts (e.g. village got renamed in-game).
            log.warning(
                "report.source_village.unresolved",
                report_id=s.travian_id, subject=parsed.subject,
                known_villages=[vv.name for vv in account_villages],
            )

        parsed_blob: dict = {}
        if detail is not None:
            parsed_blob = {
                "capacity_used": detail.capacity_used,
                "capacity_total": detail.capacity_total,
                "attacker_sent": detail.attacker_sent,
                "attacker_losses": detail.attacker_losses,
                "defender_losses": detail.defender_losses,
            }

        rec = Report(
            account_id=account_id,
            tile_id=tile.id if tile else None,
            source_village_id=source_vid,
            travian_report_id=s.travian_id,
            type=rtype,
            target_x=parsed.target_x,
            target_y=parsed.target_y,
            bounty_wood=parsed.bounty_wood,
            bounty_clay=parsed.bounty_clay,
            bounty_iron=parsed.bounty_iron,
            bounty_crop=parsed.bounty_crop,
            bounty_total=bounty_total,
            capacity_used_pct=parsed.capacity_used_pct,
            raw_html=s.raw_html,
            parsed_json=json.dumps(parsed_blob, sort_keys=True) if parsed_blob else None,
        )
        db.add(rec)

        if tile is not None:
            _apply_to_tile(tile, rtype, bounty_total, parsed.capacity_used_pct)

        log.info(
            "report.ingest",
            report_id=s.travian_id, outcome=s.outcome,
            x=parsed.target_x, y=parsed.target_y,
            bounty=bounty_total, tile_id=tile.id if tile else None,
            source_village_id=source_vid,
        )
        stored += 1

    # After ingesting, update slot loss counters for matched tiles.
    await bump_slot_counters_from_recent(db, account_id)
    return stored


def _apply_to_tile(
    tile: MapTile, rtype: ReportType, bounty: int, capacity_pct: int | None,
) -> None:
    """Mutates the MapTile aggregates in place.

    Distinct fields for lifetime vs. latest: `win/loss/empty_count` accumulate,
    while `last_raid_outcome` / `last_raid_capacity_pct` reflect only the most
    recent raid so the UI can colour a row by "what just happened" rather than
    a wash of historical totals.
    """
    tile.raid_count += 1
    last_outcome: str | None = None
    if rtype == ReportType.RAID_WIN:
        tile.win_count += 1
        last_outcome = "win"
    elif rtype == ReportType.RAID_LOSS:
        tile.loss_count += 1
        last_outcome = "loss"
    elif rtype == ReportType.RAID_EMPTY:
        tile.empty_count += 1
        last_outcome = "empty"
    tile.total_bounty += max(0, bounty)
    tile.last_raid_at = datetime.now(tz=timezone.utc)
    if last_outcome is not None:
        tile.last_raid_outcome = last_outcome
        tile.last_raid_capacity_pct = capacity_pct


async def bump_slot_counters_from_recent(db: AsyncSession, account_id: int) -> int:
    """Apply recent report outcomes to their originating farmlist slot.

    A slot is matched on the (source_village_id, tile_id) pair — both sides
    of the raid must agree. This avoids cross-village pollution when two
    villages have the same tile in their farmlists.

    Reports without a resolved `source_village_id` are skipped (we can't
    tell which village they came from). Counter update:
      win + bounty > 0   → reset consecutive_losses to 0
      loss / empty / def → +1 to consecutive_losses
    """
    reports = (
        await db.execute(
            select(Report)
            .where(
                Report.account_id == account_id,
                Report.tile_id.is_not(None),
                Report.source_village_id.is_not(None),
            )
            .order_by(Report.id.desc())
            .limit(200)
        )
    ).scalars().all()

    touched = 0
    for r in reports:
        slots = (
            await db.execute(
                select(FarmlistSlot)
                .join(Farmlist, Farmlist.id == FarmlistSlot.farmlist_id)
                .where(
                    FarmlistSlot.tile_id == r.tile_id,
                    Farmlist.village_id == r.source_village_id,
                )
            )
        ).scalars().all()
        for s in slots:
            if r.type == ReportType.RAID_WIN and r.bounty_total > 0:
                if s.consecutive_losses != 0:
                    log.debug("slot.reset_losses", slot_id=s.id)
                s.consecutive_losses = 0
            elif r.type in (ReportType.RAID_LOSS, ReportType.RAID_EMPTY, ReportType.DEFENSE):
                s.consecutive_losses = (s.consecutive_losses or 0) + 1
                log.debug(
                    "slot.bump_losses",
                    slot_id=s.id, new_losses=s.consecutive_losses, report=r.travian_report_id,
                )
            touched += 1
    return touched
