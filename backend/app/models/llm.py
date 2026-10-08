import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantMixin


class LlmCall(IdMixin, TenantMixin, Base):
    """One model call through the gateway: used for budgets, cost and tracing."""

    __tablename__ = "llm_calls"

    task: Mapped[str] = mapped_column(String(100), nullable=False)
    tier: Mapped[str] = mapped_column(String(30), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # ok | error | budget_blocked
    error: Mapped[str | None] = mapped_column(String(1000))
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TenantLlmBudget(Base):
    """Optional per-tenant monthly LLM budget.

    Without a row, settings.llm_default_monthly_budget_usd applies."""

    __tablename__ = "tenant_llm_budgets"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )
    monthly_budget_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
