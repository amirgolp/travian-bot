"""Strategy API router tests — in-memory SQLite + FastAPI test client."""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.strategy import router as strategy_router
from app.db.session import get_session
from app.models import Village


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """FastAPI app wired to the in-memory DB fixture."""
    app = FastAPI()
    app.include_router(strategy_router)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_list_strategies_includes_bundled_ark1(client: AsyncClient) -> None:
    resp = await client.get("/strategy/list")
    assert resp.status_code == 200
    entries = resp.json()
    by_name = {e["name"]: e for e in entries}
    assert "x10_egyptian_eco_ark1" in by_name
    entry = by_name["x10_egyptian_eco_ark1"]
    assert entry["tribe"] == "egyptian"
    assert entry["server_speed"] == 10
    assert entry["steps"] > 40


async def test_apply_then_list_and_resolve_gate(
    client: AsyncClient, sample_village: Village
) -> None:
    resp = await client.post(
        f"/strategy/villages/{sample_village.id}/apply",
        json={"strategy_name": "x10_egyptian_eco_ark1"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["gates_inserted"] == 3
    assert body["hero_policy_written"] is True

    # Gates listed for this village.
    resp = await client.get(f"/strategy/villages/{sample_village.id}/gates")
    assert resp.status_code == 200
    gates = resp.json()
    assert [g["step"] for g in gates] == [26, 43, 60]
    assert all(g["status"] == "pending" for g in gates)

    # Filter by status.
    resp = await client.get(
        f"/strategy/villages/{sample_village.id}/gates", params={"status": "resolved"}
    )
    assert resp.status_code == 200
    assert resp.json() == []

    # Resolve the first gate.
    first_id = gates[0]["id"]
    resp = await client.post(
        f"/strategy/gates/{first_id}/resolve",
        json={"note": "team leader picked raid mode"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"
    assert resp.json()["resolution_note"] == "team leader picked raid mode"

    # Re-resolving a resolved gate is a 409.
    resp = await client.post(f"/strategy/gates/{first_id}/resolve", json={"note": None})
    assert resp.status_code == 409


async def test_apply_unknown_strategy_is_404(
    client: AsyncClient, sample_village: Village
) -> None:
    resp = await client.post(
        f"/strategy/villages/{sample_village.id}/apply",
        json={"strategy_name": "does_not_exist"},
    )
    assert resp.status_code == 404


async def test_apply_to_missing_village_is_404(client: AsyncClient) -> None:
    resp = await client.post(
        "/strategy/villages/9999/apply",
        json={"strategy_name": "x10_egyptian_eco_ark1"},
    )
    assert resp.status_code == 404


async def test_skip_gate_transitions_and_is_idempotent(
    client: AsyncClient, sample_village: Village
) -> None:
    await client.post(
        f"/strategy/villages/{sample_village.id}/apply",
        json={"strategy_name": "x10_egyptian_eco_ark1"},
    )
    gates = (await client.get(f"/strategy/villages/{sample_village.id}/gates")).json()
    gate_id = gates[-1]["id"]

    resp = await client.post(f"/strategy/gates/{gate_id}/skip", json={"note": None})
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"

    resp = await client.post(f"/strategy/gates/{gate_id}/skip", json={"note": None})
    assert resp.status_code == 409


async def test_resolve_unknown_gate_is_404(client: AsyncClient) -> None:
    resp = await client.post("/strategy/gates/99999/resolve", json={"note": None})
    assert resp.status_code == 404
