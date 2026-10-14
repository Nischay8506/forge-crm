"""Phase 2, M3 CPQ, F3.2 the cost engine (decisions K2-K13, 2026-10-14).

- material_rates: the yield % and the scrap rate that come with a material's rate (K4 A),
  and the rate as it was typed, in its own unit, next to the canonical one (K12 A).
- erp_items: ERPNext's valuation and last purchase rates, shown next to Forge's material
  rates for reference only (K5 A).
- erp_bom_versions: every version of an ERPNext BOM Forge has read, never changed, so a
  quote's cost can always show the BOM it used (K11 A).
- quote_lines: whether the cost is complete, incomplete, not known or reviewed, and the
  review card (K10 A).
- quote_revisions: "cost changed: reprice recommended" on sent quotes (K9 A, K11 A).
- cost_alerts: what each quote owner hears about in the daily email (K9 A).

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-14
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.rls import enable_tenant_rls

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = pg.UUID(as_uuid=True)
JSONB = pg.JSONB
NOW = sa.text("now()")
TS = sa.DateTime(timezone=True)
QTY = sa.Numeric(18, 4)


def upgrade() -> None:
    op.add_column("material_rates", sa.Column("yield_pct", sa.Numeric(7, 3)))
    op.add_column("material_rates", sa.Column("scrap_rate", QTY))
    op.add_column("material_rates", sa.Column("source_unit", sa.String(20)))
    op.add_column("material_rates", sa.Column("source_rate", QTY))
    op.create_check_constraint("ck_material_rates_yield", "material_rates",
                               "yield_pct is null or (yield_pct > 0 and yield_pct <= 100)")
    op.create_check_constraint("ck_material_rates_scrap", "material_rates",
                               "scrap_rate is null or scrap_rate >= 0")

    op.add_column("erp_items", sa.Column("valuation_rate", QTY))
    op.add_column("erp_items", sa.Column("last_purchase_rate", QTY))

    op.create_table(
        "erp_bom_versions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("connection_id", UUID, sa.ForeignKey("erp_connections.id"), nullable=False),
        sa.Column("bom_code", sa.String(200), nullable=False),
        sa.Column("item_code", sa.String(200), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("data", JSONB, nullable=False),
        sa.Column("modified_at", TS, nullable=False),
        sa.Column("seen_at", TS, server_default=NOW, nullable=False),
        sa.UniqueConstraint("connection_id", "bom_code", "fingerprint",
                            name="uq_erp_bom_versions"),
    )
    op.create_index("ix_erp_bom_versions_code", "erp_bom_versions", ["tenant_id", "bom_code"])

    op.add_column("quote_lines", sa.Column("cost_status", sa.String(12)))
    op.add_column("quote_lines", sa.Column("cost_review_id", UUID,
                                           sa.ForeignKey("decisions.id")))
    op.create_check_constraint(
        "ck_quote_lines_cost_status", "quote_lines",
        "cost_status is null or cost_status in ('complete','incomplete','unknown','reviewed')")
    op.add_column("quote_revisions", sa.Column("reprice_reason", sa.String(500)))
    op.add_column("quote_revisions", sa.Column("reprice_at", TS))

    op.create_table(
        "cost_alerts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("quote_id", UUID, sa.ForeignKey("quotes.id"), nullable=False),
        sa.Column("revision", sa.SmallInteger, nullable=False),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id")),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.Column("emailed_at", TS),
    )
    op.create_index("ix_cost_alerts_waiting", "cost_alerts", ["tenant_id", "owner_id"],
                    postgresql_where=sa.text("emailed_at is null"))

    for t in ("erp_bom_versions", "cost_alerts"):
        enable_tenant_rls(op, t)
    # A BOM version Forge has seen never changes; alerts only get their email time.
    op.execute("GRANT SELECT, INSERT ON erp_bom_versions TO forge_app")
    op.execute("GRANT SELECT, INSERT ON cost_alerts TO forge_app")
    op.execute("GRANT UPDATE (emailed_at) ON cost_alerts TO forge_app")


def downgrade() -> None:
    op.drop_table("cost_alerts")
    op.drop_column("quote_revisions", "reprice_at")
    op.drop_column("quote_revisions", "reprice_reason")
    op.drop_constraint("ck_quote_lines_cost_status", "quote_lines")
    op.drop_column("quote_lines", "cost_review_id")
    op.drop_column("quote_lines", "cost_status")
    op.drop_table("erp_bom_versions")
    op.drop_column("erp_items", "last_purchase_rate")
    op.drop_column("erp_items", "valuation_rate")
    op.drop_constraint("ck_material_rates_scrap", "material_rates")
    op.drop_constraint("ck_material_rates_yield", "material_rates")
    for c in ("source_rate", "source_unit", "scrap_rate", "yield_pct"):
        op.drop_column("material_rates", c)
