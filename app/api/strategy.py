"""Strategy API: apply a bundled strategy YAML to a village and manage gates."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    StrategyApplyIn,
    StrategyApplyOut,
    StrategyGateOut,
    StrategyGateResolveIn,
)
from app.db.session import get_session
from app.models.strategy_gate import StrategyGate, StrategyGateStatus
from app.models.village import Village
from app.services.strategy import (
    apply_compiled_strategy,
    compile_strategy,
    get_strategy,
)

router = APIRouter(prefix="/strategy", tags=["strategy"])

_STRATEGIES_DIR = Path(__file__).resolve().parents[1] / "data" / "strategies"


@router.get("/list")
async def list_strategies() -> list[dict]:
    """List bundled strategy files (stem + short metadata).

    Scans ``app/data/strategies/*.yaml`` and parses each one's ``meta``
    block so the dashboard can present a picker without re-reading files.
    """
    out: list[dict] = []
    for yml in sorted(_STRATEGIES_DIR.glob("*.yaml")):
        try:
            s = get_strategy(yml.stem)
        except Exception as exc:
            out.append({"name": yml.stem, "error": str(exc)})
            continue
        out.append({
            "name": yml.stem,
            "display_name": s.meta.name,
            "tribe": s.meta.tribe,
            "server_speed": s.meta.server_speed,
            "goal": s.meta.goal,
            "source": s.meta.source,
            "steps": len(s.build),
        })
    return out


@router.post("/villages/{village_id}/apply", response_model=StrategyApplyOut)
async def apply_to_village(
    village_id: int,
    payload: StrategyApplyIn,
    db: AsyncSession = Depends(get_session),
) -> StrategyApplyOut:
    village = await db.get(Village, village_id)
    if village is None:
        raise HTTPException(404, f"village {village_id} not found")
    try:
        strategy = get_strategy(payload.strategy_name)
    except FileNotFoundError as exc:
        raise HTTPException(404, f"strategy {payload.strategy_name!r} not found") from exc
    compiled = compile_strategy(strategy)
    result = await apply_compiled_strategy(db, village_id, compiled)
    await db.commit()
    return StrategyApplyOut(
        strategy_name=payload.strategy_name,
        build_orders_inserted=result.build_orders_inserted,
        troop_goals_upserted=result.troop_goals_upserted,
        gates_inserted=result.gates_inserted,
        hero_policy_written=result.hero_policy_written,
    )


@router.get("/villages/{village_id}/gates", response_model=list[StrategyGateOut])
async def list_gates(
    village_id: int,
    status: str | None = None,
    db: AsyncSession = Depends(get_session),
) -> list[StrategyGate]:
    """List gates for a village, optionally filtered by status (pending/resolved/skipped)."""
    stmt = select(StrategyGate).where(StrategyGate.village_id == village_id)
    if status is not None:
        try:
            stmt = stmt.where(StrategyGate.status == StrategyGateStatus(status))
        except ValueError as exc:
            raise HTTPException(400, f"invalid status {status!r}") from exc
    stmt = stmt.order_by(StrategyGate.priority.asc(), StrategyGate.id.asc())
    return list((await db.scalars(stmt)).all())


async def _transition_gate(
    db: AsyncSession, gate_id: int, new_status: StrategyGateStatus, note: str | None
) -> StrategyGate:
    gate = await db.get(StrategyGate, gate_id)
    if gate is None:
        raise HTTPException(404, f"gate {gate_id} not found")
    if gate.status != StrategyGateStatus.PENDING:
        raise HTTPException(
            409, f"gate {gate_id} is already {gate.status.value}"
        )
    gate.status = new_status
    gate.resolution_note = note
    gate.resolved_at = datetime.now(tz=UTC)
    await db.commit()
    await db.refresh(gate)
    return gate


@router.post("/gates/{gate_id}/resolve", response_model=StrategyGateOut)
async def resolve_gate(
    gate_id: int,
    payload: StrategyGateResolveIn,
    db: AsyncSession = Depends(get_session),
) -> StrategyGate:
    return await _transition_gate(db, gate_id, StrategyGateStatus.RESOLVED, payload.note)


@router.post("/gates/{gate_id}/skip", response_model=StrategyGateOut)
async def skip_gate(
    gate_id: int,
    payload: StrategyGateResolveIn,
    db: AsyncSession = Depends(get_session),
) -> StrategyGate:
    return await _transition_gate(db, gate_id, StrategyGateStatus.SKIPPED, payload.note)
