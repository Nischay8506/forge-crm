"""Step 1 (F12.1, decision S2A): pgvector and account name embeddings.

The vector extension must already exist or the migrating role must be allowed to
create it. Locally infra/postgres-init.sql creates it as the superuser; on Neon the
project owner can create it.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08
"""
from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.rls import enable_tenant_rls

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = pg.UUID(as_uuid=True)
NOW = sa.text("now()")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "account_embeddings",
        sa.Column("account_id", UUID, sa.ForeignKey("accounts.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("model", sa.String(150), nullable=False),
        sa.Column("dims", sa.Integer, nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )
    op.create_index("ix_account_embeddings_model_dims", "account_embeddings",
                    ["tenant_id", "model", "dims"])
    enable_tenant_rls(op, "account_embeddings")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON account_embeddings TO forge_app")


def downgrade() -> None:
    op.drop_table("account_embeddings")
