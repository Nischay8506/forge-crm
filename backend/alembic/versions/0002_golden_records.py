"""Step 1 (F12.1): golden records (accounts, contacts), field provenance, match
candidates, merge history, import batches.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.rls import enable_tenant_rls

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = pg.UUID(as_uuid=True)
NOW = sa.text("now()")
TABLES = ["accounts", "contacts", "field_provenance", "match_candidates", "merge_events",
          "import_batches"]


def _tenant():
    return sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False, index=True)


def _ts():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    ]


def _hygiene():
    return [sa.Column("hygiene_score", sa.Integer), sa.Column("hygiene_details", pg.JSONB)]


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", UUID, primary_key=True),
        _tenant(),
        sa.Column("kind", sa.String(10), nullable=False, server_default="entity"),
        sa.Column("site_type", sa.String(20)),
        sa.Column("parent_id", UUID, sa.ForeignKey("accounts.id"), index=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("legal_name", sa.String(300)),
        sa.Column("gstin", sa.String(15), index=True),
        sa.Column("pan", sa.String(10), index=True),
        sa.Column("cin", sa.String(21), index=True),
        sa.Column("website", sa.String(300)),
        sa.Column("domain", sa.String(253), index=True),
        sa.Column("email", sa.String(320)),
        sa.Column("phone", sa.String(20), index=True),
        sa.Column("address_line1", sa.String(300)),
        sa.Column("address_line2", sa.String(300)),
        sa.Column("city", sa.String(100)),
        sa.Column("state", sa.String(100)),
        sa.Column("pincode", sa.String(6), index=True),
        sa.Column("country", sa.String(2), nullable=False, server_default="IN"),
        sa.Column("industry", sa.String(100)),
        sa.Column("normalized_name", sa.String(300), nullable=False, server_default=""),
        sa.Column("phonetic_keys", pg.ARRAY(sa.String(20)), server_default="{}"),
        sa.Column("status", sa.String(10), nullable=False, server_default="active", index=True),
        sa.Column("merged_into_id", UUID, sa.ForeignKey("accounts.id")),
        sa.Column("source", sa.String(30), nullable=False, server_default="manual"),
        *_hygiene(),
        *_ts(),
        sa.CheckConstraint("kind in ('group','entity','site')", name="ck_accounts_kind"),
    )
    op.create_index("ix_accounts_phonetic_keys", "accounts", ["phonetic_keys"],
                    postgresql_using="gin")
    op.create_table(
        "contacts",
        sa.Column("id", UUID, primary_key=True),
        _tenant(),
        sa.Column("account_id", UUID, sa.ForeignKey("accounts.id"), index=True),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320), index=True),
        sa.Column("phone", sa.String(20), index=True),
        sa.Column("designation", sa.String(150)),
        sa.Column("department", sa.String(100)),
        sa.Column("preferred_language", sa.String(10)),
        sa.Column("normalized_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("phonetic_keys", pg.ARRAY(sa.String(20)), server_default="{}"),
        sa.Column("status", sa.String(10), nullable=False, server_default="active", index=True),
        sa.Column("merged_into_id", UUID, sa.ForeignKey("contacts.id")),
        sa.Column("source", sa.String(30), nullable=False, server_default="manual"),
        *_hygiene(),
        *_ts(),
    )
    op.create_index("ix_contacts_phonetic_keys", "contacts", ["phonetic_keys"],
                    postgresql_using="gin")
    op.create_table(
        "field_provenance",
        sa.Column("id", UUID, primary_key=True),
        _tenant(),
        sa.Column("record_type", sa.String(10), nullable=False),
        sa.Column("record_id", UUID, nullable=False, index=True),
        sa.Column("field", sa.String(50), nullable=False),
        sa.Column("value", sa.Text),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("source_ref", sa.String(500), index=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False, server_default="1"),
        sa.Column("status", sa.String(12), nullable=False, server_default="current"),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("recorded_by", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )
    op.create_table(
        "match_candidates",
        sa.Column("id", UUID, primary_key=True),
        _tenant(),
        sa.Column("record_type", sa.String(10), nullable=False),
        sa.Column("left_id", UUID, nullable=False, index=True),
        sa.Column("right_id", UUID, nullable=False, index=True),
        sa.Column("score", sa.Numeric(5, 2), nullable=False),
        sa.Column("band", sa.String(12), nullable=False),
        sa.Column("reasons", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("status", sa.String(15), nullable=False, server_default="open", index=True),
        sa.Column("decided_by", sa.String(100)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("tenant_id", "record_type", "left_id", "right_id"),
    )
    op.create_table(
        "merge_events",
        sa.Column("id", UUID, primary_key=True),
        _tenant(),
        sa.Column("record_type", sa.String(10), nullable=False),
        sa.Column("survivor_id", UUID, nullable=False, index=True),
        sa.Column("merged_id", UUID, nullable=False, index=True),
        sa.Column("candidate_id", UUID, sa.ForeignKey("match_candidates.id")),
        sa.Column("survivor_before", pg.JSONB, nullable=False),
        sa.Column("merged_before", pg.JSONB, nullable=False),
        sa.Column("survivor_after", pg.JSONB, nullable=False),
        sa.Column("field_choices", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("repointed", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("automatic", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("decided_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("undone_at", sa.DateTime(timezone=True)),
        sa.Column("undone_by", sa.String(100)),
        sa.Column("undo_notes", pg.JSONB),
    )
    op.create_table(
        "import_batches",
        sa.Column("id", UUID, primary_key=True),
        _tenant(),
        sa.Column("record_type", sa.String(10), nullable=False),
        sa.Column("filename", sa.String(300), nullable=False),
        sa.Column("status", sa.String(15), nullable=False, server_default="processing"),
        sa.Column("total_rows", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("auto_merged_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("review_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("errors", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("rows", pg.JSONB, nullable=False, server_default="[]"),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    for t in TABLES:
        enable_tenant_rls(op, t)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON " + ", ".join(TABLES) + " TO forge_app")


def downgrade() -> None:
    for t in reversed(TABLES):
        op.drop_table(t)
