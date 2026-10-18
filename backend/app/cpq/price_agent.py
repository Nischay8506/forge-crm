"""The Quote agent's price approval (F3.3, decision C2 A).

A quote line that cannot stand on its own goes to a person who approves prices:

  below_floor  its margin is under the family's floor
  no_cost      its cost is not known, so no margin can be worked out
  no_floor     no pricing policy says what its floor is
  no_price     nothing priced it: no ERP price, no cost to work from

They can

  approve   send the quote with the price as it is
  counter   with the unit price to use instead
  reject    leave it waiting: someone fixes the price, the cost or the policy, and
            pricing the quote again closes the card

The full matrix of who approves which discount, with chains, deadlines and escalation,
is F3.4 (decision C5 A). Until it is built, one role approves prices.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import runtime
from app.agents.registry import (
    L2,
    AgentContext,
    AgentDef,
    ToolDef,
    Touches,
    register_agent,
    register_tool,
)
from app.core.errors import AppError
from app.models import Decision, Quote, QuoteLine, QuoteRevision

AGENT_KEY = "quote"
APPROVE_TOOL = "price.approve"
MAX_PRICE = Decimal("1e12")


class ApproveArgs(BaseModel):
    quote_id: uuid.UUID
    line_id: uuid.UUID
    fingerprint: str = Field(max_length=64)
    quote: str = Field(max_length=60)  # Q/2026-27/0003 R1, line 2
    line: str = Field(max_length=500)
    status: Literal["below_floor", "no_cost", "no_floor", "no_price"]
    priced: str | None = None  # the price as Forge worked it out, if any
    margin_pct: str | None = Field(default=None, max_length=12)
    floor_margin_pct: str | None = Field(default=None, max_length=12)
    problems: list[str] = Field(default_factory=list, max_length=30)
    # Counter (H2): the unit price to use instead.
    unit_price: str | None = Field(default=None, max_length=30)

    @field_validator("unit_price")
    @classmethod
    def _unit_price(cls, v: str | None) -> str | None:
        if v is None or not str(v).strip():
            return None
        try:
            d = Decimal(str(v).replace(",", "").replace("₹", "").strip())
        except InvalidOperation:
            raise ValueError("The unit price must be a number") from None
        if not d.is_finite() or d < 0 or d >= MAX_PRICE:
            raise ValueError("The unit price must be a number from 0 up")
        return format(d.quantize(Decimal("0.01")), "f")


async def _facts(session: AsyncSession, a: ApproveArgs) -> dict[str, Any]:
    # Not policy facts: what the tool's own ask rule puts on the card.
    return {"price_status": a.status, "price_problems": a.problems[:8]}


HEADS = {
    "below_floor": "This line is priced below its floor",
    "no_cost": "This line's cost is not known, so its margin cannot be checked",
    "no_floor": "No pricing policy says what this line's floor is",
    "no_price": "Nothing could price this line",
}


def _always(facts: dict[str, Any]) -> str:
    head = HEADS.get(facts.get("price_status"), "This line's price needs a person")
    problems = facts.get("price_problems") or []
    return f"{head}: {'; '.join(problems)}" if problems else head


async def _touches(session: AsyncSession, a: ApproveArgs) -> Touches:
    return Touches(records=(("quote", a.quote_id),))


async def _describe(session: AsyncSession, a: ApproveArgs, facts: dict[str, Any]) -> str:
    return f"Approve the price of {a.quote}: {a.line[:80]}"


async def _run(session: AsyncSession, ctx: AgentContext, a: ApproveArgs) -> dict[str, Any]:
    ln = await session.get(QuoteLine, a.line_id, with_for_update=True)
    rev = await session.get(QuoteRevision, ln.revision_id) if ln else None
    if ln is None or rev is None or rev.quote_id != a.quote_id:
        raise AppError("The quote line is no longer there")
    if rev.status != "draft":
        raise AppError("The revision was sent; its prices can't change any more")
    price = dict(ln.price or {})
    if price.get("review_key") != a.fingerprint:
        raise AppError("The line's price changed after this card was made, so it was "
                       "worked out again")
    approval = {"key": a.fingerprint, "by": ctx.approved_by or ctx.actor,
                "at": datetime.now(UTC).isoformat(), "unit_price": a.unit_price,
                "priced": a.priced}
    ln.price = {**price, "approval": approval}
    if a.unit_price is not None:
        ln.unit_price = Decimal(a.unit_price)
        cost = price.get("unit_cost")
        if cost not in (None, "") and Decimal(a.unit_price) > 0:
            ln.margin_pct = ((Decimal(a.unit_price) - Decimal(cost))
                             / Decimal(a.unit_price) * 100).quantize(Decimal("0.001"))
    ln.updated_at = datetime.now(UTC)
    await session.flush()
    return {"quote_id": str(a.quote_id), "line_id": str(a.line_id),
            "unit_price": a.unit_price or a.priced}


register_tool(ToolDef(
    key=APPROVE_TOOL,
    title="Approve a price below the floor",
    description="A quote line is priced under its family's floor, has no known cost, has no "
                "floor to check against, or could not be priced at all. A person who "
                "approves prices sends it as it is, sets the unit price, or sends it back.",
    args=ApproveArgs, run=_run, compute_facts=_facts, touches=_touches, describe=_describe,
    permission="quotes.work", decider_permission="prices.approve",
    counter_fields=("unit_price",), ask_when=_always, evidence_class="customer_money",
))

register_agent(AgentDef(
    key=AGENT_KEY,
    name="Quote agent",
    purpose="Prices each quote line: a customer's own price in the ERP first, then the "
            "selling price list, then the cost at the family's target margin, with the "
            "tier discount, the quantity break and the rep's ask on top. It checks every "
            "line and the whole quote against the floor, and a line that cannot stand on "
            "its own always goes to a person who approves prices.",
    tools=(APPROVE_TOOL,),
    default_level=L2, max_level=L2,
    triggers=("a quote is priced", "a cost or a pricing policy changes"),
    model_tier=None,
    permissions=("quotes.read", "quotes.work", "costs.view", "prices.manage"),
))


def _recommendation(ln: QuoteLine) -> str:
    price = ln.price or {}
    status = price.get("status")
    if status == "no_price":
        return ("Set a price for the item in the ERP, or a cost model and a pricing policy "
                "for the family, then price the quote again: this card then closes by "
                "itself. Approve to price the line by hand, or counter with the unit price.")
    if status == "no_floor":
        return ("Set a pricing policy for the family or the company (Quotes → Pricing "
                "policy), then price the quote again. Approve to send this price without a "
                "floor check, or counter with the unit price.")
    if status == "no_cost":
        return ("The line's cost is not known, so Forge cannot say what margin this price "
                "leaves. Settle the cost first (its card is in Decisions), or approve the "
                "price as it is, or counter with the unit price.")
    floor = price.get("floor_margin_pct")
    margin = price.get("margin_pct")
    gap = (f" ({margin}% against a {floor}% floor)" if margin and floor else "")
    return (f"Approve to send this price as it is{gap}, counter with the unit price to use, "
            "or reject and the rep reprices. Raising the quantity, the payment terms or the "
            "specification are the usual ways back above the floor.")


async def ask_approval(session: AsyncSession, q: Quote, rev: QuoteRevision,
                       lines: list[QuoteLine]) -> int:
    """A card for each line whose price needs a person, unless one for exactly this
    price is waiting or was sent back. Returns how many cards were made."""
    ctx = None
    made = 0
    for ln in lines:
        price = ln.price or {}
        key = price.get("review_key")
        if ln.price_review_id is not None:
            d = await session.get(Decision, ln.price_review_id)
            if d is not None and d.args.get("fingerprint") == key \
                    and d.status in ("pending", "rejected"):
                continue
        await runtime.supersede(session, f"price:{ln.id}", "system:quotes",
                                "The price changed; a new card asks again")
        if ctx is None:
            ctx = await runtime.start_run(session, q.tenant_id, AGENT_KEY, "quote priced",
                                          trigger_ref=str(q.id))
        args = ApproveArgs(quote_id=q.id, line_id=ln.id, fingerprint=key,
                           quote=f"{q.number} R{rev.number}, line {ln.position}",
                           line=ln.description[:500], status=price.get("status"),
                           priced=price.get("unit_price"),
                           margin_pct=price.get("margin_pct"),
                           floor_margin_pct=price.get("floor_margin_pct"),
                           problems=[p[:300] for p in (price.get("problems") or [])[:30]])
        out = await runtime.call_tool(session, ctx, APPROVE_TOOL, args,
                                      recommendation=_recommendation(ln),
                                      dedupe_key=f"price:{ln.id}")
        ln.price_review_id = out.decision_id
        made += out.decision_id is not None
    if ctx is not None:
        await runtime.finish_run(session, ctx, summary={"cards": made})
    await session.flush()
    return made
