"""Control-plane settings (env-driven).

`HERMESKILL_DB_URL` must be set in any environment doing real DB work. Defaults to
a local Postgres 18 instance on Windows dev machines.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HERMESKILL_", env_file=".env", extra="ignore")

    db_url: str = "postgresql+asyncpg://hermeskill:hermeskill@localhost:5432/hermeskill"
    debug: bool = False

    heartbeat_interval_seconds: int = 30
    verification_timeout_seconds: int = 30
    kill_poll_interval_seconds: int = 3

    # Base URL used to compose the one-click feedback URL embedded in
    # each death certificate (M3). Set this to the public origin in prod
    # so the link is clickable from operator email/Slack.
    feedback_base_url: str = "http://localhost:8000"
    # How long an issued feedback token stays valid before lookup 404s.
    feedback_token_ttl_days: int = 30

    # v1 — interactive tool approval bridge
    # TTL for pending tool-approval requests. After expiry the SDK
    # treats the approval as not-granted on the next fetch.
    approval_ttl_seconds: int = 600
    # Lifetime of the runtime grant created when an approval is granted.
    # Short on purpose — the approval is for a *single* tool call, and
    # the next call should re-evaluate against fresh arguments.
    approval_grant_duration_seconds: int = 60
    # Feature flag: when False, the bridge passes through APPROVAL_REQUIRED
    # decisions unchanged (no pending-row creation, no block directive).
    # Default off so a misconfigured bridge never silently relaxes the
    # existing tool_scope check on day-1 deploy.
    interactive_approvals_enabled: bool = False
    # Hard cap on the `arguments_preview` JSONB column size — prevents
    # a misbehaving SDK from blowing out the row.
    approval_argument_preview_max_chars: int = 4096


settings = Settings()
