"""Agent runtime (F12.2) and the audit chain (minimal F12.4, decision 6A).

Agent *definitions* (charter, tools, model tier) live in code (decision T4,
app/agents/). These tables hold what a tenant controls and what happened:
settings and autonomy overrides, kill-switches, policy rules, takeovers,
decisions waiting for a person, runs, and the hash-chained audit records.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantMixin, TimestampMixin

UUIDCol = PGUUID(as_uuid=True)


def _now_col() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AgentSetting(IdMixin, TenantMixin, TimestampMixin, Base):
    """A tenant's autonomy level for one agent (decision T3). Without a row the
    agent's default level from its definition applies."""

    __tablename__ = "agent_settings"
    __table_args__ = (UniqueConstraint("tenant_id", "agent_key"),)

    agent_key: Mapped[str] = mapped_column(String(60), nullable=False)
    autonomy_level: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(100), nullable=False)


class AutonomyOverride(IdMixin, TenantMixin, TimestampMixin, Base):
    """A different autonomy level for an account tier or one named account (T3).
    A named account wins over its tier; the lowest matching level applies."""

    __tablename__ = "autonomy_overrides"

    agent_key: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(15), nullable=False)  # account_tier | account
    account_tier: Mapped[str | None] = mapped_column(String(20))
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("accounts.id"))
    autonomy_level: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)


class KillSwitch(IdMixin, TenantMixin, Base):
    """Stops agents: every agent in the tenant, one agent, or every agent on one
    account. Active while released_at is empty."""

    __tablename__ = "kill_switches"

    scope: Mapped[str] = mapped_column(String(10), nullable=False)  # tenant | agent | account
    agent_key: Mapped[str | None] = mapped_column(String(60))
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("accounts.id"))
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = _now_col()
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_by: Mapped[str | None] = mapped_column(String(100))


class PolicyRule(IdMixin, TenantMixin, TimestampMixin, Base):
    """One policy rule (decision T1): when an agent proposes an action and every
    condition holds, the outcome applies. block > ask_person > allow."""

    __tablename__ = "policy_rules"

    agent_key: Mapped[str | None] = mapped_column(String(60))  # None = any agent
    tool_key: Mapped[str | None] = mapped_column(String(80))  # None = any action
    # [{"fact": "amount", "op": "gt", "value": 100000}, ...] from app.agents.registry.FACTS
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    outcome: Mapped[str] = mapped_column(String(15), nullable=False)  # allow|ask_person|block
    approver_role_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("roles.id"))
    note: Mapped[str | None] = mapped_column(String(300))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(100), nullable=False)


class RecordTakeover(IdMixin, TenantMixin, Base):
    """A person took a record over (decision T8): no agent acts on it until it is
    handed back."""

    __tablename__ = "record_takeovers"

    record_type: Mapped[str] = mapped_column(String(20), nullable=False)
    record_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, nullable=False, index=True)
    taken_by: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime] = _now_col()
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_by: Mapped[str | None] = mapped_column(String(100))


class AgentRun(IdMixin, TenantMixin, Base):
    """One piece of agent work started by a trigger; its steps are audit records
    with this run_id."""

    __tablename__ = "agent_runs"

    agent_key: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    trigger: Mapped[str] = mapped_column(String(80), nullable=False)
    trigger_ref: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(15), nullable=False, default="running")
    started_at: Mapped[datetime] = _now_col()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Decision(IdMixin, TenantMixin, Base):
    """A card for a person (decision T2): an action that needs approval, or a
    notice of something an agent did (L1) or was stopped from doing."""

    __tablename__ = "decisions"

    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # approval|notice|alert|task
    # approval: pending -> approved | rejected | superseded | failed
    # notice:   open -> acknowledged
    # alert:    open -> acknowledged -> resolved (by itself when the problem is gone)
    # task:     open -> done (by itself when nothing is left to do)
    status: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    agent_key: Mapped[str] = mapped_column(String(60), nullable=False)
    tool_key: Mapped[str] = mapped_column(String(80), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("agent_runs.id"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    recommendation: Mapped[str | None] = mapped_column(Text)
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    args: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    approver_role_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("roles.id"))
    record_type: Mapped[str | None] = mapped_column(String(20))
    record_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), index=True)
    created_at: Mapped[datetime] = _now_col()
    decided_by: Mapped[str | None] = mapped_column(String(100))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String(1000))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class AuditRecord(TenantMixin, Base):
    """Append-only, hash-chained (decision T6: one chain per tenant). The database
    refuses UPDATE and DELETE; app/audit/chain.py verifies the chain."""

    __tablename__ = "audit_records"
    __table_args__ = (UniqueConstraint("tenant_id", "seq"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    agent_key: Mapped[str | None] = mapped_column(String(60), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, index=True)
    record_type: Mapped[str | None] = mapped_column(String(20))
    record_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)


class SandboxAction(IdMixin, TenantMixin, Base):
    """Written only by the test-only Sandbox Agent (decision T7), so the policy gate
    can be exercised with amounts before real money-moving agents exist."""

    __tablename__ = "sandbox_actions"

    account_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("accounts.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = _now_col()
