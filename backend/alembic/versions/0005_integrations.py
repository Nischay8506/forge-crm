"""Step 3 (F12.6): ERP connections, sync flows and runs, dead letters, the synced ERP
records (one table per shared contract, decision E2), ERP writes with idempotency
keys, the mapping studio's code maps (with an append-only history), Forge's own code
lists, and MCP client tokens (decision E9).

erp_connections and sync_flows also allow the platform scheduler (app.system) to see
rows of every tenant, so one beat task can find what is due.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-09
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.rls import enable_tenant_rls

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = pg.UUID(as_uuid=True)
JSONB = pg.JSONB
NOW = sa.text("now()")
TS = sa.DateTime(timezone=True)
MONEY = sa.Numeric(18, 2)
QTY = sa.Numeric(18, 4)

SYSTEM_TABLES = ["erp_connections", "sync_flows"]
RECORD_TABLES = ["erp_customers", "erp_items", "erp_prices", "erp_orders", "erp_invoices",
                 "erp_receipts", "erp_stock"]
TABLES = [*SYSTEM_TABLES, "sync_runs", "sync_dead_letters", *RECORD_TABLES, "erp_writes",
          "forge_codes", "code_maps", "code_map_history", "mcp_clients"]


def _id():
    return sa.Column("id", UUID, primary_key=True)


def _tenant():
    return sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False, index=True)


def _ts():
    return [sa.Column("created_at", TS, server_default=NOW, nullable=False),
            sa.Column("updated_at", TS, server_default=NOW, nullable=False)]


def _conn():
    return sa.Column("connection_id", UUID, sa.ForeignKey("erp_connections.id"), nullable=False,
                     index=True)


def _record_cols():
    """What every synced ERP record carries (app/models/integrations.py ErpRecordMixin)."""
    return [
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("modified_at", TS, nullable=False),
        sa.Column("synced_at", TS, server_default=NOW, nullable=False),
        sa.Column("gone_at", TS),  # set when the nightly comparison no longer finds it
        sa.Column("disabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("data", JSONB, nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "erp_connections", _id(), _tenant(),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("settings", JSONB, nullable=False, server_default="{}"),
        sa.Column("api_key_enc", sa.Text, nullable=False),
        sa.Column("api_secret_enc", sa.Text, nullable=False),
        sa.Column("secret_hint", sa.String(12), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("last_check_at", TS),
        sa.Column("last_check_ok", sa.Boolean),
        sa.Column("last_check_cause", sa.String(30)),
        sa.Column("last_check_message", sa.String(500)),
        sa.Column("last_ok_at", TS),
        sa.Column("about", JSONB, nullable=False, server_default="{}"),
        sa.Column("webhook_token_hash", sa.String(64)),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("updated_by", sa.String(100), nullable=False),
        *_ts(),
        sa.UniqueConstraint("tenant_id", "name"),
        sa.CheckConstraint("status in ('active','paused')", name="ck_erp_connections_status"),
    )
    op.create_table(
        "sync_flows", _id(), _tenant(), _conn(),
        sa.Column("entity", sa.String(20), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("state", JSONB, nullable=False, server_default="{}"),
        sa.Column("running_since", TS),
        sa.Column("last_started_at", TS),
        sa.Column("last_finished_at", TS),
        sa.Column("last_success_at", TS),
        sa.Column("last_error_cause", sa.String(30)),
        sa.Column("last_error_message", sa.String(500)),
        sa.Column("consecutive_failures", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_read", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("reconciled_at", TS),
        sa.Column("last_reconcile", JSONB),
        sa.Column("run_requested_at", TS),
        *_ts(),
        sa.UniqueConstraint("connection_id", "entity"),
    )
    op.create_table(
        "sync_runs", _id(), _tenant(),
        sa.Column("flow_id", UUID, sa.ForeignKey("sync_flows.id"), nullable=False, index=True),
        sa.Column("kind", sa.String(12), nullable=False),  # sync | reconcile
        sa.Column("trigger", sa.String(12), nullable=False),  # schedule | manual | webhook | setup
        # running | succeeded | partial | failed
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("started_at", TS, server_default=NOW, nullable=False, index=True),
        sa.Column("finished_at", TS),
        sa.Column("read", sa.Integer, nullable=False, server_default="0"),
        sa.Column("problems", sa.Integer, nullable=False, server_default="0"),
        sa.Column("retries", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_cause", sa.String(30)),
        sa.Column("error_message", sa.String(500)),
        sa.Column("details", JSONB, nullable=False, server_default="{}"),
    )
    op.create_table(
        "sync_dead_letters", _id(), _tenant(), _conn(),
        sa.Column("entity", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(200)),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("raw", JSONB, nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="1"),
        sa.Column("first_seen_at", TS, server_default=NOW, nullable=False),
        sa.Column("last_seen_at", TS, server_default=NOW, nullable=False),
        sa.Column("resolved_by", sa.String(100)),
        sa.Column("resolved_at", TS),
        sa.Column("note", sa.String(500)),
        sa.CheckConstraint("status in ('open','resolved','skipped')",
                           name="ck_sync_dead_letters_status"),
    )
    op.create_index("uq_sync_dead_letters_open", "sync_dead_letters",
                    ["connection_id", "entity", "external_id"], unique=True,
                    postgresql_where=sa.text("status = 'open'"))

    op.create_table(
        "erp_customers", _id(), _tenant(), _conn(), *_record_cols(),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("gstin", sa.String(15), index=True),
        sa.Column("pan", sa.String(10), index=True),
        sa.Column("customer_group", sa.String(200)),
        sa.Column("territory", sa.String(200)),
        sa.Column("payment_terms", sa.String(200)),
        sa.Column("credit_limit", MONEY),
        sa.Column("currency", sa.String(3)),
        # Link to the golden record (decision E4); changes to it are audited.
        sa.Column("account_id", UUID, sa.ForeignKey("accounts.id"), index=True),
        sa.Column("link_status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("link_method", sa.String(20)),
        sa.Column("link_reasons", JSONB, nullable=False, server_default="[]"),
        sa.Column("candidates", JSONB, nullable=False, server_default="[]"),
        sa.Column("linked_by", sa.String(100)),
        sa.Column("linked_at", TS),
        sa.UniqueConstraint("connection_id", "external_id"),
        sa.CheckConstraint(
            "link_status in ('pending','linked','review','ignored')",
            name="ck_erp_customers_link_status"),
    )
    op.create_table(
        "erp_items", _id(), _tenant(), _conn(), *_record_cols(),
        sa.Column("code", sa.String(200), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("item_group", sa.String(200)),
        sa.Column("uom", sa.String(50)),
        sa.Column("hsn_code", sa.String(20)),
        sa.UniqueConstraint("connection_id", "external_id"),
    )
    op.create_table(
        "erp_prices", _id(), _tenant(), _conn(), *_record_cols(),
        sa.Column("item_code", sa.String(200), nullable=False, index=True),
        sa.Column("price_list", sa.String(200), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("rate", QTY, nullable=False),
        sa.Column("uom", sa.String(50)),
        sa.Column("customer_external_id", sa.String(200)),
        sa.Column("valid_from", sa.Date),
        sa.Column("valid_upto", sa.Date),
        sa.UniqueConstraint("connection_id", "external_id"),
    )
    op.create_table(
        "erp_orders", _id(), _tenant(), _conn(), *_record_cols(),
        sa.Column("number", sa.String(200), nullable=False),
        sa.Column("customer_external_id", sa.String(200), nullable=False),
        sa.Column("order_date", sa.Date, nullable=False),
        sa.Column("delivery_date", sa.Date),
        sa.Column("customer_po", sa.String(200)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("erp_status", sa.String(60)),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("net_total", MONEY, nullable=False),
        sa.Column("grand_total", MONEY, nullable=False),
        sa.UniqueConstraint("connection_id", "external_id"),
        sa.Index("ix_erp_orders_customer", "connection_id", "customer_external_id"),
    )
    op.create_table(
        "erp_invoices", _id(), _tenant(), _conn(), *_record_cols(),
        sa.Column("number", sa.String(200), nullable=False),
        sa.Column("customer_external_id", sa.String(200), nullable=False),
        sa.Column("invoice_date", sa.Date, nullable=False),
        sa.Column("due_date", sa.Date),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("erp_status", sa.String(60)),
        sa.Column("is_return", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("grand_total", MONEY, nullable=False),
        sa.Column("outstanding", MONEY, nullable=False),
        sa.UniqueConstraint("connection_id", "external_id"),
        sa.Index("ix_erp_invoices_customer", "connection_id", "customer_external_id"),
    )
    op.create_table(
        "erp_receipts", _id(), _tenant(), _conn(), *_record_cols(),
        sa.Column("number", sa.String(200), nullable=False),
        sa.Column("customer_external_id", sa.String(200), nullable=False),
        sa.Column("received_on", sa.Date, nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.UniqueConstraint("connection_id", "external_id"),
        sa.Index("ix_erp_receipts_customer", "connection_id", "customer_external_id"),
    )
    op.create_table(
        "erp_stock", _id(), _tenant(), _conn(), *_record_cols(),
        sa.Column("item_code", sa.String(200), nullable=False, index=True),
        sa.Column("warehouse", sa.String(200), nullable=False),
        sa.Column("actual_qty", QTY, nullable=False),
        sa.Column("reserved_qty", QTY, nullable=False),
        sa.Column("ordered_qty", QTY, nullable=False),
        sa.Column("projected_qty", QTY, nullable=False),
        sa.Column("uom", sa.String(50)),
        sa.UniqueConstraint("connection_id", "external_id"),
    )

    op.create_table(
        "erp_writes", _id(), _tenant(), _conn(),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("account_id", UUID, sa.ForeignKey("accounts.id"), index=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("request", JSONB, nullable=False),
        sa.Column("external_id", sa.String(200)),
        sa.Column("error", sa.String(1000)),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("requested_by", sa.String(100), nullable=False),
        *_ts(),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
        sa.CheckConstraint(
            "status in ('pending','sending','succeeded','failed','unknown','refused')",
            name="ck_erp_writes_status"),
    )

    op.create_table(
        "forge_codes", _id(), _tenant(),
        sa.Column("list", sa.String(20), nullable=False),
        sa.Column("code", sa.String(200), nullable=False),
        sa.Column("label", sa.String(300), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        *_ts(),
        sa.UniqueConstraint("tenant_id", "list", "code"),
    )
    op.create_table(
        "code_maps", _id(), _tenant(), _conn(),
        sa.Column("list", sa.String(20), nullable=False),
        sa.Column("erp_code", sa.String(200), nullable=False),
        sa.Column("forge_code", sa.String(200)),
        sa.Column("status", sa.String(10), nullable=False),  # suggested | confirmed | unmapped
        sa.Column("suggestion_source", sa.String(20)),
        sa.Column("confidence", sa.Numeric(5, 2)),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(100), nullable=False),
        *_ts(),
        sa.UniqueConstraint("connection_id", "list", "erp_code"),
        sa.CheckConstraint("status in ('suggested','confirmed','unmapped')",
                           name="ck_code_maps_status"),
    )
    op.create_table(
        "code_map_history", _id(), _tenant(),
        sa.Column("code_map_id", UUID, sa.ForeignKey("code_maps.id"), nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("forge_code", sa.String(200)),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("suggestion_source", sa.String(20)),
        sa.Column("changed_by", sa.String(100), nullable=False),
        sa.Column("changed_at", TS, server_default=NOW, nullable=False),
        sa.UniqueConstraint("code_map_id", "version"),
    )
    op.create_table(
        "mcp_clients", _id(), _tenant(),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("token_hint", sa.String(12), nullable=False),
        sa.Column("allow_write", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.Column("last_used_at", TS),
        sa.Column("revoked_at", TS),
        sa.Column("revoked_by", sa.String(100)),
    )

    # Integration alerts and tasks join approvals and notices in "Waiting for you" (E7).
    op.drop_constraint("ck_decisions_kind", "decisions", type_="check")
    op.create_check_constraint("ck_decisions_kind", "decisions",
                               "kind in ('approval','notice','alert','task')")

    for t in TABLES:
        enable_tenant_rls(op, t, allow_system=t in SYSTEM_TABLES or t == "mcp_clients")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON " + ", ".join(TABLES) + " TO forge_app")
    # Mapping history is append-only for the app.
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON code_map_history FROM forge_app")


def downgrade() -> None:
    op.execute("DELETE FROM decisions WHERE kind IN ('alert','task')")
    op.drop_constraint("ck_decisions_kind", "decisions", type_="check")
    op.create_check_constraint("ck_decisions_kind", "decisions", "kind in ('approval','notice')")
    for t in reversed(TABLES):
        op.drop_table(t)
