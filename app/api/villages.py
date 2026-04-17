from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import VillageIn, VillageOut
from app.db.session import get_session
from app.models.build import BuildOrder, BuildOrderStatus, BuildingSlot
from app.models.village import Village

router = APIRouter(prefix="/villages", tags=["villages"])


@router.post("", response_model=VillageOut)
async def create(payload: VillageIn, db: AsyncSession = Depends(get_session)) -> Village:
    v = Village(**payload.model_dump())
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


@router.get("", response_model=list[VillageOut])
async def list_all(account_id: int | None = None, db: AsyncSession = Depends(get_session)):
    stmt = select(Village)
    if account_id is not None:
        stmt = stmt.where(Village.account_id == account_id)
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows)


@router.get("/{village_id}/overview")
async def overview(village_id: int, db: AsyncSession = Depends(get_session)) -> dict:
    """Everything we know about one village: metadata, resources, build state.

    Fields that require their own scrapers (incoming troops, attack warnings,
    own troop counts) are returned as `None` with a small `missing` block so
    the UI can show "not yet scraped" instead of blanking.
    """
    v = await db.get(Village, village_id)
    if v is None:
        raise HTTPException(404, "village not found")

    # All build orders for this village, grouped by status.
    orders = (
        await db.execute(
            select(BuildOrder)
            .where(BuildOrder.village_id == village_id)
            .order_by(BuildOrder.priority.asc(), BuildOrder.id.asc())
        )
    ).scalars().all()
    in_progress = [_o(o) for o in orders if o.status == BuildOrderStatus.IN_PROGRESS]
    queued = [_o(o) for o in orders if o.status in (BuildOrderStatus.QUEUED, BuildOrderStatus.BLOCKED)]
    done_cancelled_failed = [
        _o(o) for o in orders
        if o.status in (BuildOrderStatus.DONE, BuildOrderStatus.FAILED, BuildOrderStatus.CANCELLED)
    ]

    # Latest building-slot snapshot (current levels we know about).
    slots = (
        await db.execute(
            select(BuildingSlot).where(BuildingSlot.village_id == village_id)
            .order_by(BuildingSlot.slot.asc())
        )
    ).scalars().all()

    def _j(raw: str | None, fallback):
        try:
            return json.loads(raw) if raw else fallback
        except Exception:
            return fallback

    own_troops = _j(v.troops_json, {})
    movements_in = _j(v.movements_in_json, [])
    movements_out = _j(v.movements_out_json, [])
    incoming_attacks = [m for m in movements_in if m.get("is_attack")]
    incoming_reinforcements = [
        m for m in movements_in
        if m.get("direction") == "in_reinforce"
    ]

    missing = []
    if v.troops_observed_at is None:
        # TroopsController hasn't run yet — UI shows "not yet scraped" hint.
        missing.append("troops")

    return {
        "village": {
            "id": v.id, "account_id": v.account_id, "travian_id": v.travian_id,
            "name": v.name, "x": v.x, "y": v.y, "is_capital": v.is_capital,
            "tribe": v.tribe.value if v.tribe else None,
        },
        "resources": {
            "wood": v.wood, "clay": v.clay, "iron": v.iron, "crop": v.crop,
            "warehouse_cap": v.warehouse_cap, "granary_cap": v.granary_cap,
        },
        "build": {
            "in_progress": in_progress,
            "queued": queued,
            "history": done_cancelled_failed[:20],
            # What the game is actually doing right now (scraped from dorf1),
            # separate from bot-managed BuildOrders. Populated by TroopsController.
            "observed": _j(v.build_queue_json, []),
            # Reference point for the observed queue: `finishes_in_seconds`
            # is time-left at scrape time, so the client adds it to this
            # timestamp to render absolute local-time ETAs.
            "observed_at": (
                v.troops_observed_at.isoformat() if v.troops_observed_at else None
            ),
        },
        "buildings": [
            {"slot": s.slot, "key": s.building_key, "level": s.level}
            for s in slots
        ],
        "troops": {
            "own": own_troops,
            "consumption_per_hour": v.troops_consumption,
            "total": sum(own_troops.values()) if isinstance(own_troops, dict) else 0,
            "observed_at": v.troops_observed_at.isoformat() if v.troops_observed_at else None,
        },
        "movements_in": movements_in,
        "movements_out": movements_out,
        "incoming_attacks": incoming_attacks,
        "incoming_reinforcements": incoming_reinforcements,
        "under_attack": bool(incoming_attacks),
        "missing": missing,
    }


def _o(o: BuildOrder) -> dict:
    return {
        "id": o.id, "building_key": o.building_key,
        "target_level": o.target_level, "slot": o.slot,
        "priority": o.priority, "status": o.status.value,
        "blocked_reason": o.blocked_reason,
        "completes_at": o.completes_at.isoformat() if o.completes_at else None,
    }


class TroopsReserveIn(BaseModel):
    """Per-troop-type minimums to keep home. Keys are t1..t11 (positional);
    values are non-negative ints. Anything in home troops above the reserve
    is available to raids; anything below is untouchable."""
    troops: dict[str, int]


@router.get("/{village_id}/troops_reserve")
async def get_troops_reserve(
    village_id: int, db: AsyncSession = Depends(get_session),
) -> dict:
    v = await db.get(Village, village_id)
    if v is None:
        raise HTTPException(404, "village not found")
    try:
        data = json.loads(v.troops_reserve_json or "{}")
    except Exception:
        data = {}
    return {"troops": data}


@router.patch("/{village_id}/troops_reserve")
async def set_troops_reserve(
    village_id: int,
    payload: TroopsReserveIn,
    db: AsyncSession = Depends(get_session),
) -> dict:
    v = await db.get(Village, village_id)
    if v is None:
        raise HTTPException(404, "village not found")
    cleaned: dict[str, int] = {}
    for k, val in payload.troops.items():
        if not (isinstance(k, str) and k.startswith("t")):
            continue
        try:
            n = int(val)
        except (TypeError, ValueError):
            continue
        if n > 0:
            cleaned[k] = n
    v.troops_reserve_json = json.dumps(cleaned, sort_keys=True)
    await db.commit()
    return {"troops": cleaned}
