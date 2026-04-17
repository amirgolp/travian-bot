"""Global settings loaded from environment / .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(default="postgresql+asyncpg://travian:travian@localhost:5432/travian")
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    secret_key: str = Field(default="")

    browser_profiles_dir: Path = Path("./.profiles")
    headless: bool = True

    action_delay_min: float = 0.8
    action_delay_max: float = 3.2
    default_active_hours: str = "07:30-23:45"
    max_session_minutes: int = 95
    break_minutes_min: int = 12
    break_minutes_max: int = 45

    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.browser_profiles_dir.mkdir(parents=True, exist_ok=True)
    return s
