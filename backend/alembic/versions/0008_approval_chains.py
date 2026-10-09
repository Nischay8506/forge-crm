"""Step 5 (F12.3): approver chains, counter, delegation, deadlines and push.

- policy_rules.approval_steps: the roles that decide, in order (H1); approver_role_id
  stays as the first step. policy_rules.sla_hours: the deadline in working hours for
  cards from this rule (H4).
- decisions: who asked (requested_by, never allowed to decide it), the agent's
  recommended option (H6, counted as an override when the person chose otherwise),
  counter_of (H2: the card this one counters), sla_hours and step_index.
- decision_steps: one row per step of a card's chain, with its deadline, reminder,
  who decided it (and on whose behalf, H3), the time it took and the fingerprint of
  the card as that person saw it (H7). Existing cards get one step each.
- delegations: one card handed to a colleague, or out-of-office with a stand-in,
  dates and an explicit scope (card types and a maximum amount, safeguard 3).
- approval_settings: each company's working calendar, default deadline, reminder,
  escalation and bottleneck-alert settings (H4, H9).
- push_subscriptions: browsers that asked for push notifications (H5).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-09
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.rls import enable_tenant_rls

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = pg.UUID(as_uuid=True)
JSONB = pg.JSONB
NOW = sa.text("now()")
TS = sa.DateTime(timezone=True)


def _tenant():
    return sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False, index=True)


def upgrade() -> None:
    op.add_column("policy_rules", sa.Column("approval_steps", JSONB, nullable=False,
                                            server_default=sa.text("'[]'::jsonb")))
    op.add_column("policy_rules", sa.Column("sla_hours", sa.Numeric(6, 2)))
    op.execute("UPDATE policy_rules SET approval_steps = jsonb_build_array(approver_role_id::text) "
               "WHERE approver_role_id IS NOT NULL")
    op.create_check_constraint("ck_policy_rules_sla_hours", "policy_rules",
                               "sla_hours IS NULL OR sla_hours > 0")

    op.add_column("decisions", sa.Column("requested_by", sa.String(100)))
    op.add_column("decisions", sa.Column("recommended_option", sa.String(10)))
    op.add_column("decisions", sa.Column("counter_of", UUID, sa.ForeignKey("decisions.id")))
    op.add_column("decisions", sa.Column("sla_hours", sa.Numeric(6, 2)))
    op.add_column("decisions", sa.Column("step_index", sa.SmallInteger, nullable=False,
                                         server_default="0"))
    op.create_check_constraint(
        "ck_decisions_recommended_option", "decisions",
        "recommended_option IS NULL OR recommended_option IN ('approve','reject','counter')")
    op.execute("UPDATE decisions SET requested_by = args->>'requested_by' "
               "WHERE args ? 'requested_by'")

    op.create_table(
        "decision_steps",
        sa.Column("id", UUID, primary_key=True),
        _tenant(),
        sa.Column("decision_id", UUID, sa.ForeignKey("decisions.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("position", sa.SmallInteger, nullable=False),
        sa.Column("role_id", UUID, sa.ForeignKey("roles.id")),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("started_at", TS),
        sa.Column("remind_at", TS),
        sa.Column("due_at", TS),
        sa.Column("reminded_at", TS),
        sa.Column("overdue_at", TS),
        sa.Column("first_shown_at", TS),
        sa.Column("decided_by", sa.String(100)),
        sa.Column("on_behalf_of", sa.String(100)),
        sa.Column("decided_at", TS),
        sa.Column("note", sa.String(1000)),
        sa.Column("decision_ms", sa.Integer),
        sa.Column("card_hash", sa.String(64)),
        sa.UniqueConstraint("decision_id", "position"),
        sa.CheckConstraint("status IN ('waiting','pending','approved','rejected','countered',"
                           "'cancelled','superseded')", name="ck_decision_steps_status"),
        sa.CheckConstraint("decision_ms IS NULL OR decision_ms >= 0",
                           name="ck_decision_steps_decision_ms"),
    )
    op.create_index("ix_decision_steps_open", "decision_steps", ["status", "due_at"])
    # One step for every card that exists already, mirroring how it ended.
    op.execute("""
        INSERT INTO decision_steps (id, tenant_id, decision_id, position, role_id, status,
                                    started_at, first_shown_at, decided_by, decided_at, note,
                                    decision_ms)
        SELECT gen_random_uuid(), tenant_id, id, 0, approver_role_id,
               CASE status WHEN 'pending' THEN 'pending' WHEN 'rejected' THEN 'rejected'
                           WHEN 'superseded' THEN 'superseded' ELSE 'approved' END,
               created_at, first_shown_at, decided_by, decided_at, note, decision_ms
        FROM decisions WHERE kind = 'approval'
    """)

    op.create_table(
        "delegations",
        sa.Column("id", UUID, primary_key=True),
        _tenant(),
        sa.Column("kind", sa.String(5), nullable=False),
        sa.Column("from_user_id", UUID, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("to_user_id", UUID, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("decision_id", UUID, sa.ForeignKey("decisions.id", ondelete="CASCADE")),
        sa.Column("starts_at", TS, server_default=NOW, nullable=False),
        sa.Column("ends_at", TS),
        sa.Column("tool_keys", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("max_amount", sa.Numeric(14, 2)),
        sa.Column("note", sa.String(300)),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.Column("revoked_at", TS),
        sa.Column("revoked_by", sa.String(100)),
        sa.CheckConstraint("kind IN ('card','away')", name="ck_delegations_kind"),
        sa.CheckConstraint("(kind = 'card') = (decision_id IS NOT NULL)",
                           name="ck_delegations_card"),
        sa.CheckConstraint("kind = 'card' OR ends_at > starts_at", name="ck_delegations_dates"),
        sa.CheckConstraint("from_user_id <> to_user_id", name="ck_delegations_people"),
        sa.CheckConstraint("max_amount IS NULL OR max_amount > 0",
                           name="ck_delegations_max_amount"),
    )

    op.create_table(
        "approval_settings",
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("time_zone", sa.String(60), nullable=False),
        sa.Column("work_days", JSONB, nullable=False),
        sa.Column("day_start", sa.String(5), nullable=False),
        sa.Column("day_end", sa.String(5), nullable=False),
        sa.Column("holidays", JSONB, nullable=False),
        sa.Column("default_sla_hours", sa.Numeric(6, 2), nullable=False),
        sa.Column("reminder_pct", sa.SmallInteger, nullable=False),
        sa.Column("escalation_role_id", UUID, sa.ForeignKey("roles.id")),
        sa.Column("bottleneck_overdue", sa.SmallInteger, nullable=False),
        sa.Column("bottleneck_wait_ratio", sa.Numeric(4, 2), nullable=False),
        sa.Column("updated_by", sa.String(100), nullable=False),
        sa.Column("updated_at", TS, server_default=NOW, nullable=False),
        sa.CheckConstraint("default_sla_hours > 0", name="ck_approval_settings_sla"),
        sa.CheckConstraint("reminder_pct BETWEEN 10 AND 95", name="ck_approval_settings_reminder"),
    )

    op.create_table(
        "push_subscriptions",
        sa.Column("id", UUID, primary_key=True),
        _tenant(),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("endpoint", sa.String(1000), nullable=False),
        sa.Column("p256dh", sa.String(200), nullable=False),
        sa.Column("auth", sa.String(100), nullable=False),
        sa.Column("user_agent", sa.String(300)),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.Column("last_sent_at", TS),
        sa.Column("failures", sa.SmallInteger, nullable=False, server_default="0"),
        sa.UniqueConstraint("tenant_id", "endpoint"),
    )

    for t in ("decision_steps", "delegations", "approval_settings", "push_subscriptions"):
        enable_tenant_rls(op, t)
    # Steps and delegations are history: they change state but are never deleted by
    # the app (a delegation is revoked, not removed).
    op.execute("REVOKE DELETE, TRUNCATE ON decision_steps, delegations, approval_settings "
               "FROM forge_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON decision_steps, delegations, approval_settings "
               "TO forge_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON push_subscriptions TO forge_app")


def downgrade() -> None:
    for t in ("push_subscriptions", "approval_settings", "delegations", "decision_steps"):
        op.drop_table(t)
    op.drop_constraint("ck_decisions_recommended_option", "decisions")
    for c in ("step_index", "sla_hours", "counter_of", "recommended_option", "requested_by"):
        op.drop_column("decisions", c)
    op.drop_constraint("ck_policy_rules_sla_hours", "policy_rules")
    op.drop_column("policy_rules", "sla_hours")
    op.drop_column("policy_rules", "approval_steps")
