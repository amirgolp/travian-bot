from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.report import Report, ReportType

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("")
async def list_reports(
    account_id: int | None = None,
    tile_id: int | None = None,
    type: str | None = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_session),
):
    stmt = select(Report).order_by(desc(Report.id))
    if account_id is not None:
        stmt = stmt.where(Report.account_id == account_id)
    if tile_id is not None:
        stmt = stmt.where(Report.tile_id == tile_id)
    if type:
        try:
            stmt = stmt.where(Report.type == ReportType(type))
        except ValueError:
            pass
    stmt = stmt.limit(min(max(1, limit), 2000))
    rows = (await db.execute(stmt)).scalars().all()
    return [{
        "id": r.id,
        "account_id": r.account_id,
        "tile_id": r.tile_id,
        "type": r.type.value,
        "when": r.when.isoformat() if r.when else None,
        "target_x": r.target_x, "target_y": r.target_y,
        "bounty_total": r.bounty_total,
        "bounty": {"wood": r.bounty_wood, "clay": r.bounty_clay,
                   "iron": r.bounty_iron, "crop": r.bounty_crop},
        "capacity_used_pct": r.capacity_used_pct,
    } for r in rows]
