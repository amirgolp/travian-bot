"""Pydantic schemas for API I/O."""
from __future__ import annotations

from pydantic import BaseModel, Field


class AccountIn(BaseModel):
    label: str = Field(..., min_length=1, max_length=64)
    server_url: str
    username: str
    password: str
    active_hours: str | None = None


class AccountOut(BaseModel):
    id: int
    label: str
    server_url: str
    server_code: str
    status: str
    active_hours: str | None = None

    model_config = {"from_attributes": True}


class VillageIn(BaseModel):
    account_id: int
    travian_id: int
    name: str
    x: int
    y: int
    is_capital: bool = False


class VillageOut(BaseModel):
    id: int
    account_id: int
    name: str
    x: int
    y: int
    is_capital: bool

    model_config = {"from_attributes": True}


class FarmlistIn(BaseModel):
    village_id: int
    name: str
    interval_seconds: int = 1800
    kind: str = "mixed"             # villages | oases_natars | mixed
    default_troops: dict[str, int] | None = None


class FarmlistSlotIn(BaseModel):
    """Add a target by either its MapTile id or by (x, y). One must be set."""
    farmlist_id: int
    tile_id: int | None = None
    target_x: int | None = None
    target_y: int | None = None
    troops: dict[str, int] | None = None


class BuildOrderIn(BaseModel):
    village_id: int
    building_key: str
    target_level: int
    slot: int | None = None
    priority: int = 100


class BuildOrderOut(BaseModel):
    id: int
    village_id: int
    building_key: str
    target_level: int
    slot: int | None
    priority: int
    status: str
    blocked_reason: str | None

    model_config = {"from_attributes": True}


class ReorderIn(BaseModel):
    village_id: int
    ordered_ids: list[int]
