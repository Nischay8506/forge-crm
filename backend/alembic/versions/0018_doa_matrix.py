"""Phase 2, M3 CPQ, F3.4 who approves a discount (Z1-Z10 A, 2026-10-21).

- doa_matrices: a company's delegation of authority, written as rows that say "when a
  quote looks like this, these roles approve, in this order" (app/cpq/doa.py). Never
  edited: a change is a new row from a date (Z4 A), so a quote approved in March can
  still be explained with the rulebook that applied in March. One matrix per company;
  the rows inside it carry the conditions.

Written by Claude at Nischay's explicit instruction ("0018", 2026-10-21); migrations are
normally his. It revises 0017, which at the time of writing is on his PC only.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.rls import enable_tenant_rls

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = pg.UUID(as_uuid=True)
JSONB = pg.JSONB
NOW = sa.text("now()")
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "doa_matrices",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("effective_from", sa.Date, nullable=False),
        sa.Column("matrix", JSONB, nullable=False),
        sa.Column("note", sa.String(300)),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
    )
    op.create_index("ix_doa_matrices_lookup", "doa_matrices",
                    ["tenant_id", "effective_from"])

    enable_tenant_rls(op, "doa_matrices")
    # A matrix is never edited or removed: a change is a new row from a date (Z4 A). The
    # default privileges 0001 sets give forge_app update and delete on every new table, so
    # saying "grant select, insert" would add nothing; the two have to be taken away for
    # the database itself to hold the rule, not just the code that writes to it.
    op.execute("GRANT SELECT, INSERT ON doa_matrices TO forge_app")
    op.execute("REVOKE UPDATE, DELETE ON doa_matrices FROM forge_app")


def downgrade() -> None:
    op.drop_table("doa_matrices")
