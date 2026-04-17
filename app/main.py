"""FastAPI entrypoint — wires routes, starts the AccountManager on boot."""
from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import (
    accounts, build, farmlists, hero, map_tiles, reports, troop_goals, villages,
)
from app.core.account_manager import get_manager
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import engine
from app.models import Base

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    async with engine.begin() as conn:
        # Minimal create_all for local dev. Production should use Alembic.
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight in-place migrations — idempotent ALTER TABLEs for columns
        # we added after the first release. Cheaper than pulling in Alembic for
        # what is still a one-person hobby DB.
        await conn.execute(text(
            "ALTER TABLE accounts "
            "ADD COLUMN IF NOT EXISTS disabled_controllers TEXT DEFAULT '[]'"
        ))
        await conn.execute(text(
            "ALTER TABLE accounts "
            "ADD COLUMN IF NOT EXISTS watch_video_bonuses BOOLEAN DEFAULT TRUE"
        ))
        # active_hours widened from VARCHAR(16) to VARCHAR(128) to fit
        # multi-window specs like "09:00-13:00,14:00-22:00,23:00-08:00".
        await conn.execute(text(
            "ALTER TABLE accounts "
            "ALTER COLUMN active_hours TYPE VARCHAR(128)"
        ))
        for col_ddl in (
            "ADD COLUMN IF NOT EXISTS troops_json TEXT DEFAULT '{}'",
            "ADD COLUMN IF NOT EXISTS movements_in_json TEXT DEFAULT '[]'",
            "ADD COLUMN IF NOT EXISTS movements_out_json TEXT DEFAULT '[]'",
            "ADD COLUMN IF NOT EXISTS troops_consumption INTEGER DEFAULT 0",
            "ADD COLUMN IF NOT EXISTS troops_observed_at TIMESTAMPTZ",
            "ADD COLUMN IF NOT EXISTS build_queue_json TEXT DEFAULT '[]'",
            "ADD COLUMN IF NOT EXISTS troops_reserve_json TEXT DEFAULT '{}'",
        ):
            await conn.execute(text(f"ALTER TABLE villages {col_ddl}"))
        for col_ddl in (
            "ADD COLUMN IF NOT EXISTS equipment_json TEXT DEFAULT '[]'",
            "ADD COLUMN IF NOT EXISTS bag_count INTEGER DEFAULT 0",
            "ADD COLUMN IF NOT EXISTS bag_items_json TEXT DEFAULT '[]'",
        ):
            await conn.execute(text(f"ALTER TABLE hero_stats {col_ddl}"))
        for col_ddl in (
            "ADD COLUMN IF NOT EXISTS last_raid_outcome VARCHAR(16)",
            "ADD COLUMN IF NOT EXISTS last_raid_capacity_pct INTEGER",
            "ADD COLUMN IF NOT EXISTS animals_json VARCHAR(256)",
            "ADD COLUMN IF NOT EXISTS animals_checked_at TIMESTAMPTZ",
        ):
            await conn.execute(text(f"ALTER TABLE map_tiles {col_ddl}"))
        # Reports gained source_village_id so we can match a report back to
        # the farmlist slot that fired it (same village + same target tile).
        await conn.execute(text(
            "ALTER TABLE reports "
            "ADD COLUMN IF NOT EXISTS source_village_id INTEGER "
            "REFERENCES villages(id) ON DELETE SET NULL"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_reports_source_village_id "
            "ON reports (source_village_id)"
        ))
        await conn.execute(text("SELECT 1"))
    # Workers stay stopped at boot — the user starts them from the dashboard.
    log.info("app.startup")
    try:
        yield
    finally:
        await get_manager().stop_all()
        await engine.dispose()
        log.info("app.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(title="travian-bot", version="0.1.0", lifespan=lifespan)
    # Permissive CORS for local dashboard use — tighten in prod if ever exposed.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )
    app.include_router(accounts.router)
    app.include_router(villages.router)
    app.include_router(farmlists.router)
    app.include_router(build.router)
    app.include_router(map_tiles.router)
    app.include_router(reports.router)
    app.include_router(hero.router)
    app.include_router(troop_goals.router)

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    return app


app = create_app()


def run() -> None:
    s = get_settings()
    uvicorn.run("app.main:app", host=s.api_host, port=s.api_port, reload=False)


if __name__ == "__main__":
    run()
