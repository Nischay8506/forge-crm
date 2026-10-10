"""Application settings, read from environment variables or backend/.env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Forge CRM"
    environment: str = "development"

    # The app connects as a role WITHOUT superuser/BYPASSRLS so row-level security applies.
    database_url: str = "postgresql+asyncpg://forge_app:forge_app@localhost:5432/forge"
    # Migrations run as the schema owner.
    migration_database_url: str = (
        "postgresql+asyncpg://forge_owner:forge_owner@localhost:5432/forge"
    )
    # Password Alembic gives the forge_app role when it creates it.
    app_db_password: str = "forge_app"

    redis_url: str = "redis://localhost:6379/0"

    jwt_secret_key: str = "change-me-in-env"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    refresh_token_days: int = 7

    cors_origins: list[str] = ["http://localhost:5173"]

    # LLM gateway (decision 3C): provider keys; routing lives in app/llm/routing.py
    groq_api_key: str | None = None
    gemini_api_key: str | None = None
    anthropic_api_key: str | None = None
    llm_default_monthly_budget_usd: float = 50.0

    # Data Steward (F12.1). Automatic merges go through the policy gate (Step 2): stop
    # or slow them on the Agents screen, not here. Every merge can be undone.
    # Decision S3: "b" would also auto-merge close name + same city/pincode without IDs.
    steward_name_only_auto_merge: bool = False
    steward_candidate_limit: int = 200
    # Decision S2A: meaning match through the gateway's "embedding" tier (review only).
    # Runs only when a key for that tier is set (GEMINI_API_KEY).
    steward_embeddings: bool = True
    steward_embedding_neighbours: int = 10
    import_max_rows: int = 20000

    # Integrations (F12.6). Keys that encrypt stored ERP credentials (decision E8):
    # "id:base64key,..." with the current key first; see app/core/secrets.py.
    credential_keys: str | None = None
    integration_poll_minutes: int = 5  # decision E6
    integration_lag_alert_minutes: int = 15
    # A revoked key or missing permission alerts at once; the ERP being unreachable or
    # a sync failing for a reason that may pass alerts only once it lasts this long.
    integration_unreachable_alert_minutes: int = 3
    integration_failures_before_alert: int = 3
    integration_stuck_run_minutes: int = 30
    integration_reconcile_hour: int = 2  # nightly comparison, local time below
    integration_local_time_zone: str = "Asia/Kolkata"
    # Where this backend is reached from outside; names the MCP server (decision E9).
    public_base_url: str = "http://localhost:8000"

    # Audit trail (F12.4). Keys that sign the hourly seals (decision R3 A), same
    # "id:base64key,..." form as above; see app/audit/seals.py. Unset = seals off.
    audit_seal_keys: str | None = None
    # Encrypts the audit trail's evidence (Step 4 safeguard 1); see app/audit/evidence.py.
    # Required: without it nothing that writes to the audit trail can run.
    evidence_keys: str | None = None

    # Browser push notifications for the approval inbox (decision H5). Make a key pair
    # with `uv run python -m app.cli new-push-keys`; unset = push is off.
    vapid_public_key: str | None = None
    vapid_private_key: str | None = None
    vapid_subject: str = "mailto:admin@example.com"

    # Step 6 (F12.5): where people open Forge in a browser (links in notification
    # emails), and how long a message may sit in "sending" before it counts as lost.
    app_base_url: str = "http://localhost:5173"
    channels_stuck_sending_minutes: int = 10

    outbox_batch_size: int = 100
    outbox_max_attempts: int = 8


@lru_cache
def get_settings() -> Settings:
    return Settings()
