"""Step 2 (F12.2 + minimal F12.4): agent settings, autonomy overrides, kill-switches,
policy rules, record takeovers, agent runs, decisions, the hash-chained audit log,
the test-only sandbox table, and account tiers.

audit_records is append-only for the app role: UPDATE/DELETE/TRUNCATE are revoked
and a trigger refuses them for every role (an owner must drop the trigger on
purpose to change history, and the chain check would still show it).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.rls import enable_tenant_rls

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = pg.UUID(as_uuid=True)
NOW = sa.text("now()")
TABLES = ["agent_settings", "autonomy_overrides", "kill_switches", "policy_rules",
          "record_takeovers", "agent_runs", "decisions", "audit_records", "sandbox_actions"]


def _id():
    return sa.Column("id", UUID, primary_key=True)


def _tenant():
    return sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False, index=True)


def _ts():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    ]


def _created():
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW,
                     nullable=False)


def upgrade() -> None:
    op.add_column("accounts", sa.Column("tier", sa.String(20)))

    op.create_table(
        "agent_settings", _id(), _tenant(),
        sa.Column("agent_key", sa.String(60), nullable=False),
        sa.Column("autonomy_level", sa.Integer, nullable=False),
        sa.Column("updated_by", sa.String(100), nullable=False),
        *_ts(),
        sa.UniqueConstraint("tenant_id", "agent_key"),
        sa.CheckConstraint("autonomy_level between 0 and 3", name="ck_agent_settings_level"),
    )
    op.create_table(
        "autonomy_overrides", _id(), _tenant(),
        sa.Column("agent_key", sa.String(60), nullable=False, index=True),
        sa.Column("scope", sa.String(15), nullable=False),
        sa.Column("account_tier", sa.String(20)),
        sa.Column("account_id", UUID, sa.ForeignKey("accounts.id")),
        sa.Column("autonomy_level", sa.Integer, nullable=False),
        sa.Column("note", sa.String(300)),
        sa.Column("created_by", sa.String(100), nullable=False),
        *_ts(),
        sa.CheckConstraint("scope in ('account_tier','account')",
                           name="ck_autonomy_overrides_scope"),
        sa.CheckConstraint("autonomy_level between 0 and 3", name="ck_autonomy_overrides_level"),
    )
    op.create_table(
        "kill_switches", _id(), _tenant(),
        sa.Column("scope", sa.String(10), nullable=False),
        sa.Column("agent_key", sa.String(60)),
        sa.Column("account_id", UUID, sa.ForeignKey("accounts.id")),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        _created(),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("released_by", sa.String(100)),
        sa.CheckConstraint("scope in ('tenant','agent','account')", name="ck_kill_switches_scope"),
    )
    op.create_index("ix_kill_switches_active", "kill_switches", ["tenant_id"],
                    postgresql_where=sa.text("released_at IS NULL"))
    op.create_table(
        "policy_rules", _id(), _tenant(),
        sa.Column("agent_key", sa.String(60)),
        sa.Column("tool_key", sa.String(80)),
        sa.Column("conditions", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("outcome", sa.String(15), nullable=False),
        sa.Column("approver_role_id", UUID, sa.ForeignKey("roles.id")),
        sa.Column("note", sa.String(300)),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("updated_by", sa.String(100), nullable=False),
        *_ts(),
        sa.CheckConstraint("outcome in ('allow','ask_person','block')",
                           name="ck_policy_rules_outcome"),
    )
    op.create_table(
        "record_takeovers", _id(), _tenant(),
        sa.Column("record_type", sa.String(20), nullable=False),
        sa.Column("record_id", UUID, nullable=False, index=True),
        sa.Column("taken_by", sa.String(100), nullable=False),
        sa.Column("reason", sa.String(500)),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("released_by", sa.String(100)),
    )
    # At most one active takeover per record.
    op.create_index("uq_record_takeovers_active", "record_takeovers",
                    ["tenant_id", "record_type", "record_id"], unique=True,
                    postgresql_where=sa.text("released_at IS NULL"))
    op.create_table(
        "agent_runs", _id(), _tenant(),
        sa.Column("agent_key", sa.String(60), nullable=False, index=True),
        sa.Column("trigger", sa.String(80), nullable=False),
        sa.Column("trigger_ref", sa.String(200)),
        sa.Column("status", sa.String(15), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("summary", pg.JSONB),
    )
    op.create_table(
        "decisions", _id(), _tenant(),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, index=True),
        sa.Column("agent_key", sa.String(60), nullable=False),
        sa.Column("tool_key", sa.String(80), nullable=False),
        sa.Column("run_id", UUID, sa.ForeignKey("agent_runs.id")),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("recommendation", sa.Text),
        sa.Column("reasons", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("args", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("facts", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("checks", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("approver_role_id", UUID, sa.ForeignKey("roles.id")),
        sa.Column("record_type", sa.String(20)),
        sa.Column("record_id", UUID),
        sa.Column("dedupe_key", sa.String(200), index=True),
        _created(),
        sa.Column("decided_by", sa.String(100)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("note", sa.String(1000)),
        sa.Column("result", pg.JSONB),
        sa.CheckConstraint("kind in ('approval','notice')", name="ck_decisions_kind"),
    )
    op.create_index("ix_decisions_record", "decisions", ["tenant_id", "record_type", "record_id"])
    op.create_table(
        "audit_records",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        _tenant(),
        sa.Column("seq", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False, index=True),
        sa.Column("agent_key", sa.String(60), index=True),
        sa.Column("run_id", UUID, index=True),
        sa.Column("record_type", sa.String(20)),
        sa.Column("record_id", UUID, index=True),
        sa.Column("data", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("tenant_id", "seq"),
    )
    op.create_table(
        "sandbox_actions", _id(), _tenant(),
        sa.Column("account_id", UUID, sa.ForeignKey("accounts.id")),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("note", sa.String(300)),
        sa.Column("created_by", sa.String(100), nullable=False),
        _created(),
    )

    for t in TABLES:
        enable_tenant_rls(op, t)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON " + ", ".join(TABLES) + " TO forge_app")

    # Append-only audit log.
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON audit_records FROM forge_app")
    op.execute("GRANT USAGE ON SEQUENCE audit_records_id_seq TO forge_app")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_records_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'audit_records is append-only (% refused)', TG_OP;
        END $$;
        """
    )
    op.execute(
        "CREATE TRIGGER audit_records_no_update BEFORE UPDATE OR DELETE ON audit_records "
        "FOR EACH ROW EXECUTE FUNCTION audit_records_append_only()"
    )
    op.execute(
        "CREATE TRIGGER audit_records_no_truncate BEFORE TRUNCATE ON audit_records "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_records_append_only()"
    )


def downgrade() -> None:
    for t in reversed(TABLES):
        op.drop_table(t)
    op.execute("DROP FUNCTION IF EXISTS audit_records_append_only()")
    op.drop_column("accounts", "tier")
