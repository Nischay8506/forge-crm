"""Phase 2, M3 CPQ, F3.2 the cost engine, and the quote record it fills.

Decision 2026-10-14 C3 A: a rate card and a full cost model per family, material rates
with dates, and BOMs, routings and machine-hour rates read from ERPNext where the
company keeps them there. Decisions C12 A and C14 A: quotes numbered per company and
financial year (Q/2026-27/0001) with revisions R1, R2 ..., each belonging to an enquiry.

- erp_workstations, erp_boms: synced from ERPNext like the other erp_* records; every
  existing connection gets the two new flows switched off (the integration user may
  need the Manufacturing User role first).
- material_rates, machine_rates: dated rates; a change is a new row, never an edit.
- cost_models: one row per saved version of a family's cost model.
- quotes, quote_revisions, quote_lines: the quote record; each line keeps its cost
  breakdown.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-14
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.rls import enable_tenant_rls

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = pg.UUID(as_uuid=True)
JSONB = pg.JSONB
NOW = sa.text("now()")
TS = sa.DateTime(timezone=True)
QTY = sa.Numeric(18, 4)

ERP_TABLES = ["erp_workstations", "erp_boms"]
TABLES = ["material_rates", "machine_rates", "cost_models", "quotes", "quote_revisions",
          "quote_lines"]


def _id():
    return sa.Column("id", UUID, primary_key=True)


def _tenant():
    return sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False, index=True)


def _erp_cols():
    """ErpRecordMixin, as in 0005."""
    return [
        sa.Column("connection_id", UUID, sa.ForeignKey("erp_connections.id"), nullable=False,
                  index=True),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("modified_at", TS, nullable=False),
        sa.Column("synced_at", TS, server_default=NOW, nullable=False),
        sa.Column("gone_at", TS),
        sa.Column("disabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("data", JSONB, nullable=False),
        sa.UniqueConstraint("connection_id", "external_id"),
    ]


def _who():
    return [sa.Column("created_by", sa.String(100), nullable=False),
            sa.Column("created_at", TS, server_default=NOW, nullable=False)]


def upgrade() -> None:
    op.create_table(
        "erp_workstations", _id(), _tenant(), *_erp_cols(),
        sa.Column("code", sa.String(200), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("hour_rate", QTY, nullable=False),
    )
    op.create_table(
        "erp_boms", _id(), _tenant(), *_erp_cols(),
        sa.Column("code", sa.String(200), nullable=False),
        sa.Column("item_code", sa.String(200), nullable=False, index=True),
        sa.Column("quantity", QTY, nullable=False),
        sa.Column("uom", sa.String(50)),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    for t in ERP_TABLES:
        enable_tenant_rls(op, t)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON " + ", ".join(ERP_TABLES)
               + " TO forge_app")
    # Every tenant's connections: the scheduler's view (erp_connections and sync_flows
    # allow it, 0005), only inside this migration's transaction.
    op.execute("SELECT set_config('app.system', 'on', true)")
    for entity in ("workstation", "bom"):
        op.execute(
            "INSERT INTO sync_flows (id, tenant_id, connection_id, entity, enabled, state) "
            f"SELECT gen_random_uuid(), c.tenant_id, c.id, '{entity}', false, '{{}}' "
            "FROM erp_connections c WHERE NOT EXISTS (SELECT 1 FROM sync_flows f WHERE "
            f"f.connection_id = c.id AND f.entity = '{entity}')")
    op.execute("SELECT set_config('app.system', 'off', true)")

    op.create_table(
        "material_rates", _id(), _tenant(),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("unit", sa.String(10), nullable=False, server_default="kg"),
        sa.Column("rate", QTY, nullable=False),
        sa.Column("effective_from", sa.Date, nullable=False),
        sa.Column("note", sa.String(300)),
        *_who(),
        sa.CheckConstraint("rate >= 0", name="ck_material_rates_rate"),
    )
    op.create_index("ix_material_rates_code", "material_rates",
                    ["tenant_id", "code", "effective_from"])
    op.create_table(
        "machine_rates", _id(), _tenant(),
        sa.Column("code", sa.String(140), nullable=False),
        sa.Column("name", sa.String(140), nullable=False),
        sa.Column("hour_rate", QTY, nullable=False),
        sa.Column("effective_from", sa.Date, nullable=False),
        sa.Column("note", sa.String(300)),
        *_who(),
        sa.CheckConstraint("hour_rate >= 0", name="ck_machine_rates_rate"),
    )
    op.create_index("ix_machine_rates_code", "machine_rates",
                    ["tenant_id", "code", "effective_from"])
    op.create_table(
        "cost_models", _id(), _tenant(),
        sa.Column("family_id", UUID, sa.ForeignKey("product_families.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("current", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("model", JSONB, nullable=False),
        sa.Column("note", sa.String(300)),
        *_who(),
        sa.UniqueConstraint("tenant_id", "family_id", "version", name="uq_cost_models_version"),
    )
    op.create_index("uq_cost_models_current", "cost_models", ["tenant_id", "family_id"],
                    unique=True, postgresql_where=sa.text("current"))

    op.create_table(
        "quotes", _id(), _tenant(),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("fy", sa.String(7), nullable=False),
        sa.Column("number", sa.String(30), nullable=False),
        sa.Column("enquiry_id", UUID, sa.ForeignKey("enquiries.id"), nullable=False,
                  index=True),
        sa.Column("account_id", UUID, sa.ForeignKey("accounts.id"), index=True),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id")),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("active_revision", sa.SmallInteger, nullable=False, server_default="1"),
        *_who(),
        sa.Column("updated_at", TS, server_default=NOW, nullable=False),
        sa.UniqueConstraint("tenant_id", "number", name="uq_quotes_number"),
        sa.UniqueConstraint("tenant_id", "fy", "seq", name="uq_quotes_seq"),
        sa.CheckConstraint("status in ('draft','pending_approval','sent','accepted','lost',"
                           "'expired')", name="ck_quotes_status"),
    )
    op.create_table(
        "quote_revisions", _id(), _tenant(),
        sa.Column("quote_id", UUID, sa.ForeignKey("quotes.id"), nullable=False),
        sa.Column("number", sa.SmallInteger, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("reason", sa.String(20)),
        sa.Column("reason_note", sa.String(300)),
        sa.Column("valid_until", sa.Date, nullable=False),
        sa.Column("delivery_terms", sa.String(20)),
        sa.Column("destination", JSONB),
        sa.Column("costed_at", TS),
        *_who(),
        sa.Column("sent_at", TS),
        sa.UniqueConstraint("quote_id", "number", name="uq_quote_revisions_number"),
        sa.CheckConstraint("reason is null or reason in ('customer_ask','cost_change',"
                           "'correction')", name="ck_quote_revisions_reason"),
    )
    op.create_table(
        "quote_lines", _id(), _tenant(),
        sa.Column("revision_id", UUID, sa.ForeignKey("quote_revisions.id"), nullable=False,
                  index=True),
        sa.Column("position", sa.SmallInteger, nullable=False),
        sa.Column("enquiry_line_id", UUID, sa.ForeignKey("enquiry_lines.id")),
        sa.Column("family_id", UUID, sa.ForeignKey("product_families.id")),
        sa.Column("configuration_id", UUID, sa.ForeignKey("configurations.id")),
        sa.Column("item_code", sa.String(140)),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("variant_code", sa.String(100)),
        sa.Column("qty", QTY),
        sa.Column("unit", sa.String(20)),
        sa.Column("cost_source", sa.String(20)),
        sa.Column("unit_cost", QTY),
        sa.Column("cost", JSONB),
        sa.Column("cost_flag", sa.String(500)),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.Column("updated_at", TS, server_default=NOW, nullable=False),
    )
    for t in TABLES:
        enable_tenant_rls(op, t)
    op.execute("GRANT SELECT, INSERT, UPDATE ON " + ", ".join(TABLES) + " TO forge_app")
    op.execute("REVOKE DELETE, TRUNCATE ON material_rates, machine_rates, cost_models, quotes, "
               "quote_revisions FROM forge_app")
    # Rates and saved cost models never change: a change is a new row.
    op.execute("REVOKE UPDATE ON material_rates, machine_rates, cost_models FROM forge_app")
    op.execute("GRANT UPDATE (current) ON cost_models TO forge_app")
    # Draft lines are replaced when a draft is edited; sent revisions are frozen (F3.7).
    op.execute("GRANT DELETE ON quote_lines TO forge_app")


def downgrade() -> None:
    for t in reversed(TABLES):
        op.drop_table(t)
    # The new flows, their runs and dead letters, tenant by tenant (RLS).
    op.execute("""
        DO $$
        DECLARE t uuid;
        BEGIN
          PERFORM set_config('app.system', 'on', true);
          FOR t IN SELECT DISTINCT tenant_id FROM sync_flows
                   WHERE entity IN ('workstation', 'bom') LOOP
            PERFORM set_config('app.tenant_id', t::text, true);
            DELETE FROM sync_runs WHERE flow_id IN (
              SELECT id FROM sync_flows WHERE entity IN ('workstation', 'bom'));
            DELETE FROM sync_dead_letters WHERE entity IN ('workstation', 'bom');
          END LOOP;
          DELETE FROM sync_flows WHERE entity IN ('workstation', 'bom');
          PERFORM set_config('app.system', 'off', true);
          PERFORM set_config('app.tenant_id', '', true);
        END $$;
    """)
    for t in reversed(ERP_TABLES):
        op.drop_table(t)
