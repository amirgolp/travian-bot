"""Travian server URL parsing and version detection.

Examples of in-the-wild URLs:
  https://ts1.x1.international.travian.com/
  https://ts5.x2.europe.travian.com/
  https://ts30.x3.arabics.travian.com/
  https://ts1.x1.america.travian.com/

Pattern (Legends): https://ts{INDEX}.x{SPEED}.{DOMAIN}.travian.com

Legends is the current T4.6 family and is what this bot targets first. Other
versions (Kingdoms, Shores of War) have different URL shapes and DOM; when we
add them the detector returns a different GameVersion and the browser layer
dispatches to version-specific page objects.
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from urllib.parse import urlparse


class GameVersion(str, enum.Enum):
    LEGENDS = "legends"     # T4.6
    SHORES_OF_WAR = "shores"  # future
    KINGDOMS = "kingdoms"    # future
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ServerInfo:
    url: str                 # normalized, trailing slash stripped
    version: GameVersion
    region: str              # "international", "europe", ...
    gameworld: str           # "ts1"
    speed: str               # "x1", "x3", "x10"
    code: str                # stable identifier: "legends-international-ts1-x1"

    @property
    def speed_multiplier(self) -> int:
        """Numeric multiplier extracted from `speed` ('x3' -> 3). 1 on parse failure."""
        digits = "".join(c for c in self.speed if c.isdigit())
        return int(digits) if digits else 1


# Gameworld identifier is the first host label; speed is `x\d+`; then region;
# then travian.com. Examples:
#   ts1.x1.international.travian.com  — standard numbered world
#   ts30.x3.arabics.travian.com       — numbered + fast
#   rof.x3.international.travian.com  — Return of Fame special world
#   com.x1.europe.travian.com         — named world
_LEGENDS_RE = re.compile(
    r"^(?P<gw>[a-z0-9-]+)\.(?P<speed>x\d+)\.(?P<region>[a-z0-9-]+)\.travian\.com$",
    re.IGNORECASE,
)


def detect_server(url: str) -> ServerInfo:
    """Parse a Travian login/game URL into ServerInfo, or raise ValueError."""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.hostname or "").lower()
    m = _LEGENDS_RE.match(host)
    if not m:
        # Kingdoms uses kingdoms.com; shores has a different host form.
        # We intentionally fail loud so the user sees an unsupported server early.
        raise ValueError(f"Unrecognized Travian server host: {host!r}")
    gw = m.group("gw").lower()
    speed = m.group("speed").lower()
    region = m.group("region").lower()
    version = GameVersion.LEGENDS
    code = f"{version.value}-{region}-{gw}-{speed}"
    normalized = f"https://{host}"
    return ServerInfo(
        url=normalized, version=version, region=region,
        gameworld=gw, speed=speed, code=code,
    )
