from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import BuildOrderIn, BuildOrderOut, ReorderIn
from app.db.session import get_session
from app.models.build import BuildOrder
from app.services import building
from app.services.building_data import load_buildings

router = APIRouter(prefix="/build", tags=["build"])


@router.get("/catalog")
async def catalog():
    """Return all known buildings + their prereqs — useful for a UI/picker."""
    return {k: {
        "gid": d.gid, "name": d.name, "category": d.category,
        "placement": d.placement, "max_level": d.max_level,
        "unique": d.unique,
        "prereqs": [{"key": p.key, "level": p.level} for p in d.prereqs],
    } for k, d in load_buildings().items()}


@router.post("/orders", response_model=BuildOrderOut)
async def create_order(payload: BuildOrderIn, db: AsyncSession = Depends(get_session)):
    order = await building.queue_upgrade(
        db, payload.village_id, payload.building_key,
        payload.target_level, payload.slot, payload.priority,
    )
    await db.commit()
    await db.refresh(order)
    return order


@router.get("/orders", response_model=list[BuildOrderOut])
async def list_orders(village_id: int, db: AsyncSession = Depends(get_session)):
    rows = (
        await db.execute(
            select(BuildOrder)
            .where(BuildOrder.village_id == village_id)
            .order_by(BuildOrder.priority.asc(), BuildOrder.id.asc())
        )
    ).scalars().all()
    return list(rows)


@router.delete("/orders/{order_id}")
async def delete_order(order_id: int, db: AsyncSession = Depends(get_session)):
    await building.cancel_order(db, order_id)
    await db.commit()
    return {"ok": True}


@router.post("/reorder")
async def reorder(payload: ReorderIn, db: AsyncSession = Depends(get_session)):
    await building.reorder(db, payload.village_id, payload.ordered_ids)
    await db.commit()
    return {"ok": True}
