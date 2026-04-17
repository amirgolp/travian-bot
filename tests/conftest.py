"""Shared test fixtures — in-memory async SQLite for DB-touching tests."""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Account, AccountStatus, Base, Village


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Fresh in-memory SQLite DB with the full schema created — one per test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def sample_village(db_session: AsyncSession) -> Village:
    """A minimal Account + Village pair; returns the Village with a real id."""
    account = Account(
        label="test",
        server_url="https://ts1.example.com",
        server_code="test-ts1",
        username="u",
        password_encrypted="x",
        status=AccountStatus.ACTIVE,
    )
    db_session.add(account)
    await db_session.flush()
    village = Village(
        account_id=account.id,
        travian_id=12345,
        name="Main",
        x=0,
        y=0,
        is_capital=True,
    )
    db_session.add(village)
    await db_session.flush()
    return village
