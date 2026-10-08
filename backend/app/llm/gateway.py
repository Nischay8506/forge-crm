"""LLM gateway: the only way application code calls a model.

    result = await complete(session, tenant_id, task="enquiry.classify",
                            tier="fast", messages=[...])

It enforces the tenant's monthly budget, tries the tier's routes in order
(fallback on error), and records every attempt in `llm_calls` for cost,
latency and tracing. Agents never hold provider keys or pick models.
"""

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.llm import providers as providers_mod
from app.llm import routing
from app.models import LlmCall, TenantLlmBudget


class LlmError(Exception):
    pass


class BudgetExceeded(LlmError):
    pass


@dataclass
class LlmResult:
    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


def _cost(provider: str, model: str, tin: int, tout: int) -> Decimal:
    price = routing.PRICES_PER_MTOK.get(f"{provider}/{model}")
    if price is None:
        return Decimal(0)
    return (Decimal(str(price[0])) * tin + Decimal(str(price[1])) * tout) / Decimal(1_000_000)


async def month_spend(session: AsyncSession, tenant_id: UUID) -> Decimal:
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = await session.scalar(
        select(func.coalesce(func.sum(LlmCall.cost_usd), 0)).where(
            LlmCall.tenant_id == tenant_id, LlmCall.created_at >= start
        )
    )
    return Decimal(total)


async def monthly_budget(session: AsyncSession, tenant_id: UUID) -> Decimal:
    row = await session.get(TenantLlmBudget, tenant_id)
    if row is not None:
        return row.monthly_budget_usd
    return Decimal(str(get_settings().llm_default_monthly_budget_usd))


async def complete(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    task: str,
    tier: str,
    messages: list[dict[str, str]],
    trace_id: str | None = None,
    **kwargs: Any,
) -> LlmResult:
    routes = routing.ROUTES.get(tier)
    if not routes:
        raise LlmError(f"Unknown tier: {tier}")

    if await month_spend(session, tenant_id) >= await monthly_budget(session, tenant_id):
        session.add(
            LlmCall(
                tenant_id=tenant_id,
                task=task,
                tier=tier,
                provider="-",
                model="-",
                status="budget_blocked",
                trace_id=trace_id,
            )
        )
        await session.flush()
        raise BudgetExceeded("Monthly LLM budget reached for this tenant")

    errors: list[str] = []
    for route in routes:
        provider = providers_mod.PROVIDERS[route.provider]
        started = time.monotonic()
        try:
            resp = await provider.chat(route.model, messages, **kwargs)
        except Exception as exc:  # noqa: BLE001 - recorded, then fall back to the next route
            msg = f"{type(exc).__name__}: {exc}"[:1000]
            errors.append(f"{route.provider}/{route.model}: {msg}")
            session.add(
                LlmCall(
                    tenant_id=tenant_id,
                    task=task,
                    tier=tier,
                    provider=route.provider,
                    model=route.model,
                    status="error",
                    error=msg,
                    trace_id=trace_id,
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
            )
            continue
        cost = _cost(route.provider, route.model, resp.input_tokens, resp.output_tokens)
        session.add(
            LlmCall(
                tenant_id=tenant_id,
                task=task,
                tier=tier,
                provider=route.provider,
                model=route.model,
                status="ok",
                input_tokens=resp.input_tokens,
                output_tokens=resp.output_tokens,
                cost_usd=cost,
                trace_id=trace_id,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        )
        await session.flush()
        return LlmResult(
            resp.text, route.provider, route.model, resp.input_tokens, resp.output_tokens, cost
        )

    await session.flush()
    raise LlmError("All routes failed: " + " | ".join(errors))
