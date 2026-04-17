"""Loader for app/data/troops.yaml."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "troops.yaml"

BUILDING_GID = {
    "barracks": 19,
    "stable": 20,
    "workshop": 21,
    "residence": 25,
}


@lru_cache(maxsize=1)
def _load() -> dict:
    with _DATA_PATH.open() as fh:
        return yaml.safe_load(fh)


def troop_info(tribe: str | None, key: str) -> dict:
    """Return `{name, building, gid}` for a troop key under the given tribe.

    Falls back to the `default` block if the tribe is unknown or missing.
    """
    data = _load()
    block = data.get("tribes", {}).get((tribe or "").lower()) or data["default"]
    row = block.get(key) or data["default"].get(key) or {}
    building = row.get("building")
    return {
        "key": key,
        "name": row.get("name", key),
        "building": building,
        "gid": BUILDING_GID.get(building),
    }


def all_troops(tribe: str | None) -> list[dict]:
    """Ordered list t1..t10 with resolved names for one tribe."""
    return [troop_info(tribe, f"t{i}") for i in range(1, 11)]
