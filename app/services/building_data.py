"""Loader for app/data/buildings.yaml."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Prereq:
    key: str
    level: int


@dataclass(frozen=True)
class BuildingDef:
    key: str
    gid: int
    name: str
    category: str
    placement: str        # "dorf1" | "dorf2" | "both"
    max_level: int
    unique: bool
    prereqs: tuple[Prereq, ...]


_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "buildings.yaml"


@lru_cache(maxsize=1)
def load_buildings() -> dict[str, BuildingDef]:
    with _DATA_PATH.open() as fh:
        raw = yaml.safe_load(fh)
    out: dict[str, BuildingDef] = {}
    for entry in raw["buildings"]:
        prereqs = tuple(Prereq(p["key"], int(p["level"])) for p in entry.get("prereqs") or [])
        out[entry["key"]] = BuildingDef(
            key=entry["key"],
            gid=int(entry["gid"]),
            name=entry["name"],
            category=entry["category"],
            placement=entry["placement"],
            max_level=int(entry["max_level"]),
            unique=bool(entry.get("unique", False)),
            prereqs=prereqs,
        )
    return out


def get(key: str) -> BuildingDef:
    return load_buildings()[key]


@lru_cache(maxsize=1)
def by_gid() -> dict[int, BuildingDef]:
    """Reverse index gid -> BuildingDef for scraper-to-DB mapping."""
    return {d.gid: d for d in load_buildings().values()}
