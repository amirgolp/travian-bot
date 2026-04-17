"""Parse Travian's nightly map.sql dump and upsert MapTiles.

Each Legends server publishes `/map.sql` once a day. The format is a series of
INSERT statements. Columns vary slightly between versions, but the common
Legends shape is:

  INSERT INTO `x_world` VALUES
    (field_id, x, y, tribe_id, village_id, 'village_name',
     player_id, 'player_name', alliance_id, 'alliance_name', population);

Natars use player_id=1 / tribe_id=5. Unoccupied oases are NOT in this file;
they come from the in-game map scrape (MapScanController).

This parser is deliberately regex-based rather than a full SQL parser — we
don't need strictness, just speed and resilience to small format drift.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.map_tile import MapTile, TileType

log = get_logger("service.world_sql")


@dataclass
class WorldRow:
    x: int
    y: int
    tribe: int
    village_id: int
    name: str
    player_id: int
    player_name: str
    alliance_id: int
    alliance_name: str
    population: int


# Matches one VALUES tuple. Strings are '…' with escaped single quotes as ''.
# Column order: field_id, x, y, tribe, village_id, 'name', player_id, 'player',
#               alliance_id, 'alliance', population.
_VALUES_RE = re.compile(
    r"\(\s*"
    r"(-?\d+)\s*,\s*"          # field_id
    r"(-?\d+)\s*,\s*"          # x
    r"(-?\d+)\s*,\s*"          # y
    r"(-?\d+)\s*,\s*"          # tribe
    r"(-?\d+)\s*,\s*"          # village_id
    r"'((?:[^'\\]|''|\\.)*)'\s*,\s*"  # village name
    r"(-?\d+)\s*,\s*"          # player_id
    r"'((?:[^'\\]|''|\\.)*)'\s*,\s*"  # player name
    r"(-?\d+)\s*,\s*"          # alliance_id
    r"'((?:[^'\\]|''|\\.)*)'\s*,\s*"  # alliance name
    r"(-?\d+)\s*"              # population
    r"\)",
    re.DOTALL,
)


def parse_map_sql(text: str):
    """Yield WorldRow for every tuple in the dump. Tolerates BOM, CR/LF, utf-8 strings.

    When the canonical 11-column regex produces zero rows, the server may be
    using a shorter shape. Falls back to a tolerant tuple parser that splits
    on top-level commas (respecting 'quoted strings'). Logs the first 200
    bytes of the payload when both approaches yield zero so we can debug format.
    """
    rows = 0
    for m in _VALUES_RE.finditer(text):
        yield WorldRow(
            x=int(m.group(2)),
            y=int(m.group(3)),
            tribe=int(m.group(4)),
            village_id=int(m.group(5)),
            name=m.group(6).replace("''", "'"),
            player_id=int(m.group(7)),
            player_name=m.group(8).replace("''", "'"),
            alliance_id=int(m.group(9)),
            alliance_name=m.group(10).replace("''", "'"),
            population=int(m.group(11)),
        )
        rows += 1

    if rows > 0:
        log.debug("world_sql.parse", rows=rows, strategy="11-col")
        return

    # Fallback: try the tolerant parser.
    for row in _parse_tolerant(text):
        yield row
        rows += 1

    log.debug("world_sql.parse", rows=rows, strategy="tolerant")
    if rows == 0:
        head = text[:240].replace("\n", "\\n")
        log.warning(
            "world_sql.parse.empty",
            bytes=len(text),
            head=head,
            hint="Drop into samples/world_sql/ so we can calibrate the regex",
        )


# Splits one VALUES tuple into fields, respecting 'quoted strings' with
# escaped-quote via `''` doubling. Handles row shapes that differ from the
# canonical 11-col Legends format (some servers add/remove columns).
_TUPLE_RE = re.compile(r"\(((?:[^()']|'(?:[^'\\]|''|\\.)*')*)\)", re.DOTALL)


def _split_fields(body: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    in_str = False
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "'" and not in_str:
            in_str = True
            buf.append(ch)
        elif ch == "'" and in_str:
            if i + 1 < len(body) and body[i + 1] == "'":
                buf.append("''")
                i += 1
            else:
                in_str = False
                buf.append(ch)
        elif ch == "," and not in_str:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    if buf:
        out.append("".join(buf).strip())
    return out


def _unquote(s: str) -> str:
    s = s.strip()
    if s.startswith("'") and s.endswith("'"):
        return s[1:-1].replace("''", "'")
    return s


def _as_int(s: str, default: int = 0) -> int:
    try:
        return int(s.strip())
    except Exception:
        return default


def _parse_tolerant(text: str):
    """Yield WorldRow for tuples of 8–12 fields by sniffing the role of each.

    Heuristic: first three numeric fields are (field_id?, x, y) or (x, y, tribe);
    quoted strings are (name, player, alliance); trailing int is population.
    We fall back to positional guessing if neither pattern matches cleanly.
    """
    for m in _TUPLE_RE.finditer(text):
        fields = _split_fields(m.group(1))
        if len(fields) < 6:
            continue
        # Locate the quoted string positions → most likely name/player/alliance.
        quoted_idx = [i for i, f in enumerate(fields) if f.startswith("'")]
        if len(quoted_idx) < 3:
            continue
        name_i, player_i, alliance_i = quoted_idx[:3]
        name = _unquote(fields[name_i])
        player_name = _unquote(fields[player_i])
        alliance_name = _unquote(fields[alliance_i])

        # Numeric fields: everything before the name block is positional map
        # data (likely field_id, x, y, tribe, village_id or a subset).
        head = [_as_int(f) for f in fields[:name_i]]
        # Canonical 11-col: head == [field_id, x, y, tribe, village_id]; x at [1], y at [2].
        # 10-col variants: head == [x, y, tribe, village_id]; x at [0], y at [1].
        if len(head) == 5:
            x, y, tribe, village_id = head[1], head[2], head[3], head[4]
        elif len(head) == 4:
            x, y, tribe, village_id = head[0], head[1], head[2], head[3]
        elif len(head) >= 6:
            # map.sql occasionally carries an extra leading id; x/y are the
            # first two numerics that look like map coords (|v| < 1000).
            cand = [i for i, v in enumerate(head) if -500 <= v <= 500]
            if len(cand) >= 2:
                x, y = head[cand[0]], head[cand[1]]
                tribe = head[cand[1] + 1] if cand[1] + 1 < len(head) else 0
                village_id = head[cand[1] + 2] if cand[1] + 2 < len(head) else 0
            else:
                continue
        else:
            continue

        # Column layout around the quoted strings:
        #   ... name(q) player_id* ... player(q) alliance_id* ... alliance(q) population*
        # where `*` is a numeric we expect but can't always pin to a fixed offset.
        name_player_gap = [_as_int(f) for f in fields[name_i + 1 : player_i]]
        player_alliance_gap = [_as_int(f) for f in fields[player_i + 1 : alliance_i]]
        after_alliance = [_as_int(f) for f in fields[alliance_i + 1 :]]
        player_id = name_player_gap[0] if name_player_gap else 0
        alliance_id = player_alliance_gap[0] if player_alliance_gap else 0
        population = after_alliance[-1] if after_alliance else 0

        yield WorldRow(
            x=x, y=y, tribe=tribe, village_id=village_id,
            name=name, player_id=player_id, player_name=player_name,
            alliance_id=alliance_id, alliance_name=alliance_name,
            population=population,
        )


async def download_map_sql(server_url: str, timeout_s: float = 60.0) -> str:
    url = server_url.rstrip("/") + "/map.sql"
    log.info("world_sql.download", url=url)
    async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        # Travian serves latin-1 / utf-8 depending on locale.
        try:
            return resp.content.decode("utf-8")
        except UnicodeDecodeError:
            return resp.content.decode("latin-1", errors="replace")


def _row_type(row: WorldRow, account_player_id: int | None) -> TileType:
    if row.player_id == 1 or row.tribe == 5:
        return TileType.NATAR
    if account_player_id is not None and row.player_id == account_player_id:
        return TileType.OWN_VILLAGE
    return TileType.VILLAGE


async def sync_world_sql(
    db: AsyncSession,
    server_code: str,
    server_url: str,
    account_player_id: int | None = None,
) -> tuple[int, int]:
    """Download + parse + upsert. Returns (new_tiles, updated_tiles)."""
    text = await download_map_sql(server_url)
    now = datetime.now(tz=timezone.utc)

    new_tiles = 0
    updated_tiles = 0
    batch = 0

    for row in parse_map_sql(text):
        batch += 1
        # Compact existence check to avoid loading the full table into memory.
        tile = (
            await db.execute(
                select(MapTile).where(
                    MapTile.server_code == server_code,
                    MapTile.x == row.x,
                    MapTile.y == row.y,
                )
            )
        ).scalar_one_or_none()

        ttype = _row_type(row, account_player_id)
        if tile is None:
            tile = MapTile(
                server_code=server_code,
                x=row.x, y=row.y, type=ttype,
                name=row.name, tribe=row.tribe, population=row.population,
                village_id=row.village_id,
                player_id=row.player_id, player_name=row.player_name,
                alliance_id=row.alliance_id, alliance_name=row.alliance_name,
                last_seen_at=now,
            )
            db.add(tile)
            new_tiles += 1
        else:
            # Refresh fields that may change (owner, pop, alliance)
            changed = (
                tile.type != ttype
                or tile.name != row.name
                or tile.population != row.population
                or tile.player_id != row.player_id
                or tile.alliance_id != row.alliance_id
            )
            if changed:
                tile.type = ttype
                tile.name = row.name
                tile.tribe = row.tribe
                tile.population = row.population
                tile.village_id = row.village_id
                tile.player_id = row.player_id
                tile.player_name = row.player_name
                tile.alliance_id = row.alliance_id
                tile.alliance_name = row.alliance_name
                updated_tiles += 1
            tile.last_seen_at = now

        if batch % 500 == 0:
            await db.flush()

    await db.flush()
    log.info(
        "world_sql.sync.done",
        server=server_code, new=new_tiles, updated=updated_tiles, total=batch,
    )
    return new_tiles, updated_tiles
