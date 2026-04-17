"""Stable per-account browser fingerprint: UA, viewport, timezone, locale.

A fingerprint is derived deterministically from the account label so that restarting
the bot presents the same identity to the server (random churn is itself a signal).
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Fingerprint:
    user_agent: str
    viewport: tuple[int, int]
    screen: tuple[int, int]
    timezone: str
    locale: str
    platform: str
    hardware_concurrency: int
    device_memory: int
    webgl_vendor: str
    webgl_renderer: str


# Realistic, recent Chrome on desktop (the only family Travian Legends UI fully
# supports). Keep this list small and current — ancient UAs stand out more than
# common ones.
_CHROME_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

# (viewport_w, viewport_h, screen_w, screen_h) — browser chrome subtracted from screen.
_RESOLUTIONS = [
    (1280, 720, 1280, 800),
    (1366, 657, 1366, 768),
    (1440, 821, 1440, 900),
    (1536, 864, 1536, 960),
    (1600, 900, 1600, 1000),
    (1920, 969, 1920, 1080),
]

_TIMEZONES = [
    "Europe/Berlin", "Europe/Paris", "Europe/London", "Europe/Warsaw",
    "Europe/Amsterdam", "Europe/Stockholm", "America/New_York",
    "America/Chicago", "America/Los_Angeles",
]

_LOCALES = ["en-US", "en-GB", "de-DE", "fr-FR", "pl-PL", "nl-NL"]

_GPU_PAIRS = [
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Apple Inc.", "Apple M1"),
]


def fingerprint_for(label: str) -> Fingerprint:
    """Deterministic fingerprint from the account label (stable across restarts)."""
    seed = int(hashlib.sha256(label.encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)

    ua = rng.choice(_CHROME_UAS)
    vw, vh, sw, sh = rng.choice(_RESOLUTIONS)
    tz = rng.choice(_TIMEZONES)
    locale = rng.choice(_LOCALES)
    gpu_vendor, gpu_renderer = rng.choice(_GPU_PAIRS)

    if "Windows" in ua:
        platform = "Win32"
    elif "Mac OS" in ua:
        platform = "MacIntel"
    else:
        platform = "Linux x86_64"

    return Fingerprint(
        user_agent=ua,
        viewport=(vw, vh),
        screen=(sw, sh),
        timezone=tz,
        locale=locale,
        platform=platform,
        hardware_concurrency=rng.choice([4, 6, 8, 12, 16]),
        device_memory=rng.choice([4, 8, 8, 16]),
        webgl_vendor=gpu_vendor,
        webgl_renderer=gpu_renderer,
    )
