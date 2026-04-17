"""Behavioral humanization helpers.

Even with a perfect fingerprint, multi-hunter flags accounts on *how* you play:
- clicks at exactly N ms intervals
- actions only on the critical path (never browsing the hero / reports / map)
- 24/7 activity with no sleep window
- straight-line or instant mouse movements

These helpers inject the variance and idle behavior a human shows.
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, time
from typing import Iterable

from playwright.async_api import ElementHandle, Locator, Page

from app.core.config import get_settings


def _cfg():
    return get_settings()


async def sleep_action(scale: float = 1.0) -> None:
    """Sleep a random delay in [min, max] * scale seconds (log-normal-ish)."""
    s = _cfg()
    base = random.uniform(s.action_delay_min, s.action_delay_max)
    # Long-tail: ~5% of delays are 3-6x longer (user got distracted)
    if random.random() < 0.05:
        base *= random.uniform(3, 6)
    await asyncio.sleep(base * scale)


async def read_page(page: Page, words: int | None = None) -> None:
    """Simulate reading: idle proportional to visible text."""
    if words is None:
        try:
            text = await page.evaluate("() => document.body ? document.body.innerText.length : 0")
            words = max(10, int(text) // 5)
        except Exception:
            words = 80
    # ~220 wpm average, big jitter
    seconds = (words / 220) * 60 * random.uniform(0.4, 1.3)
    seconds = min(max(seconds, 0.6), 12.0)
    await asyncio.sleep(seconds)


def _bezier_points(
    x0: float, y0: float, x1: float, y1: float, steps: int
) -> list[tuple[float, float]]:
    """Cubic Bezier with two random control points — sweeps are curved, not linear."""
    dx, dy = x1 - x0, y1 - y0
    cx1 = x0 + dx * random.uniform(0.2, 0.5) + random.uniform(-40, 40)
    cy1 = y0 + dy * random.uniform(0.2, 0.5) + random.uniform(-40, 40)
    cx2 = x0 + dx * random.uniform(0.5, 0.8) + random.uniform(-40, 40)
    cy2 = y0 + dy * random.uniform(0.5, 0.8) + random.uniform(-40, 40)
    pts: list[tuple[float, float]] = []
    for i in range(1, steps + 1):
        t = i / steps
        u = 1 - t
        bx = u**3 * x0 + 3 * u**2 * t * cx1 + 3 * u * t**2 * cx2 + t**3 * x1
        by = u**3 * y0 + 3 * u**2 * t * cy1 + 3 * u * t**2 * cy2 + t**3 * y1
        pts.append((bx, by))
    return pts


async def human_move_to(page: Page, x: float, y: float) -> None:
    """Move the mouse along a curved path with variable-speed steps."""
    # Playwright doesn't expose the current cursor position; keep one in page state.
    state = await page.evaluate(
        "() => ({ x: window.__mouse_x || 0, y: window.__mouse_y || 0 })"
    )
    x0, y0 = state["x"], state["y"]
    distance = max(1.0, ((x - x0) ** 2 + (y - y0) ** 2) ** 0.5)
    steps = max(8, min(40, int(distance / 25)))
    for bx, by in _bezier_points(x0, y0, x, y, steps):
        await page.mouse.move(bx, by)
        await asyncio.sleep(random.uniform(0.008, 0.022))
    await page.evaluate(f"() => {{ window.__mouse_x = {x}; window.__mouse_y = {y}; }}")


async def human_click(page: Page, target: Locator | ElementHandle) -> None:
    """Curve-move to the element, small in-box offset, click with a realistic hold."""
    box = await target.bounding_box()
    if not box:
        await target.click()
        return
    # Target a point away from center so repeated clicks don't all land in the same pixel.
    tx = box["x"] + box["width"] * random.uniform(0.25, 0.75)
    ty = box["y"] + box["height"] * random.uniform(0.25, 0.75)
    await human_move_to(page, tx, ty)
    await asyncio.sleep(random.uniform(0.04, 0.16))
    await page.mouse.down()
    await asyncio.sleep(random.uniform(0.05, 0.14))
    await page.mouse.up()


async def human_type(page: Page, locator: Locator, text: str) -> None:
    """Type with per-char delay and occasional typo + backspace."""
    await locator.click()
    for ch in text:
        if random.random() < 0.015 and ch.isalpha():
            wrong = random.choice("qwertyuiopasdfghjklzxcvbnm")
            await page.keyboard.type(wrong, delay=random.uniform(60, 140))
            await asyncio.sleep(random.uniform(0.12, 0.35))
            await page.keyboard.press("Backspace")
        await page.keyboard.type(ch, delay=random.uniform(55, 170))


async def idle_scroll(page: Page) -> None:
    """A few mouse-wheel ticks in either direction — a human often scrolls while thinking."""
    for _ in range(random.randint(1, 4)):
        dy = random.choice([-1, 1]) * random.randint(80, 320)
        await page.mouse.wheel(0, dy)
        await asyncio.sleep(random.uniform(0.15, 0.8))


async def maybe_take_tangent(page: Page, candidates: Iterable[str]) -> None:
    """With low probability, click a non-critical link (hero, reports, map) to look human."""
    if random.random() > 0.15:
        return
    for sel in candidates:
        el = page.locator(sel).first
        try:
            if await el.count() > 0 and await el.is_visible():
                await human_click(page, el)
                await read_page(page)
                await page.go_back()
                await sleep_action()
                return
        except Exception:
            continue


def parse_active_hours(spec: str) -> list[tuple[time, time]]:
    """Parse one or more comma-separated windows into (lo, hi) pairs.

    Accepts \"07:30-23:45\" or \"09:00-13:00,14:00-22:00,23:00-08:00\".
    Wrap-around (hi < lo) is allowed for windows that cross midnight.
    """
    windows: list[tuple[time, time]] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        lo, hi = chunk.split("-")
        lh, lm = [int(x) for x in lo.split(":")]
        hh, hm = [int(x) for x in hi.split(":")]
        windows.append((time(lh, lm), time(hh, hm)))
    if not windows:
        raise ValueError(f"empty active_hours spec: {spec!r}")
    return windows


def _window_contains(lo: time, hi: time, t: time) -> bool:
    if lo <= hi:
        return lo <= t <= hi
    return t >= lo or t <= hi  # crosses midnight


def in_active_window(spec: str, now: datetime | None = None) -> bool:
    t = (now or datetime.now()).time()
    return any(_window_contains(lo, hi, t) for lo, hi in parse_active_hours(spec))


def seconds_until_active(spec: str, now: datetime | None = None) -> float:
    """If currently outside all windows, seconds until the nearest window opens."""
    from datetime import datetime as dt, timedelta
    now = now or dt.now()
    best: float | None = None
    for lo, _ in parse_active_hours(spec):
        target = now.replace(hour=lo.hour, minute=lo.minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        delta = (target - now).total_seconds()
        if best is None or delta < best:
            best = delta
    assert best is not None  # parse_active_hours guarantees ≥1 window
    return best
