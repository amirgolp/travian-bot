"""Resolver for Travian hero-item type ids -> names.

Wraps `app/data/hero_items.yaml`. Unknown ids fall back to `"Item #<id>"` so
the dashboard always shows something useful while the catalog is extended.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "hero_items.yaml"


@lru_cache(maxsize=1)
def _load() -> dict:
    if not _DATA_PATH.exists():
        return {"slots": {}}
    with _DATA_PATH.open() as fh:
        return yaml.safe_load(fh) or {"slots": {}}


def item_info(slot: str, type_id: int | None) -> dict:
    """Resolve `(slot, itemTypeId)` to `{name, description}`.

    Missing entries yield `{"name": "Item #<id>", "description": None}` so
    callers can always render a label even for catalog gaps.
    """
    if type_id is None:
        return {"name": None, "description": None}
    data = _load()
    slot_map = (data.get("slots") or {}).get(slot) or {}
    entry = slot_map.get(type_id) or {}
    name = entry.get("name") or f"Item #{type_id}"
    return {"name": name, "description": entry.get("description")}


def item_name(slot: str, type_id: int | None) -> str | None:
    """Backwards-compatible name-only accessor (thin wrapper over item_info)."""
    return item_info(slot, type_id)["name"]
