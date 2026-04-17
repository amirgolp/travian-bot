from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import AccountIn, AccountOut
from app.browser.humanize import parse_active_hours
from app.browser.server import detect_server
from app.core.account_manager import get_manager
from app.core.crypto import encrypt
from app.db.session import get_session
from app.models.account import Account, AccountStatus

# Canonical ordered list of controllers — the UI shows them in this order.
# Keep in sync with app/core/account_manager.py::build_controllers.
ALL_CONTROLLERS = [
    "villages",
    "hero",
    "troops",
    "reports",
    "maintenance",
    "farming",
    "building",
    "training",
    "world_sql",
    "map_scan",
]

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("", response_model=AccountOut)
async def create_account(payload: AccountIn, db: AsyncSession = Depends(get_session)) -> Account:
    try:
        info = detect_server(payload.server_url)
    except ValueError as e:
        raise HTTPException(400, str(e))

    acc = Account(
        label=payload.label,
        server_url=info.url,
        server_code=info.code,
        username=payload.username,
        password_encrypted=encrypt(payload.password),
        active_hours=payload.active_hours,
        status=AccountStatus.ACTIVE,
    )
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    return acc


@router.get("", response_model=list[AccountOut])
async def list_accounts(db: AsyncSession = Depends(get_session)) -> list[Account]:
    rows = (await db.execute(select(Account))).scalars().all()
    return list(rows)


class AccountPatch(BaseModel):
    # Only fields listed here are editable; all optional.
    active_hours: str | None = None


@router.patch("/{account_id}", response_model=AccountOut)
async def patch_account(
    account_id: int,
    payload: AccountPatch,
    db: AsyncSession = Depends(get_session),
) -> Account:
    acc = await db.get(Account, account_id)
    if acc is None:
        raise HTTPException(404, "account not found")
    if payload.active_hours is not None:
        try:
            parse_active_hours(payload.active_hours)
        except ValueError as e:
            raise HTTPException(400, f"invalid active_hours: {e}")
        acc.active_hours = payload.active_hours
    await db.commit()
    await db.refresh(acc)
    # Outer session loop re-reads account.active_hours each tick, so the change
    # takes effect on the next wake without needing to bounce the worker.
    return acc


@router.post("/{account_id}/start")
async def start_account(account_id: int) -> dict:
    await get_manager().start(account_id)
    return {"ok": True}


@router.post("/{account_id}/stop")
async def stop_account(account_id: int) -> dict:
    await get_manager().stop(account_id)
    return {"ok": True}


@router.post("/start_all")
async def start_all(db: AsyncSession = Depends(get_session)) -> dict:
    """Start a worker for every ACTIVE account that isn't already running."""
    rows = (
        await db.execute(select(Account).where(Account.status == AccountStatus.ACTIVE))
    ).scalars().all()
    mgr = get_manager()
    for a in rows:
        await mgr.start(a.id)
    return {"ok": True, "started": len(rows)}


@router.post("/stop_all")
async def stop_all() -> dict:
    mgr = get_manager()
    ids = list(mgr.status().keys())
    for aid in ids:
        await mgr.stop(aid)
    return {"ok": True, "stopped": len(ids)}


@router.get("/status")
async def status() -> dict:
    return {"workers": get_manager().status()}


class ControllerToggles(BaseModel):
    # All known controller names with their enabled flag.
    enabled: dict[str, bool]


@router.get("/{account_id}/controllers")
async def list_controllers(
    account_id: int, db: AsyncSession = Depends(get_session),
) -> dict:
    acc = await db.get(Account, account_id)
    if acc is None:
        raise HTTPException(404, "account not found")
    disabled = set(json.loads(acc.disabled_controllers or "[]") or [])
    return {"enabled": {name: name not in disabled for name in ALL_CONTROLLERS}}


@router.post("/{account_id}/controllers")
async def set_controllers(
    account_id: int,
    payload: ControllerToggles,
    db: AsyncSession = Depends(get_session),
) -> dict:
    acc = await db.get(Account, account_id)
    if acc is None:
        raise HTTPException(404, "account not found")
    # Unknown names are ignored; missing names keep whatever state they had.
    cur_disabled = set(json.loads(acc.disabled_controllers or "[]") or [])
    for name, on in payload.enabled.items():
        if name not in ALL_CONTROLLERS:
            continue
        if on:
            cur_disabled.discard(name)
        else:
            cur_disabled.add(name)
    acc.disabled_controllers = json.dumps(sorted(cur_disabled))
    await db.commit()
    # Hot-apply to any running worker.
    get_manager().apply_toggles(account_id, cur_disabled)
    return {"enabled": {name: name not in cur_disabled for name in ALL_CONTROLLERS}}


class FeatureToggles(BaseModel):
    # Named feature flags — grows over time. Only the keys sent are updated.
    watch_video_bonuses: bool | None = None


@router.get("/{account_id}/features")
async def list_features(
    account_id: int, db: AsyncSession = Depends(get_session),
) -> dict:
    acc = await db.get(Account, account_id)
    if acc is None:
        raise HTTPException(404, "account not found")
    return {"watch_video_bonuses": bool(acc.watch_video_bonuses)}


@router.post("/{account_id}/features")
async def set_features(
    account_id: int,
    payload: FeatureToggles,
    db: AsyncSession = Depends(get_session),
) -> dict:
    acc = await db.get(Account, account_id)
    if acc is None:
        raise HTTPException(404, "account not found")
    if payload.watch_video_bonuses is not None:
        acc.watch_video_bonuses = payload.watch_video_bonuses
    await db.commit()
    # Note: no live worker push needed. BuildPage reads the flag per tick from
    # the account row, so the next build tick picks up the new value.
    return {"watch_video_bonuses": bool(acc.watch_video_bonuses)}
