"""Sandbox Agent (test only, decision T7): records pretend payments with an amount,
so policy rules on money ("ask a person above ₹1,00,000") can be tried and tested
before real money-moving agents exist. It writes only to sandbox_actions.
"""

import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.controls import account_tiers
from app.agents.registry import (
    L2,
    L3,
    AgentContext,
    AgentDef,
    ToolDef,
    Touches,
    register_agent,
    register_tool,
)
from app.core.format import inr
from app.models import Account, SandboxAction

AGENT_KEY = "sandbox"
PAYMENT_TOOL = "sandbox.record_payment"


class PaymentArgs(BaseModel):
    account_id: uuid.UUID | None = None
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    note: str | None = Field(default=None, max_length=300)


async def _facts(session: AsyncSession, a: PaymentArgs) -> dict[str, Any]:
    facts: dict[str, Any] = {"amount": float(a.amount)}
    if a.account_id:
        facts["account_tier"] = (await account_tiers(session, [a.account_id]))[a.account_id]
    return facts


async def _touches(session: AsyncSession, a: PaymentArgs) -> Touches:
    return Touches(accounts=(a.account_id,) if a.account_id else ())


async def _describe(session: AsyncSession, a: PaymentArgs, facts: dict[str, Any]) -> str:
    who = ""
    if a.account_id:
        acc = await session.get(Account, a.account_id)
        who = f" for {acc.name}" if acc else ""
    return f"Record a test payment of {inr(a.amount)}{who}"


async def _run(session: AsyncSession, ctx: AgentContext, a: PaymentArgs) -> dict[str, Any]:
    row = SandboxAction(tenant_id=ctx.tenant_id, account_id=a.account_id, amount=a.amount,
                        note=a.note, created_by=ctx.approved_by or ctx.actor)
    session.add(row)
    await session.flush()
    return {"sandbox_action_id": str(row.id), "amount": str(a.amount)}


register_tool(ToolDef(
    key=PAYMENT_TOOL,
    title="Record a test payment",
    description="Writes a pretend payment to the sandbox table. Nothing real changes.",
    args=PaymentArgs, run=_run, facts=("amount", "account_tier"),
    compute_facts=_facts, touches=_touches, describe=_describe,
    evidence_class="customer_money",
))

register_agent(AgentDef(
    key=AGENT_KEY,
    name="Sandbox Agent",
    purpose="Test only. Lets you try policy rules on amounts before real agents that move "
            "money exist. It records pretend payments and nothing else.",
    tools=(PAYMENT_TOOL,),
    default_level=L2, max_level=L3,
    triggers=("'Try it' on the Agents screen", "tests"),
    test_only=True,
))
