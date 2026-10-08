"""Step 0 foundation: tenants, users, roles, outbox, LLM usage; app role and RLS.

Revision ID: 0001
Revises:
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.config import get_settings
from app.rls import enable_tenant_rls

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = pg.UUID(as_uuid=True)
NOW = sa.text("now()")
TENANT_TABLES = ["users", "roles", "user_roles", "outbox_events", "llm_calls", "tenant_llm_budgets"]


def _ts():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    ]


def _tenant_fk():
    return sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False, index=True)


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(63), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        *_ts(),
    )
    op.create_table(
        "users",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk(),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        *_ts(),
        sa.UniqueConstraint("tenant_id", "email"),
    )
    op.create_table(
        "roles",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk(),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500)),
        sa.Column("permissions", pg.ARRAY(sa.String(100)), nullable=False, server_default="{}"),
        *_ts(),
        sa.UniqueConstraint("tenant_id", "name"),
    )
    op.create_table(
        "user_roles",
        _tenant_fk(),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", UUID, sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk(),
        sa.Column("event_type", sa.String(100), nullable=False, index=True),
        sa.Column("payload", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending", index=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "llm_calls",
        sa.Column("id", UUID, primary_key=True),
        _tenant_fk(),
        sa.Column("task", sa.String(100), nullable=False),
        sa.Column("tier", sa.String(30), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.String(1000)),
        sa.Column("trace_id", sa.String(64), index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )
    op.create_table(
        "tenant_llm_budgets",
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("monthly_budget_usd", sa.Numeric(12, 2), nullable=False),
    )

    for table in TENANT_TABLES:
        enable_tenant_rls(op, table, allow_system=(table == "outbox_events"))

    # The application role: no superuser, no BYPASSRLS, so the policies above apply to it.
    pw = get_settings().app_db_password.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'forge_app') THEN
            CREATE ROLE forge_app LOGIN PASSWORD '{pw}' NOSUPERUSER NOBYPASSRLS;
          END IF;
        END $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO forge_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO forge_app")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO forge_app"
    )
    # The app may read tenants (login) but not create or change them.
    op.execute("REVOKE INSERT, UPDATE, DELETE ON tenants FROM forge_app")


def downgrade() -> None:
    for table in reversed(["tenants", *TENANT_TABLES]):
        op.drop_table(table)
