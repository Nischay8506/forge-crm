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

    # Data Steward (F12.1). Until the policy gate exists (Step 2) automatic merges
    # are controlled by this switch; every merge is recorded and can be undone.
    steward_auto_merge: bool = True
    # Decision S3: "b" would also auto-merge close name + same city/pincode without IDs.
    steward_name_only_auto_merge: bool = False
    steward_candidate_limit: int = 200
    # Decision S2A: meaning match through the gateway's "embedding" tier (review only).
    # Runs only when a key for that tier is set (GEMINI_API_KEY).
    steward_embeddings: bool = True
    steward_embedding_neighbours: int = 10
    import_max_rows: int = 20000

    outbox_batch_size: int = 100
    outbox_max_attempts: int = 8


@lru_cache
def get_settings() -> Settings:
    return Settings()
