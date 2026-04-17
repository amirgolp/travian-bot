from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.hero import HeroStats

router = APIRouter(prefix="/hero", tags=["hero"])


@router.get("")
async def list_hero(
    account_id: int | None = None, db: AsyncSession = Depends(get_session),
):
    import json
    stmt = select(HeroStats)
    if account_id is not None:
        stmt = stmt.where(HeroStats.account_id == account_id)
    rows = (await db.execute(stmt)).scalars().all()

    def _equipment(raw: str | None) -> list[dict]:
        if not raw:
            return []
        try:
            return json.loads(raw)
        except Exception:
            return []

    return [{
        "id": h.id,
        "account_id": h.account_id,
        "health_pct": h.health_pct,
        "experience": h.experience,
        "speed_fph": h.speed_fph,
        "production_per_hour": h.production_per_hour,
        "fighting_strength": h.fighting_strength,
        "off_bonus_pct": h.off_bonus_pct,
        "def_bonus_pct": h.def_bonus_pct,
        "attribute_points": h.attribute_points,
        "home_village_id": h.home_village_id,
        "status": h.status,
        "adventures_available": h.adventures_available,
        "equipment": _equipment(h.equipment_json),
        "bag_count": h.bag_count,
        "bag_items": _equipment(h.bag_items_json),
        "observed_at": h.observed_at.isoformat() if h.observed_at else None,
    } for h in rows]
