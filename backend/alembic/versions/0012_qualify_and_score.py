"""Phase 2, M1 (second part): "can we make this?" (F1.6) and lead scoring (F1.7).

Decisions 2026-10-12: N8 A (a capability profile the company sets up, from starter
templates), N15 A (scoring v1: clear rules and weights, top three reasons in words),
P1 A (a decline is a draft a person approves), P2 A (an enquiry that needs
engineering gets a review step for an engineering role, with its own timer), P3 A
(the automatic acknowledgement goes to standard and not-yet-checked enquiries).

- capability_profiles: one per company: processes and their limits, materials,
  certificates, industries it does not supply and the ones it wants, and the role
  that does engineering reviews.
- product_families: how the family is usually supplied (standard, configurable,
  needs engineering, or not made), the minimum order, the order size it suits best,
  and who to refer a customer to when we decline.
- enquiries and enquiry_lines: the check's stamp and reasons; enquiries also the
  score, its three parts and the top reasons.
- enquiry_settings.scoring: the company's scoring weights (defaults in code).
- enquiry_reviews: the engineering review step.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.rls import enable_tenant_rls

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = pg.UUID(as_uuid=True)
JSONB = pg.JSONB
NOW = sa.text("now()")
TS = sa.DateTime(timezone=True)
EMPTY_LIST = sa.text("'[]'::jsonb")
MAKE = "('standard','cto','eto','no_fit')"


def _list(name: str) -> sa.Column:
    return sa.Column(name, JSONB, server_default=EMPTY_LIST, nullable=False)


def upgrade() -> None:
    op.create_table(
        "capability_profiles",
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), primary_key=True),
        _list("processes"),
        _list("materials"),
        _list("certifications"),
        _list("excluded_industries"),
        _list("target_industries"),
        sa.Column("engineering_role_id", UUID, sa.ForeignKey("roles.id")),
        sa.Column("updated_by", sa.String(100), nullable=False),
        sa.Column("updated_at", TS, server_default=NOW, nullable=False),
    )

    op.add_column("product_families", sa.Column("make_ability", sa.String(10),
                                                server_default="standard", nullable=False))
    op.add_column("product_families", sa.Column("moq", JSONB))
    op.add_column("product_families", sa.Column("sweet_spot", JSONB))
    op.add_column("product_families", sa.Column("refer_to", sa.String(300)))
    op.create_check_constraint("ck_product_families_make", "product_families",
                               f"make_ability IN {MAKE}")

    op.add_column("enquiries", sa.Column("make_ability", sa.String(10)))
    op.add_column("enquiries", _list("make_reasons"))
    op.add_column("enquiries", sa.Column("make_source", sa.String(10)))
    op.add_column("enquiries", sa.Column("make_checked_at", TS))
    op.add_column("enquiries", sa.Column("score", sa.SmallInteger))
    op.add_column("enquiries", sa.Column("score_parts", JSONB))
    op.add_column("enquiries", _list("score_reasons"))
    op.add_column("enquiries", sa.Column("scored_at", TS))
    op.create_check_constraint("ck_enquiries_make", "enquiries",
                               f"make_ability IS NULL OR make_ability IN {MAKE}")
    op.create_check_constraint("ck_enquiries_make_source", "enquiries",
                               "make_source IS NULL OR make_source IN "
                               "('rules','model','person')")
    op.create_check_constraint("ck_enquiries_score", "enquiries",
                               "score IS NULL OR score BETWEEN 0 AND 100")
    op.create_index("ix_enquiries_score", "enquiries", ["tenant_id", "score"])

    op.add_column("enquiry_lines", sa.Column("make_ability", sa.String(10)))
    op.add_column("enquiry_lines", _list("make_reasons"))
    op.create_check_constraint("ck_enquiry_lines_make", "enquiry_lines",
                               f"make_ability IS NULL OR make_ability IN {MAKE}")

    op.add_column("enquiry_settings", sa.Column("scoring", JSONB))

    op.create_table(
        "enquiry_reviews",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("enquiry_id", UUID, sa.ForeignKey("enquiries.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="engineering"),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column("role_id", UUID, sa.ForeignKey("roles.id")),
        sa.Column("assignee_id", UUID, sa.ForeignKey("users.id")),
        _list("reasons"),
        sa.Column("package", JSONB, nullable=False),
        sa.Column("outcome", sa.String(20)),
        sa.Column("notes", sa.String(2000)),
        sa.Column("opened_by", sa.String(100), nullable=False),
        sa.Column("opened_at", TS, server_default=NOW, nullable=False),
        sa.Column("taken_at", TS),
        sa.Column("done_by", sa.String(100)),
        sa.Column("done_at", TS),
        sa.CheckConstraint("kind IN ('engineering')", name="ck_enquiry_reviews_kind"),
        sa.CheckConstraint("status IN ('open','done','cancelled')",
                           name="ck_enquiry_reviews_status"),
        sa.CheckConstraint("outcome IS NULL OR outcome IN ('can_make','cannot_make')",
                           name="ck_enquiry_reviews_outcome"),
    )
    op.create_index("ix_enquiry_reviews_enquiry", "enquiry_reviews", ["tenant_id", "enquiry_id"])
    op.create_index("uq_enquiry_reviews_open", "enquiry_reviews",
                    ["tenant_id", "enquiry_id", "kind"], unique=True,
                    postgresql_where=sa.text("status = 'open'"))

    for table in ("capability_profiles", "enquiry_reviews"):
        enable_tenant_rls(op, table)
    op.execute("REVOKE DELETE, TRUNCATE ON capability_profiles, enquiry_reviews FROM forge_app")


def downgrade() -> None:
    op.drop_table("enquiry_reviews")
    op.drop_column("enquiry_settings", "scoring")
    op.drop_constraint("ck_enquiry_lines_make", "enquiry_lines")
    for col in ("make_reasons", "make_ability"):
        op.drop_column("enquiry_lines", col)
    op.drop_index("ix_enquiries_score", "enquiries")
    for ck in ("ck_enquiries_score", "ck_enquiries_make_source", "ck_enquiries_make"):
        op.drop_constraint(ck, "enquiries")
    for col in ("scored_at", "score_reasons", "score_parts", "score", "make_checked_at",
                "make_source", "make_reasons", "make_ability"):
        op.drop_column("enquiries", col)
    op.drop_constraint("ck_product_families_make", "product_families")
    for col in ("refer_to", "sweet_spot", "moq", "make_ability"):
        op.drop_column("product_families", col)
    op.drop_table("capability_profiles")
