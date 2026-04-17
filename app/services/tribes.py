"""Shared tribe mapping + detection helpers.

Travian's internal tribe ids appear in `tribe<N>` CSS classes on
`.resourceField` / other tribe-themed DOM nodes on dorf1. Nature (4) isn't a
playable tribe; the Natar NPC (5) doesn't play from the browser.
"""
from __future__ import annotations

from app.browser.pages.dorf import Dorf1Page
from app.browser.session import BrowserSession
from app.models.village import Tribe

TRIBE_BY_ID: dict[int, Tribe] = {
    1: Tribe.ROMAN,
    2: Tribe.TEUTON,
    3: Tribe.GAUL,
    6: Tribe.EGYPTIAN,
    7: Tribe.HUN,
    8: Tribe.SPARTAN,
    9: Tribe.VIKING,
}


async def detect_tribe(session: BrowserSession) -> Tribe | None:
    """Read the current page's `tribe<N>` class and map to a Tribe enum.

    Caller must already be on a dorf-themed page (dorf1 is the canonical one).
    Returns None when the class isn't parseable or the id isn't playable.
    """
    dorf1 = Dorf1Page(session.page)
    tid = await dorf1.read_tribe_id()
    if tid is None:
        return None
    return TRIBE_BY_ID.get(tid)
