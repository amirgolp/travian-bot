from __future__ import annotations

import json
from math import sqrt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import FarmlistIn, FarmlistSlotIn
from app.browser.server import detect_server
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.account import Account
from app.models.farmlist import Farmlist, FarmlistKind, FarmlistSlot
from app.models.map_tile import MapTile
from app.models.village import Village
from app.services import farming

log = get_logger("api.farmlists")
router = APIRouter(prefix="/farmlists", tags=["farmlists"])


@router.post("")
async def create(payload: FarmlistIn, db: AsyncSession = Depends(get_session)):
    try:
        kind = FarmlistKind(payload.kind)
    except ValueError:
        raise HTTPException(400, f"invalid kind: {payload.kind}")
    fl = await farming.create_farmlist(
        db, payload.village_id, payload.name, kind,
        payload.interval_seconds, payload.default_troops,
    )
    await db.commit()
    return {"id": fl.id, "name": fl.name, "kind": fl.kind.value}


@router.get("")
async def list_all(village_id: int | None = None, db: AsyncSession = Depends(get_session)):
    stmt = select(Farmlist)
    if village_id is not None:
        stmt = stmt.where(Farmlist.village_id == village_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [{
        "id": r.id, "village_id": r.village_id, "name": r.name,
        "kind": r.kind.value, "enabled": r.enabled,
        "interval_seconds": r.interval_seconds,
    } for r in rows]


@router.post("/slots")
async def add_slot(payload: FarmlistSlotIn, db: AsyncSession = Depends(get_session)):
    """Attach a MapTile to a farmlist.

    Either pass tile_id directly, or pass (target_x, target_y) and we'll
    look up the tile on the farmlist's village's server.
    """
    tile_id = payload.tile_id
    if tile_id is None:
        if payload.target_x is None or payload.target_y is None:
            raise HTTPException(400, "tile_id or (target_x, target_y) required")
        # Resolve server_code via the farmlist -> village -> account.
        fl = await db.get(Farmlist, payload.farmlist_id)
        if fl is None:
            raise HTTPException(404, "farmlist not found")
        village = await db.get(Village, fl.village_id)
        account = await db.get(Account, village.account_id) if village else None
        if account is None:
            raise HTTPException(404, "account not found")
        server = detect_server(account.server_url)
        tile = (
            await db.execute(
                select(MapTile).where(
                    MapTile.server_code == server.code,
                    MapTile.x == payload.target_x,
                    MapTile.y == payload.target_y,
                )
            )
        ).scalar_one_or_none()
        if tile is None:
            raise HTTPException(
                404,
                f"no MapTile at ({payload.target_x},{payload.target_y}) on {server.code}. "
                "Run world.sql or map scan first.",
            )
        tile_id = tile.id

    slot = await farming.add_slot_for_tile(
        db, payload.farmlist_id, tile_id, payload.troops
    )
    await db.commit()
    log.info("farmlist.slot.added", farmlist_id=payload.farmlist_id, tile_id=tile_id)
    return {"id": slot.id if slot else None, "tile_id": tile_id}


# ---------- detail view ----------

@router.get("/{farmlist_id}")
async def detail(farmlist_id: int, db: AsyncSession = Depends(get_session)) -> dict:
    """Return the farmlist + every slot joined with its MapTile + distance
    from the source village. The UI renders this as a table with one row per
    slot and one column per troop type the user chooses to show."""
    fl = await db.get(Farmlist, farmlist_id)
    if fl is None:
        raise HTTPException(404, "farmlist not found")
    village = await db.get(Village, fl.village_id)
    if village is None:
        raise HTTPException(500, "farmlist points at a village that no longer exists")

    # Single query: slots LEFT JOIN map_tiles so the UI can render coords even
    # if the tile was scrubbed somehow.
    rows = (
        await db.execute(
            select(FarmlistSlot, MapTile)
            .outerjoin(MapTile, MapTile.id == FarmlistSlot.tile_id)
            .where(FarmlistSlot.farmlist_id == farmlist_id)
            .order_by(FarmlistSlot.id.asc())
        )
    ).all()

    def _dist(tx: int, ty: int) -> float:
        dx = tx - village.x
        dy = ty - village.y
        return round(sqrt(dx * dx + dy * dy), 2)

    default_troops = {}
    try:
        default_troops = json.loads(fl.default_troops_json or "{}") or {}
    except Exception:
        default_troops = {}

    slot_rows: list[dict] = []
    for slot, tile in rows:
        troops = default_troops.copy()
        if slot.troops_json:
            try:
                troops.update(json.loads(slot.troops_json) or {})
            except Exception:
                pass
        slot_rows.append({
            "slot_id": slot.id,
            "enabled": slot.enabled,
            "consecutive_losses": slot.consecutive_losses,
            "last_raid_at": slot.last_raid_at.isoformat() if slot.last_raid_at else None,
            "troops": troops,
            "tile": None if tile is None else {
                "id": tile.id, "x": tile.x, "y": tile.y,
                "type": tile.type.value, "name": tile.name,
                "player_name": tile.player_name, "alliance_name": tile.alliance_name,
                "population": tile.population, "oasis_type": tile.oasis_type,
                "raid_count": tile.raid_count, "win_count": tile.win_count,
                "loss_count": tile.loss_count, "empty_count": tile.empty_count,
                "total_bounty": tile.total_bounty,
                "last_raid_outcome": tile.last_raid_outcome,
                "last_raid_capacity_pct": tile.last_raid_capacity_pct,
            },
            "distance": _dist(tile.x, tile.y) if tile is not None else None,
        })

    return {
        "farmlist": {
            "id": fl.id, "village_id": fl.village_id, "name": fl.name,
            "kind": fl.kind.value, "enabled": fl.enabled,
            "interval_seconds": fl.interval_seconds,
            "default_troops": default_troops,
        },
        "source_village": {
            "id": village.id, "name": village.name, "x": village.x, "y": village.y,
        },
        "slots": slot_rows,
    }


class DefaultTroopsIn(BaseModel):
    # {"t1": 3, "t4": 2}; zeros / negatives are dropped before persisting.
    troops: dict[str, int]


@router.patch("/{farmlist_id}/default_troops")
async def set_default_troops(
    farmlist_id: int,
    payload: DefaultTroopsIn,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Set the per-raid troop composition used when a slot has no override.

    Dispatch applies this composition to each enabled slot in turn and
    subtracts from the village's live home-troop count as raids fire, so
    only slots with remaining troops get raids.
    """
    fl = await db.get(Farmlist, farmlist_id)
    if fl is None:
        raise HTTPException(404, "farmlist not found")
    cleaned = {
        k: int(v) for k, v in (payload.troops or {}).items()
        if k.startswith("t") and str(v).lstrip("-").isdigit() and int(v) > 0
    }
    fl.default_troops_json = json.dumps(cleaned, sort_keys=True)
    await db.commit()
    log.info(
        "farmlist.default_troops.set",
        farmlist_id=fl.id, troops=cleaned,
    )
    return {"id": fl.id, "default_troops": cleaned}


class IntervalIn(BaseModel):
    interval_seconds: int


@router.patch("/{farmlist_id}/interval")
async def set_interval(
    farmlist_id: int,
    payload: IntervalIn,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Update how often the farmlist dispatches raids (1x speed seconds)."""
    if payload.interval_seconds < 60:
        raise HTTPException(400, "interval_seconds must be at least 60 (1 min)")
    fl = await db.get(Farmlist, farmlist_id)
    if fl is None:
        raise HTTPException(404, "farmlist not found")
    fl.interval_seconds = payload.interval_seconds
    await db.commit()
    log.info(
        "farmlist.interval.set",
        farmlist_id=fl.id, interval_seconds=fl.interval_seconds,
    )
    return {"id": fl.id, "interval_seconds": fl.interval_seconds}


class SlotToggle(BaseModel):
    enabled: bool


@router.post("/slots/{slot_id}/toggle")
async def toggle_slot(
    slot_id: int, payload: SlotToggle, db: AsyncSession = Depends(get_session),
) -> dict:
    slot = await db.get(FarmlistSlot, slot_id)
    if slot is None:
        raise HTTPException(404, "slot not found")
    slot.enabled = payload.enabled
    if payload.enabled:
        # Re-enabling clears the loss counter so one old streak doesn't
        # immediately re-disable the slot on the next maintenance pass.
        slot.consecutive_losses = 0
    await db.commit()
    return {"id": slot.id, "enabled": slot.enabled}


class ListToggle(BaseModel):
    enabled: bool


@router.post("/{farmlist_id}/toggle")
async def toggle_farmlist(
    farmlist_id: int, payload: ListToggle, db: AsyncSession = Depends(get_session),
) -> dict:
    fl = await db.get(Farmlist, farmlist_id)
    if fl is None:
        raise HTTPException(404, "farmlist not found")
    fl.enabled = payload.enabled
    await db.commit()
    log.info("farmlist.toggle", farmlist_id=fl.id, enabled=fl.enabled)
    return {"id": fl.id, "enabled": fl.enabled}


@router.post("/{farmlist_id}/slots/toggle_all")
async def toggle_all_slots(
    farmlist_id: int, payload: ListToggle, db: AsyncSession = Depends(get_session),
) -> dict:
    """Bulk-flip every slot in this list. Clears loss counters on enable."""
    fl = await db.get(Farmlist, farmlist_id)
    if fl is None:
        raise HTTPException(404, "farmlist not found")
    slots = (
        await db.execute(select(FarmlistSlot).where(FarmlistSlot.farmlist_id == farmlist_id))
    ).scalars().all()
    for s in slots:
        s.enabled = payload.enabled
        if payload.enabled:
            s.consecutive_losses = 0
    await db.commit()
    log.info(
        "farmlist.slots.toggle_all",
        farmlist_id=farmlist_id, enabled=payload.enabled, count=len(slots),
    )
    return {"farmlist_id": farmlist_id, "enabled": payload.enabled, "count": len(slots)}
