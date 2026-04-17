"""Sidebar village list — scraped from dorf1.php (or any page that shows it).

Selectors calibrated from samples/legend/dorf1:
  .villageList .listEntry.village              — one per village
  [data-did]                                    — Travian's `newdid` (stable id)
  .name                                         — village name text
  .coordinateX / .coordinateY                   — coord spans (wrapped in bidi marks)
  .listEntry.village.capital                    — capital village (verify when seen)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from playwright.async_api import Page

from app.core.logging import get_logger

log = get_logger("page.sidebar")

# Strip LRM/RLM/LRE/PDF bidi marks and normalize U+2212 minus.
_BIDI_CHARS = "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
_BIDI_TRANS = str.maketrans("", "", _BIDI_CHARS)


def _coord(text: str | None) -> int | None:
    if not text:
        return None
    clean = text.translate(_BIDI_TRANS).replace("\u2212", "-")
    m = re.search(r"-?\d+", clean)
    return int(m.group(0)) if m else None


@dataclass
class SidebarVillage:
    travian_id: int       # data-did
    name: str
    x: int
    y: int
    is_capital: bool
    is_active: bool       # the one the player currently has selected


class SidebarVillages:
    """Read the left-rail village list. Present on every authenticated page."""

    class Selectors:
        ENTRY = ".villageList .listEntry.village"
        NAME = ".name"
        COORD_X = ".coordinateX"
        COORD_Y = ".coordinateY"

    def __init__(self, page: Page):
        self.page = page

    async def read(self) -> list[SidebarVillage]:
        """Return one SidebarVillage per sidebar entry. Returns [] if the
        sidebar isn't rendered (e.g. page didn't load or we're logged out)."""
        entries = self.page.locator(self.Selectors.ENTRY)
        count = await entries.count()
        log.debug("sidebar.read.count", entries=count)

        out: list[SidebarVillage] = []
        for i in range(count):
            e = entries.nth(i)
            did_str = await e.get_attribute("data-did") or ""
            if not did_str.isdigit():
                log.warning("sidebar.entry.no_did", index=i)
                continue
            name = (await e.locator(self.Selectors.NAME).first.text_content() or "").strip()
            x = _coord(await e.locator(self.Selectors.COORD_X).first.text_content())
            y = _coord(await e.locator(self.Selectors.COORD_Y).first.text_content())
            if x is None or y is None:
                log.warning("sidebar.entry.bad_coords", name=name, did=did_str)
                continue
            classes = (await e.get_attribute("class")) or ""
            out.append(SidebarVillage(
                travian_id=int(did_str),
                name=name, x=x, y=y,
                is_capital="capital" in classes,
                is_active="active" in classes,
            ))
        log.info("sidebar.read", count=len(out))
        return out
