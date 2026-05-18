"""Control-plane settings (env-driven).

`STASIS_DB_URL` must be set in any environment doing real DB work. Defaults to
a local Postgres 18 instance on Windows dev machines.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STASIS_", env_file=".env", extra="ignore")

    db_url: str = "postgresql+asyncpg://stasis:stasis@localhost:5432/stasis"
    debug: bool = False

    heartbeat_interval_seconds: int = 30
    verification_timeout_seconds: int = 30
    kill_poll_interval_seconds: int = 3


settings = Settings()
