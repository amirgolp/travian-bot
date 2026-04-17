"""Scan the in-game map for oases and Natar tiles.

Endpoint: POST /api/v1/map/position (calibrated from samples/legend/map-ajax.json).

Request body: {"data": {"x": cx, "y": cy, "zoomLevel": 2}}
Response:     {"tiles": [{position:{x,y}, did?, uid?, title, text}, ...]}

The server returns a fixed window (~11×9 tiles at zoomLevel 2) centered on
(cx, cy) — we iterate centers spaced STEP apart in map_scan_ctrl to cover a
square of radius SCAN_RADIUS around each of our villages.

Tile classification keys (from the raw JSON):
  * Unoccupied oasis    : `did == -1` + title contains `{k.fo}` ("free oasis").
                          Bonus resources appear in `text` as `{a:rN} {a.rN} NN%`.
  * Occupied oasis      : `did == -1` + title contains `{k.bt}`. Has `uid`/`aid`
                          and owner fields (`{k.spieler}`, `{k.allianz}`, `{k.volk}`)
                          — same TileType.OASIS but with owner populated.
  * Natar / ghost       : `uid` ≤ 10 (canonical Natar uid) OR title/text mentions
                          "Natar"/"Natarian". These are raidable villages.
  * Occupied village    : `uid > 10` + `did > 0` + title `{k.dt} <name>`.
  * Landscape (skip)    : title is "Forest"/"Sea"/"Lake"/"{k.vt} ..."/"{k.as} ...".

Observer hints on oasis tiles (per-viewer, based on the scanning player's raid
history): `{b:riN}` is the last-raid outcome icon (1=won clean..7=lost),
followed by a raw timestamp string; `{b:biN}` is the bounty tier (0 empty, 1
half, 2 full) followed by `current/max` carry figures. These are lower-fidelity
than report ingestion — stored on columns prefixed `scan_` to mark them as
hints, not ground truth.

`{k.*}` / `{a.*}` are server-side i18n placeholders rendered client-side from
the shared translation bundle — we match on the placeholder names because
they're stable across locales (DE, FR, AR, ...).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.session import BrowserSession
from app.core.logging import get_logger
from app.models.map_tile import MapTile, TileType

log = get_logger("service.map_scan")


@dataclass
class ScannedTile:
    x: int
    y: int
    type: TileType
    oasis_type: str | None = None
    name: str | None = None
    population: int | None = None
    player_name: str | None = None
    village_id: int | None = None
    player_id: int | None = None
    alliance_id: int | None = None
    tribe: int | None = None
    # Scan-sourced observer hints (oases only) — see module docstring.
    scan_bounty_tier: int | None = None
    scan_bounty_pct: int | None = None
    scan_last_raid_outcome: int | None = None
    scan_last_raid_text: str | None = None


# ---- parsing helpers -------------------------------------------------------

_COORD_NOISE = re.compile(r"<[^>]+>")             # strip HTML tags from `text`
_BONUS_RE = re.compile(r"\{a[:.]r(\d)\}[^%]*?(\d+)\s*%")
_POP_RE = re.compile(r"\{k\.einwohner\}\s*(\d+)")
_PLAYER_RE = re.compile(r"\{k\.spieler\}\s*([^<]+?)\s*(?:<|\{|$)")
_TRIBE_RE = re.compile(r"\{a\.v(\d+)\}")
_NAME_FROM_DT_RE = re.compile(r"\{k\.dt\}\s*(.+)$")
# Observer hints. Bounty carry: `{b:biN} &#x202d;&#x202d;<cur>&#x202c;/&#x202d;<max>&#x202c;&#x202c;`.
_BOUNTY_RE = re.compile(r"\{b:bi(\d)\}\s*&#x202d;&#x202d;(\d+)&#x202c;/&#x202d;(\d+)&#x202c;")
# Last-raid report: `{b:riN} <timestamp> {b.riN}`.
_LAST_RAID_RE = re.compile(r"\{b:ri(\d)\}\s*([^<{]+?)\s*\{b\.ri\d\}")


async def fetch_tiles(
    session: BrowserSession, top_left: tuple[int, int], bottom_right: tuple[int, int]
) -> list[ScannedTile]:
    """Fetch one window of tiles from /api/v1/map/position.

    Returns every interesting tile in the window (oases + villages + natars);
    landscape (Forest / Sea / empty valley) is dropped. The caller persists
    them all — we track enemy villages too so future raid features can pull
    from the same dataset.
    """
    x1, y1 = top_left
    x2, y2 = bottom_right
    origin = session.page.url.split("/", 3)
    origin = "/".join(origin[:3]) if len(origin) >= 3 else ""
    url = f"{origin}/api/v1/map/position"

    payload = {"data": {"x": (x1 + x2) // 2, "y": (y1 + y2) // 2, "zoomLevel": 2}}
    log.debug("map_scan.request", url=url, payload=payload)
    try:
        resp = await session.page.request.post(url, data=payload)
        if not resp.ok:
            log.warning("map_scan.request.not_ok", status=resp.status, url=url)
            return []
        data = await resp.json()
    except Exception as e:  # noqa: BLE001
        log.exception("map_scan.request.error", err=str(e))
        return []

    raw_tiles = data.get("tiles") if isinstance(data, dict) else None
    if not raw_tiles:
        log.debug("map_scan.response.empty", keys=list(data.keys()) if isinstance(data, dict) else "?")
        return []

    out: list[ScannedTile] = []
    counts = {"oasis": 0, "village": 0, "natar": 0, "landscape": 0, "unknown": 0}
    for t in raw_tiles:
        try:
            scanned = _parse_tile(t)
        except Exception as e:  # noqa: BLE001
            log.debug("map_scan.parse_error", err=str(e), tile=t)
            counts["unknown"] += 1
            continue
        if scanned is None:
            counts["landscape"] += 1
            continue
        counts[scanned.type.value] = counts.get(scanned.type.value, 0) + 1
        out.append(scanned)

    log.info(
        "map_scan.parsed",
        window=(payload["data"]["x"], payload["data"]["y"]),
        fetched=len(raw_tiles), kept=len(out),
        oasis=counts["oasis"], village=counts["village"],
        natar=counts["natar"], landscape=counts["landscape"],
    )
    return out


def _parse_oasis_type(text: str) -> str | None:
    """Compact bonus summary like "r4_25" or "r2_25+r4_25" from oasis text."""
    bonuses: dict[str, int] = {}
    for m in _BONUS_RE.finditer(text):
        bonuses[f"r{m.group(1)}"] = int(m.group(2))
    return "+".join(f"{k}_{v}" for k, v in sorted(bonuses.items())) or None


def _parse_scan_hints(text: str) -> dict:
    """Extract observer-visible raid/bounty hints from an oasis `text` blob.

    Returns a dict with only the keys that were present — callers splat into
    ScannedTile kwargs so missing hints stay None.
    """
    out: dict = {}
    m = _BOUNTY_RE.search(text)
    if m:
        out["scan_bounty_tier"] = int(m.group(1))
        cur, mx = int(m.group(2)), int(m.group(3))
        out["scan_bounty_pct"] = round(cur / mx * 100) if mx else None
    m = _LAST_RAID_RE.search(text)
    if m:
        out["scan_last_raid_outcome"] = int(m.group(1))
        out["scan_last_raid_text"] = m.group(2).strip() or None
    return out


def _parse_tile(t: dict) -> ScannedTile | None:
    """Classify one raw tile. Returns None for landscape (we skip those)."""
    pos = t.get("position") or {}
    x = int(pos.get("x"))
    y = int(pos.get("y"))
    title = str(t.get("title") or "")
    text = str(t.get("text") or "")
    did = t.get("did")
    uid = t.get("uid")
    aid = t.get("aid")

    # Unoccupied oasis: did == -1, title tag `{k.fo}`.
    if did == -1 and "{k.fo}" in title:
        return ScannedTile(
            x=x, y=y, type=TileType.OASIS,
            oasis_type=_parse_oasis_type(text),
            **_parse_scan_hints(text),
        )

    # Occupied oasis: did == -1, title tag `{k.bt}`. Still OASIS, but with an
    # owner — farmlist logic can choose to skip these (owner defends) or raid
    # them anyway if the owner is weak.
    if did == -1 and "{k.bt}" in title:
        flat = _COORD_NOISE.sub(" ", text)
        player_m = _PLAYER_RE.search(flat)
        tribe_m = _TRIBE_RE.search(flat)
        return ScannedTile(
            x=x, y=y, type=TileType.OASIS,
            oasis_type=_parse_oasis_type(text),
            player_name=player_m.group(1).strip() if player_m else None,
            player_id=int(uid) if uid else None,
            alliance_id=int(aid) if aid else None,
            tribe=int(tribe_m.group(1)) if tribe_m else None,
            **_parse_scan_hints(text),
        )

    # Occupied village (uid + did set, did > 0).
    if uid is not None and did is not None and did > 0:
        name = None
        m = _NAME_FROM_DT_RE.search(title)
        if m:
            name = m.group(1).strip() or None
        # Clean `text` of HTML tags before extracting fields.
        flat = _COORD_NOISE.sub(" ", text)
        player_m = _PLAYER_RE.search(flat)
        pop_m = _POP_RE.search(flat)
        tribe_m = _TRIBE_RE.search(flat)
        player = player_m.group(1).strip() if player_m else None
        population = int(pop_m.group(1)) if pop_m else None
        tribe = int(tribe_m.group(1)) if tribe_m else None

        # Natars: canonical Natar uid is 1 (a few servers use up to ~10 for
        # system accounts). Also flagged by the name containing "Natar".
        is_natar = (
            (uid is not None and uid <= 10)
            or "natar" in (player or "").lower()
            or "natar" in name.lower() if name else False
        )
        return ScannedTile(
            x=x, y=y,
            type=TileType.NATAR if is_natar else TileType.VILLAGE,
            name=name, player_name=player, population=population,
            tribe=tribe, village_id=int(did) if did else None,
            player_id=int(uid) if uid else None,
        )

    # Landscape: Forest / Sea / `{k.vt} ...` (valley) / `{k.as} ...`
    # (abandoned settlement). Not a farm target.
    return None


async def upsert_scanned(
    db: AsyncSession, server_code: str, tiles: Iterable[ScannedTile]
) -> tuple[int, int]:
    """Insert new MapTiles, refresh metadata on existing rows."""
    now = datetime.now(tz=timezone.utc)
    new = 0
    updated = 0
    for s in tiles:
        tile = (
            await db.execute(
                select(MapTile).where(
                    MapTile.server_code == server_code, MapTile.x == s.x, MapTile.y == s.y,
                )
            )
        ).scalar_one_or_none()
        if tile is None:
            tile = MapTile(
                server_code=server_code, x=s.x, y=s.y, type=s.type,
                name=s.name, oasis_type=s.oasis_type,
                player_name=s.player_name, population=s.population,
                tribe=s.tribe, village_id=s.village_id, player_id=s.player_id,
                alliance_id=s.alliance_id,
                scan_bounty_tier=s.scan_bounty_tier,
                scan_bounty_pct=s.scan_bounty_pct,
                scan_last_raid_outcome=s.scan_last_raid_outcome,
                scan_last_raid_text=s.scan_last_raid_text,
                last_seen_at=now,
            )
            db.add(tile)
            new += 1
        else:
            changed = (
                tile.type != s.type
                or tile.oasis_type != s.oasis_type
                or tile.player_name != s.player_name
                or tile.population != s.population
                or tile.scan_bounty_tier != s.scan_bounty_tier
                or tile.scan_bounty_pct != s.scan_bounty_pct
                or tile.scan_last_raid_outcome != s.scan_last_raid_outcome
                or tile.scan_last_raid_text != s.scan_last_raid_text
            )
            tile.type = s.type
            tile.oasis_type = s.oasis_type
            # Scan hints are overwritten unconditionally — a missing hint this
            # scan means the server stopped showing it (e.g. bounty tier aged
            # out), which we want reflected.
            tile.scan_bounty_tier = s.scan_bounty_tier
            tile.scan_bounty_pct = s.scan_bounty_pct
            tile.scan_last_raid_outcome = s.scan_last_raid_outcome
            tile.scan_last_raid_text = s.scan_last_raid_text
            if s.name is not None:
                tile.name = s.name
            if s.player_name is not None:
                tile.player_name = s.player_name
            if s.population is not None:
                tile.population = s.population
            if s.tribe is not None:
                tile.tribe = s.tribe
            if s.village_id is not None:
                tile.village_id = s.village_id
            if s.player_id is not None:
                tile.player_id = s.player_id
            if s.alliance_id is not None:
                tile.alliance_id = s.alliance_id
            tile.last_seen_at = now
            if changed:
                updated += 1
    await db.flush()
    log.info("map_scan.upsert", new=new, updated=updated, server=server_code)
    return new, updated


def sweep_rectangles(
    center: tuple[int, int], radius: int, step: int = 20
) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """Break a square of ±radius around `center` into `step`-sized tiles.

    The ajax endpoint limits the tile count per call; we page across small
    rectangles with a tile step that fits comfortably under the limit.
    """
    cx, cy = center
    out: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for x in range(cx - radius, cx + radius, step):
        for y in range(cy - radius, cy + radius, step):
            out.append(((x, y), (x + step, y + step)))
    return out
