"""Phase 2, M3 CPQ, F3.3 the price and the floor (decision C2 A, 2026-10-18).

- price_policies: what a family is priced by from a date (target margin, floor, tier
  discounts, quantity breaks, rounding). Never edited: a change is a new row from a
  date, so an old quote can still be explained with the numbers of its day. A row with
  no family is the company-wide one.
- quote_lines: where the list price came from, the price itself, the margin, the floor
  check, the waterfall that made it, what the rep asked for and the approval card.
- quote_revisions: when the revision was priced, and whether anything on it is below
  its floor.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-18
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.rls import enable_tenant_rls

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = pg.UUID(as_uuid=True)
JSONB = pg.JSONB
NOW = sa.text("now()")
TS = sa.DateTime(timezone=True)
QTY = sa.Numeric(18, 4)


def upgrade() -> None:
    op.create_table(
        "price_policies",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("family_id", UUID, sa.ForeignKey("product_families.id")),
        sa.Column("effective_from", sa.Date, nullable=False),
        sa.Column("policy", JSONB, nullable=False),
        sa.Column("note", sa.String(300)),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
    )
    op.create_index("ix_price_policies_lookup", "price_policies",
                    ["tenant_id", "family_id", "effective_from"])

    op.add_column("quote_lines", sa.Column("price_source", sa.String(12)))
    op.add_column("quote_lines", sa.Column("list_price", QTY))
    op.add_column("quote_lines", sa.Column("unit_price", QTY))
    op.add_column("quote_lines", sa.Column("margin_pct", sa.Numeric(7, 3)))
    op.add_column("quote_lines", sa.Column("price_status", sa.String(12)))
    op.add_column("quote_lines", sa.Column("price", JSONB))
    op.add_column("quote_lines", sa.Column("ask", JSONB))
    op.add_column("quote_lines", sa.Column("price_review_id", UUID,
                                           sa.ForeignKey("decisions.id")))
    op.create_check_constraint(
        "ck_quote_lines_price_source", "quote_lines",
        "price_source is null or price_source in "
        "('contract','price_list','cost_plus','none')")
    op.create_check_constraint(
        "ck_quote_lines_price_status", "quote_lines",
        "price_status is null or price_status in "
        "('ok','below_floor','no_cost','no_price','no_floor')")
    op.create_check_constraint("ck_quote_lines_prices_positive", "quote_lines",
                               "(list_price is null or list_price >= 0) and "
                               "(unit_price is null or unit_price >= 0)")

    op.add_column("quote_revisions", sa.Column("priced_at", TS))
    op.add_column("quote_revisions", sa.Column("price_status", sa.String(12)))
    op.create_check_constraint(
        "ck_quote_revisions_price_status", "quote_revisions",
        "price_status is null or price_status in ('ok','below_floor')")

    enable_tenant_rls(op, "price_policies")
    # A policy is never edited or removed: a change is a new row from a date.
    op.execute("GRANT SELECT, INSERT ON price_policies TO forge_app")


def downgrade() -> None:
    op.drop_constraint("ck_quote_revisions_price_status", "quote_revisions")
    op.drop_column("quote_revisions", "price_status")
    op.drop_column("quote_revisions", "priced_at")
    for c in ("ck_quote_lines_prices_positive", "ck_quote_lines_price_status",
              "ck_quote_lines_price_source"):
        op.drop_constraint(c, "quote_lines")
    for c in ("price_review_id", "ask", "price", "price_status", "margin_pct",
              "unit_price", "list_price", "price_source"):
        op.drop_column("quote_lines", c)
    op.drop_table("price_policies")
