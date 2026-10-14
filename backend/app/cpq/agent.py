"""The Cost Engine agent (F3.2, decisions K10 A and "manage it as a card in Decisions").

A quote line whose cost is incomplete (a rate, formula, unit or BOM part is missing or
invalid) or unknown (no cost model, BOM or rate card covers it) goes to a person with
costs.manage as a Decisions card. They can

  approve   use the cost as worked out (an unknown cost is then priced by hand, and
            the quote goes for approval, K7 A)
  counter   with the unit cost to use instead
  reject    leave it waiting: someone fixes what is missing, and working the costs out
            again closes the card

The card names exactly what was reviewed (its fingerprint): if the line's cost then
changes for another reason, the old card is closed and a new one asks again.
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

AGENT_KEY = "cost"
REVIEW_TOOL = "cost.review"
MAX_UNIT_COST = Decimal("1e12")


class ReviewArgs(BaseModel):
    quote_id: uuid.UUID
    line_id: uuid.UUID
    fingerprint: str = Field(max_length=64)
    quote: str = Field(max_length=60)  # Q/2026-27/0003 R1, line 2
    line: str = Field(max_length=500)
    status: Literal["incomplete", "unknown"]
    worked_out: str | None = None  # the unit cost as worked out, if any
    problems: list[str] = Field(default_factory=list, max_length=30)
    # Counter (H2): the unit cost to use instead.
    unit_cost: str | None = Field(default=None, max_length=30)

    @field_validator("unit_cost")
    @classmethod
    def _unit_cost(cls, v: str | None) -> str | None:
        if v is None or not str(v).strip():
            return None
        try:
            d = Decimal(str(v).replace(",", "").replace("₹", "").strip())
        except InvalidOperation:
            raise ValueError("The unit cost must be a number") from None
        if not d.is_finite() or d < 0 or d >= MAX_UNIT_COST:
            raise ValueError("The unit cost must be a number from 0 up")
        return format(d.quantize(Decimal("0.01")), "f")


async def _facts(session: AsyncSession, a: ReviewArgs) -> dict[str, Any]:
    # Not policy facts: what the tool's own ask rule says on the card.
    return {"cost_status": a.status, "cost_problems": a.problems[:8]}


def _always(facts: dict[str, Any]) -> str:
    problems = facts.get("cost_problems") or []
    if facts.get("cost_status") == "unknown":
        head = "No cost model, ERPNext BOM or rate card covers this line"
    else:
        head = "Part of the cost could not be worked out"
    return f"{head}: {'; '.join(problems)}" if problems else head


async def _touches(session: AsyncSession, a: ReviewArgs) -> Touches:
    return Touches(records=(("quote", a.quote_id),))


async def _describe(session: AsyncSession, a: ReviewArgs, facts: dict[str, Any]) -> str:
    what = "unknown" if a.status == "unknown" else "incomplete"
    return f"Review the {what} cost of {a.quote}: {a.line[:80]}"


async def _run(session: AsyncSession, ctx: AgentContext, a: ReviewArgs) -> dict[str, Any]:
    ln = await session.get(QuoteLine, a.line_id, with_for_update=True)
    rev = await session.get(QuoteRevision, ln.revision_id) if ln else None
    if ln is None or rev is None or rev.quote_id != a.quote_id:
        raise AppError("The quote line is no longer there")
    if rev.status != "draft":
        raise AppError("The revision was sent; its costs can't change any more")
    cost = dict(ln.cost or {})
    if cost.get("review_key") != a.fingerprint:
        raise AppError("The line's cost changed after this card was made, so it was worked "
                       "out again")
    review = {"key": a.fingerprint, "by": ctx.approved_by or ctx.actor,
              "at": datetime.now(UTC).isoformat(), "unit_cost": a.unit_cost,
              "worked_out": a.worked_out}
    ln.cost = {**cost, "review": review}
    ln.cost_status = "reviewed"
    if a.unit_cost is not None:
        ln.unit_cost = Decimal(a.unit_cost)
    ln.updated_at = datetime.now(UTC)
    await session.flush()
    return {"quote_id": str(a.quote_id), "line_id": str(a.line_id),
            "unit_cost": a.unit_cost or a.worked_out}


register_tool(ToolDef(
    key=REVIEW_TOOL,
    title="Review an incomplete cost",
    description="A quote line's cost could not be fully worked out (a rate, formula, unit or "
                "BOM part is missing), or nothing covers it. A person who manages costs uses "
                "it as it is, sets the unit cost, or sends it back to be fixed.",
    args=ReviewArgs, run=_run, compute_facts=_facts, touches=_touches, describe=_describe,
    permission="costs.manage", decider_permission="costs.manage",
    counter_fields=("unit_cost",), ask_when=_always, evidence_class="customer_money",
))

register_agent(AgentDef(
    key=AGENT_KEY,
    name="Cost Engine",
    purpose="Works out what each quote line costs, from the family's cost model, the "
            "item's ERPNext BOM or the rate card, and keeps the breakdown. It works the "
            "costs out again when a rate, BOM or cost model changes, and marks sent quotes "
            "for repricing. A cost it can't fully work out always goes to a person who "
            "manages costs.",
    tools=(REVIEW_TOOL,),
    default_level=L2, max_level=L2,
    triggers=("a quote is started or changed", "a rate, BOM or cost model changes",
              "the hourly cost check"),
    model_tier=None,
    permissions=("quotes.read", "costs.view", "costs.manage"),
))


def _recommendation(ln: QuoteLine) -> str:
    cost = ln.cost or {}
    if cost.get("status") == "unknown":
        return ("Set up a cost model or rate card for the family, or a default BOM in "
                "ERPNext, then work the costs out again: this card then closes by itself. "
                "Approve to price the line by hand (the quote then goes for approval), or "
                "counter with the unit cost to use.")
    from app.cpq.costing import rs

    per = cost.get("per_unit")
    known = f" ({rs(Decimal(per))} per unit from what is known)" if per else ""
    return ("Add what is missing and work the costs out again: this card then closes by "
            f"itself. Approve to use the cost as worked out{known}, or counter with the unit "
            "cost to use.")


async def ask_review(session: AsyncSession, q: Quote, rev: QuoteRevision,
                     lines: list[QuoteLine]) -> int:
    """A card for each line whose cost needs a review, unless one for exactly this
    cost is waiting or was sent back. Returns how many cards were made."""
    ctx = None
    made = 0
    for ln in lines:
        cost = ln.cost or {}
        key = cost.get("review_key")
        if ln.cost_review_id is not None:
            d = await session.get(Decision, ln.cost_review_id)
            if d is not None and d.args.get("fingerprint") == key \
                    and d.status in ("pending", "rejected"):
                continue
        await runtime.supersede(session, f"cost:{ln.id}", "system:costs",
                                "The cost changed; a new card asks again")
        if ctx is None:
            ctx = await runtime.start_run(session, q.tenant_id, AGENT_KEY, "quote costed",
                                          trigger_ref=str(q.id))
        args = ReviewArgs(quote_id=q.id, line_id=ln.id, fingerprint=key,
                          quote=f"{q.number} R{rev.number}, line {ln.position}",
                          line=ln.description[:500], status=cost.get("status", "unknown"),
                          worked_out=cost.get("per_unit"),
                          problems=[p[:300] for p in (cost.get("problems") or [])[:30]]
                          or ([cost["why"][:300]] if cost.get("why") else []))
        out = await runtime.call_tool(session, ctx, REVIEW_TOOL, args,
                                      recommendation=_recommendation(ln),
                                      dedupe_key=f"cost:{ln.id}")
        ln.cost_review_id = out.decision_id
        made += out.decision_id is not None
    if ctx is not None:
        await runtime.finish_run(session, ctx, summary={"cards": made})
    await session.flush()
    return made
