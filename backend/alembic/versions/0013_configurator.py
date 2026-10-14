"""Phase 2, M3 CPQ (built in the guide's order, decision C1 B), F3.1 the configurator.

Decision 2026-10-14 C4 A: each product family can have an options model (attributes,
options, rules: requires / excludes / only when, and a variant code pattern), checked
for conflicts and dead ends before it is saved, and a guided configurator that offers
only valid choices.

- product_models: one row per saved version of a family's model; never changed after,
  only the latest is current.
- configurations: a configured product (values, variant code, fingerprint) and why it
  should be repriced, if something it depends on changed.
- enquiry_lines.configuration_id: the configured product behind a line.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-14
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.rls import enable_tenant_rls

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = pg.UUID(as_uuid=True)
JSONB = pg.JSONB
NOW = sa.text("now()")
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "product_models",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("family_id", UUID, sa.ForeignKey("product_families.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("current", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("attributes", JSONB, nullable=False),
        sa.Column("rules", JSONB, nullable=False),
        sa.Column("code_pattern", sa.String(100), nullable=False),
        sa.Column("note", sa.String(300)),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.UniqueConstraint("tenant_id", "family_id", "version",
                            name="uq_product_models_version"),
    )
    op.create_index("uq_product_models_current", "product_models", ["tenant_id", "family_id"],
                    unique=True, postgresql_where=sa.text("current"))

    op.create_table(
        "configurations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("family_id", UUID, sa.ForeignKey("product_families.id"), nullable=False),
        sa.Column("model_id", UUID, sa.ForeignKey("product_models.id"), nullable=False),
        sa.Column("values", JSONB, nullable=False),
        sa.Column("labels", JSONB, nullable=False),
        sa.Column("variant_code", sa.String(100), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("reprice_reason", sa.String(500)),
        sa.Column("reprice_at", TS),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
    )
    op.create_index("ix_configurations_fingerprint", "configurations",
                    ["tenant_id", "fingerprint"])
    op.create_index("ix_configurations_model", "configurations", ["model_id"])

    op.add_column("enquiry_lines", sa.Column("configuration_id", UUID,
                                             sa.ForeignKey("configurations.id")))

    for table in ("product_models", "configurations"):
        enable_tenant_rls(op, table)
    op.execute("REVOKE DELETE, TRUNCATE ON product_models, configurations FROM forge_app")
    # A saved version never changes: only the "current" flag moves to the next one.
    op.execute("REVOKE UPDATE ON product_models FROM forge_app")
    op.execute("GRANT UPDATE (current) ON product_models TO forge_app")


def downgrade() -> None:
    op.drop_column("enquiry_lines", "configuration_id")
    op.drop_table("configurations")
    op.drop_table("product_models")
