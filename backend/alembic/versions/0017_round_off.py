"""Phase 2, M3 CPQ, F3.3: rounding is switched on per quote (Y5, 2026-10-21).

- quote_revisions.round_off: whether this revision's prices are rounded up to the step
  its pricing policy names (₹1, ₹5 or ₹10). Off by default, so a price is exact to the
  paisa until a person ticks "Round off" on the quote.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-21
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("quote_revisions", sa.Column("round_off", sa.Boolean, nullable=False,
                                               server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("quote_revisions", "round_off")
