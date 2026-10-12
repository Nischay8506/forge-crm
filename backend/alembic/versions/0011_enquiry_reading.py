"""Phase 2, M1 (second part): the document reader (F1.4).

- enquiry_lines.unsure: the fields of a line the reader was not sure of, or could not
  read on a scan or drawing (left empty, never guessed), so the screen highlights
  them for a person to check (N10 A).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMPTY_LIST = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.add_column("enquiry_lines", sa.Column("unsure", pg.JSONB, server_default=EMPTY_LIST,
                                             nullable=False))


def downgrade() -> None:
    op.drop_column("enquiry_lines", "unsure")
