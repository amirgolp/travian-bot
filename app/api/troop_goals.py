from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.troop_goal import TroopGoal
from app.models.village import Village
from app.services.troop_data import all_troops

router = APIRouter(prefix="/troop_goals", tags=["troop_goals"])


class GoalIn(BaseModel):
    village_id: int
    troop_key: str = Field(..., pattern=r"^t([1-9]|10)$")
    target_count: int = Field(..., ge=0)
    priority: int = 100


class GoalPatch(BaseModel):
    target_count: int | None = None
    priority: int | None = None
    paused: bool | None = None


def _out(g: TroopGoal) -> dict:
    return {
        "id": g.id, "village_id": g.village_id, "troop_key": g.troop_key,
        "target_count": g.target_count, "priority": g.priority, "paused": g.paused,
    }


@router.get("")
async def list_goals(
    village_id: int | None = None, db: AsyncSession = Depends(get_session),
):
    stmt = select(TroopGoal).order_by(TroopGoal.priority.asc(), TroopGoal.id.asc())
    if village_id is not None:
        stmt = stmt.where(TroopGoal.village_id == village_id)
    return [_out(g) for g in (await db.execute(stmt)).scalars().all()]


@router.post("")
async def upsert_goal(payload: GoalIn, db: AsyncSession = Depends(get_session)):
    """Create or update (village_id, troop_key) — the pair is unique."""
    existing = (
        await db.execute(
            select(TroopGoal).where(
                TroopGoal.village_id == payload.village_id,
                TroopGoal.troop_key == payload.troop_key,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        g = TroopGoal(**payload.model_dump())
        db.add(g)
    else:
        existing.target_count = payload.target_count
        existing.priority = payload.priority
        g = existing
    await db.commit()
    await db.refresh(g)
    return _out(g)


@router.patch("/{goal_id}")
async def patch_goal(
    goal_id: int, payload: GoalPatch, db: AsyncSession = Depends(get_session),
):
    g = await db.get(TroopGoal, goal_id)
    if g is None:
        raise HTTPException(404, "goal not found")
    if payload.target_count is not None:
        g.target_count = payload.target_count
    if payload.priority is not None:
        g.priority = payload.priority
    if payload.paused is not None:
        g.paused = payload.paused
    await db.commit()
    await db.refresh(g)
    return _out(g)


@router.delete("/{goal_id}")
async def delete_goal(goal_id: int, db: AsyncSession = Depends(get_session)):
    g = await db.get(TroopGoal, goal_id)
    if g is None:
        raise HTTPException(404, "goal not found")
    await db.delete(g)
    await db.commit()
    return {"ok": True}


@router.get("/catalog")
async def catalog(village_id: int | None = None, db: AsyncSession = Depends(get_session)):
    """Return the troop roster (t1..t10) resolved to tribe-specific names.

    Pass `?village_id=` to get the names for that village's tribe; omit for
    the generic fallback list.
    """
    tribe = None
    if village_id is not None:
        v = await db.get(Village, village_id)
        if v is not None and v.tribe is not None:
            tribe = v.tribe.value
    return {"tribe": tribe, "troops": all_troops(tribe)}
