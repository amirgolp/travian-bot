from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.map_tile import MapTile, TileType

router = APIRouter(prefix="/map/tiles", tags=["map"])


@router.get("")
async def list_tiles(
    server_code: str | None = None,
    type: str | None = None,
    min_raids: int = 0,
    limit: int = 500,
    order_by: str = "raid_count",  # raid_count | total_bounty | last_raid_at
    db: AsyncSession = Depends(get_session),
):
    """Browse MapTiles. Defaults to the 500 most-raided tiles across all servers."""
    stmt = select(MapTile)
    if server_code:
        stmt = stmt.where(MapTile.server_code == server_code)
    if type:
        try:
            stmt = stmt.where(MapTile.type == TileType(type))
        except ValueError:
            pass
    if min_raids:
        stmt = stmt.where(MapTile.raid_count >= min_raids)

    order_col = {
        "raid_count": MapTile.raid_count,
        "total_bounty": MapTile.total_bounty,
        "last_raid_at": MapTile.last_raid_at,
    }.get(order_by, MapTile.raid_count)
    stmt = stmt.order_by(desc(order_col)).limit(min(max(1, limit), 5000))

    rows = (await db.execute(stmt)).scalars().all()
    return [{
        "id": t.id, "server_code": t.server_code,
        "x": t.x, "y": t.y, "type": t.type.value,
        "name": t.name, "tribe": t.tribe,
        "population": t.population,
        "player_name": t.player_name, "alliance_name": t.alliance_name,
        "oasis_type": t.oasis_type,
        "raid_count": t.raid_count, "win_count": t.win_count,
        "loss_count": t.loss_count, "empty_count": t.empty_count,
        "total_bounty": t.total_bounty,
        "last_raid_at": t.last_raid_at.isoformat() if t.last_raid_at else None,
    } for t in rows]
