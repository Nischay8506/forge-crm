"""Step 5 (F12.3): the approval inbox.

- decisions.first_shown_at: when a person who can decide first had the card on
  screen (waiting time before anyone looked, for the approval analytics).
- decisions.decision_ms: how long the person who decided had the card in front of
  them before deciding, measured by the browser (the "under 60 seconds" check).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-09
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("decisions", sa.Column("first_shown_at", sa.DateTime(timezone=True)))
    op.add_column("decisions", sa.Column("decision_ms", sa.Integer()))
    op.create_check_constraint("ck_decisions_decision_ms", "decisions",
                               "decision_ms IS NULL OR decision_ms >= 0")


def downgrade() -> None:
    op.drop_constraint("ck_decisions_decision_ms", "decisions")
    op.drop_column("decisions", "decision_ms")
    op.drop_column("decisions", "first_shown_at")
