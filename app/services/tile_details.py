"""Per-tile details (animal counts on unoccupied oases).

Endpoint: POST /api/v1/map/tile-details (calibrated from
samples/legend/tile-details-oasis.{txt,json}).

Request body: {"x": x, "y": y}
Response:     {"html": "<div id='tileDetails'>...</div>"}

For unoccupied oases the HTML contains a `<h4>Troops</h4>` heading followed
by a `<table id="troop_info">` whose rows look like:
    <td class="ico"><img class="unit u35" alt="Wild Boar" ...></td>
    <td class="val">7</td>
    <td class="desc">Wild Boars</td>

`uNN` is the Travian troop-definition ID (nature units fall in the 31..40
range). A clean oasis either omits the Troops section entirely or has an
empty table. Occupied oases show the owner's garrison behind-the-scenes but
the public response usually has no Troops section — we treat that as "no
animals visible" and the farming layer filters on owner separately.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from app.browser.session import BrowserSession
from app.core.logging import get_logger
from app.models.map_tile import MapTile

log = get_logger("service.tile_details")

# Animals in an unoccupied oasis regenerate on the order of hours — a half-hour
# TTL keeps the cached answer safe while cutting the tile-details request rate
# roughly to 1/N of the farming tick frequency on steady-state slots. Tune
# downward if we start getting ambushed by freshly-regenerated animals.
ANIMAL_CACHE_TTL = timedelta(minutes=30)

_TROOPS_SECTION_RE = re.compile(
    r"<h4>\s*Troops\s*</h4>.*?<table[^>]*id=\"troop_info\"[^>]*>(.*?)</table>",
    re.DOTALL,
)
_TROOP_ROW_RE = re.compile(
    r"<img[^>]*class=\"unit u(\d+)\"[^>]*?>.*?<td class=\"val\">\s*(\d+)\s*</td>",
    re.DOTALL,
)
# Travian sends X-Version on XHRs; missing it occasionally 403s. Hardcoded
# from the current capture — if tile-details starts failing after a game
# patch, refresh by lifting the value from a window global during session
# bootstrap.
_X_VERSION = "417.5"


async def fetch_oasis_animals(
    session: BrowserSession, x: int, y: int
) -> dict[int, int] | None:
    """Return {unit_id: count} for animals visible on the oasis at (x, y).

    `None` means the request failed — callers should fail open (proceed with
    the raid) rather than stall farming on a flaky endpoint. `{}` means the
    oasis is clean.
    """
    origin_parts = session.page.url.split("/", 3)
    origin = "/".join(origin_parts[:3]) if len(origin_parts) >= 3 else ""
    url = f"{origin}/api/v1/map/tile-details"
    try:
        resp = await session.page.request.post(
            url,
            data={"x": x, "y": y},
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "X-Version": _X_VERSION,
            },
        )
        if not resp.ok:
            log.warning("tile_details.not_ok", status=resp.status, x=x, y=y)
            return None
        data = await resp.json()
    except Exception as e:  # noqa: BLE001
        log.exception("tile_details.error", err=str(e), x=x, y=y)
        return None

    html = data.get("html") if isinstance(data, dict) else None
    if not html:
        return {}
    m = _TROOPS_SECTION_RE.search(html)
    if not m:
        return {}
    counts: dict[int, int] = {}
    for row in _TROOP_ROW_RE.finditer(m.group(1)):
        counts[int(row.group(1))] = int(row.group(2))
    return counts


async def get_oasis_animals_cached(
    session: BrowserSession, tile: MapTile
) -> dict[int, int] | None:
    """Cached wrapper over `fetch_oasis_animals`. Writes the result to
    `tile.animals_json` / `tile.animals_checked_at` — caller must be inside a
    session that will commit (or flush + commit) for persistence.

    Returns the cached animals dict if fresh; otherwise fetches and stores.
    `None` propagates from the fetch layer to mean "couldn't check, fail open".
    """
    now = datetime.now(tz=timezone.utc)
    if tile.animals_checked_at is not None:
        checked = tile.animals_checked_at
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        if now - checked < ANIMAL_CACHE_TTL:
            try:
                cached = json.loads(tile.animals_json or "{}")
                return {int(k.lstrip("u")): int(v) for k, v in cached.items()}
            except (ValueError, TypeError):
                # Corrupt cache — fall through and refetch.
                pass

    fresh = await fetch_oasis_animals(session, tile.x, tile.y)
    if fresh is None:
        return None
    tile.animals_json = json.dumps({f"u{k}": v for k, v in fresh.items()})
    tile.animals_checked_at = now
    return fresh
