"""The discount approval a whole quote needs, and the card that asks for it (F3.4, Z5 A).

F3.3 raises a card per line, for a price Forge could not work out: no cost, no price, no
floor. F3.4 asks the other question, about the quote as a whole: **this is the discount the
rep wants, who signs it off?** The answer is the company's matrix (app/cpq/doa.py), and
there is **one card for the quote** (Z5 A), with the lines' numbers on it, rather than one
per discounted line.

The two are not rivals. A line Forge could not price is a cost problem and goes to whoever
manages costs; the discount is the quote's and goes to whoever the matrix names. A quote can
need both, and then the rep has one cost card per broken line and one discount card.

What the approver is shown (Z7 A) is the ask and what it costs in margin and money, the
lines it is on, the floor it breaks, what the customer owes and how they have paid from the
ERP with its last-synced time beside it (H5, through app/approvals/card.py), the rep's own
reason and competitor claim **marked as the rep's claim, not as something Forge checked**,
and the quote's history. All of it is fingerprinted, so the card is validated against what
the approver actually had on screen (H7).

The chain comes from the matrix through ToolDef.chain_for, not from this module calling the
approvals engine in its own way, so everything Step 5 guarantees still holds: step 1 decides
before step 2, a rejection ends the chain, the requester can never decide their own card,
and a counter goes back through the gate and is re-chained by its own numbers.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import runtime
from app.agents.registry import AgentContext, ToolDef, Touches, register_tool
from app.audit import chain as audit
from app.core.errors import AppError
from app.cpq import doa
from app.models import Decision, Quote, QuoteLine, QuoteRevision

AGENT_KEY = "quote"
DISCOUNT_TOOL = "discount.approve"
MAX_PCT = Decimal(100)
# A line is only interesting to the approver if it was actually discounted.
SHOW_LINES = 20


def _dec(v: Any) -> Decimal | None:
    try:
        d = Decimal(str(v).replace(",", "").replace("₹", "").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None
    return d if d.is_finite() else None


def _num(v: Decimal | None, places: str = "0.01") -> str | None:
    return None if v is None else format(v.quantize(Decimal(places)), "f")


# What the quote looks like to the matrix --------------------------------------------------

def quote_facts(lines: list[Any], whole: dict[str, Any]) -> dict[str, Any]:
    """The quote's own numbers: the discount it asks for, what it is worth, the margin it
    leaves and the floor its lines weigh out to.

    The discount is the quote's, not a line's (Z5 A): it is what the whole quote gives away
    against its own list prices, so five lines at 20% and one at nothing come to the quote's
    real depth rather than to whichever line looks worst. A line with no list price has no
    discount to measure and is counted instead of guessed at.
    """
    listed = Decimal(0)
    sold = Decimal(0)
    rows: list[dict[str, Any]] = []
    left_out = 0
    for ln in lines:
        price, qty, lp = ln.unit_price, ln.qty, ln.list_price
        if price is None or qty is None or lp is None or lp <= 0:
            left_out += 1
            continue
        listed += lp * qty
        sold += price * qty
        if price >= lp:
            continue
        rows.append({
            "position": ln.position, "description": ln.description[:200],
            "qty": _num(qty, "0.0001"), "unit": ln.unit,
            "list_price": _num(lp), "unit_price": _num(price),
            "discount_pct": _num((lp - price) / lp * 100),
            "given_away": _num((lp - price) * qty),
            "margin_pct": _num(ln.margin_pct, "0.001"),
            "floor_margin_pct": (ln.price or {}).get("floor_margin_pct"),
            "asked": (ln.ask or {}) or None,
        })
    depth = ((listed - sold) / listed * 100) if listed > 0 else Decimal(0)
    rows.sort(key=lambda r: Decimal(r["given_away"]), reverse=True)
    return {
        "discount_pct": _num(depth), "amount": whole.get("total"),
        "margin_pct": whole.get("margin_pct"),
        "floor_margin_pct": whole.get("floor_margin_pct"),
        "list_total": _num(listed), "given_away": _num(listed - sold),
        "discounted_lines": rows[:SHOW_LINES],
        "discounted_line_count": len(rows), "lines_not_measured": left_out,
    }


@dataclass
class _AsIf:
    """A quote line as it would be at a different discount.

    pricing.totals() and quote_facts() read a line through these few attributes, so a
    countered discount can be weighed with exactly the same arithmetic as the real one
    instead of a second copy of it that could drift.
    """

    position: int
    description: str
    qty: Decimal | None
    unit: str | None
    list_price: Decimal | None
    unit_price: Decimal | None
    margin_pct: Decimal | None
    price: dict[str, Any] | None
    ask: dict[str, Any] | None


def at_discount(lines: list[QuoteLine], allowed: Decimal) -> list[_AsIf]:
    """Every line that was discounted, re-priced at exactly `allowed` percent off its list.

    What an approver countering a discount is actually deciding about: the quote as it will
    be if they allow 12% instead of 20%, margin and floor included, not the quote as the rep
    asked for it.
    """
    out: list[_AsIf] = []
    for ln in lines:
        price, lp = ln.unit_price, ln.list_price
        margin = ln.margin_pct
        if lp is not None and lp > 0 and price is not None and price < lp:
            price = (lp * (1 - allowed / 100)).quantize(Decimal("0.01"))
            cost = _dec((ln.price or {}).get("unit_cost"))
            margin = (((price - cost) / price * 100).quantize(Decimal("0.001"))
                      if cost is not None and price > 0 else None)
        out.append(_AsIf(position=ln.position, description=ln.description, qty=ln.qty,
                         unit=ln.unit, list_price=lp, unit_price=price, margin_pct=margin,
                         price=ln.price, ask=ln.ask))
    return out


def facts_for(got: dict[str, Any]) -> doa.Facts:
    return doa.Facts(
        discount_pct=_dec(got.get("discount_pct")) or Decimal(0),
        value=_dec(got.get("amount")) or Decimal(0),
        margin_pct=_dec(got.get("margin_pct")),
        floor_margin_pct=_dec(got.get("floor_margin_pct")),
    )


def fingerprint(got: dict[str, Any]) -> str:
    """What a person approves: the depth, the money, the margin against the floor and the
    lines it is on. A changed quote asks again; the same quote does not."""
    content = {k: got.get(k) for k in ("discount_pct", "amount", "margin_pct",
                                       "floor_margin_pct", "given_away")}
    content["lines"] = [[r["position"], r["unit_price"], r["qty"]]
                        for r in got.get("discounted_lines") or []]
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:32]


async def look(session: AsyncSession, q: Quote, rev: QuoteRevision) -> dict[str, Any]:
    """Everything about this revision's discount: its numbers, the chain the matrix asks
    for, and the card that is open on it, if any. The quote screen and the card are both
    built from this, so they can never disagree."""
    from app.cpq import pricing, quotes

    lines = await quotes.lines_of(session, rev)
    got = quote_facts(lines, pricing.totals(lines))
    f = facts_for(got)
    day = await quotes.today(session, q.tenant_id)
    matrix = await doa.in_force(session, q.tenant_id, day)
    chain = await doa.quote_chain(session, q.tenant_id, day, f)
    key = fingerprint(got)
    card = await open_card(session, rev)
    return {"facts": got, "below_floor": f.below_floor, "fingerprint": key,
            "chain": chain, "matrix_id": str(matrix.id) if matrix else None,
            "at": day, "card": card,
            # A card raised against other numbers is not this quote's approval any more.
            "settled": bool(card is not None and card.status == "approved"
                            and card.args.get("fingerprint") == key)}


async def open_card(session: AsyncSession, rev: QuoteRevision) -> Decision | None:
    """The newest discount card on this revision, whatever became of it."""
    return (await session.scalars(
        select(Decision).where(Decision.tool_key == DISCOUNT_TOOL,
                               Decision.record_type == "quote",
                               Decision.dedupe_key == f"discount:{rev.id}")
        .order_by(Decision.created_at.desc()).limit(1))).first()


# The tool ---------------------------------------------------------------------------------

class ApproveArgs(BaseModel):
    quote_id: uuid.UUID
    revision_id: uuid.UUID
    account_id: uuid.UUID | None = None  # so the card can show the ERP figures (Z7 A)
    fingerprint: str = Field(max_length=64)
    quote: str = Field(max_length=60)  # Q/2026-27/0003 R1
    # The rep's own words. Shown as the rep's claim, never as a fact Forge checked (Z7 A).
    why: str | None = Field(default=None, max_length=1000)
    competitor: str | None = Field(default=None, max_length=300)
    # Counter (H2): the discount the approver will allow instead.
    discount_pct_allowed: str | None = Field(default=None, max_length=12)

    @field_validator("discount_pct_allowed")
    @classmethod
    def _allowed(cls, v: str | None) -> str | None:
        if v is None or not str(v).strip():
            return None
        d = _dec(v)
        if d is None or not (0 <= d <= MAX_PCT):
            raise ValueError("The discount must be a percentage from 0 to 100")
        return format(d.quantize(Decimal("0.01")), "f")


async def _facts(session: AsyncSession, a: ApproveArgs) -> dict[str, Any]:
    """The quote's numbers and what the matrix makes of them.

    Worked out here, from the quote as it stands, rather than taken from the card's
    arguments, because this runs again on a countered ask: the chain a counter needs is
    the chain its own numbers need.
    """
    q = await session.get(Quote, a.quote_id)
    rev = await session.get(QuoteRevision, a.revision_id)
    if q is None or rev is None or rev.quote_id != q.id:
        raise AppError("The quote is no longer there")
    got = await look(session, q, rev)
    allowed = _dec(a.discount_pct_allowed)
    numbers = got["facts"]
    if allowed is not None:
        # A countered ask is judged as the quote it would become, not as the one the rep
        # asked for: its depth, the money it gives away, and the margin it leaves.
        from app.cpq import pricing, quotes

        as_if = at_discount(await quotes.lines_of(session, rev), allowed)
        numbers = quote_facts(as_if, pricing.totals(as_if))
        f = facts_for(numbers)
        chain = await doa.quote_chain(session, q.tenant_id, got["at"], f)
        got = {**got, "facts": numbers, "chain": chain, "below_floor": f.below_floor}
    out: dict[str, Any] = {
        k: numbers.get(k) for k in ("discount_pct", "amount", "margin_pct",
                                    "floor_margin_pct")}
    out.update({
        "doa_rows": [s.out() for s in got["chain"].stages],
        "doa_chain": [s.role for s in got["chain"].stages],
        "doa_problems": got["chain"].problems,
        "doa_matrix_id": got["matrix_id"],
        "quote_numbers": numbers,
    })
    return out


def _chain(facts: dict[str, Any]) -> tuple[list[uuid.UUID], Decimal | None]:
    """The matrix's chain, as roles in order (Z2 A, Z6 A). Read from the facts, which were
    worked out from the quote itself, so the counter path is chained the same way."""
    steps: list[uuid.UUID] = []
    for role in facts.get("doa_chain") or []:
        try:
            steps.append(uuid.UUID(str(role)))
        except ValueError:
            continue
    return steps, doa.SLA_HOURS


def _ask_when(facts: dict[str, Any]) -> str | None:
    """Why a person must decide, in the company's own words where it gave any.

    A problem with the rulebook is itself a reason to ask: a company that has set no
    matrix at all does not thereby get its discounts waved through (doa.NO_MATRIX).
    """
    problems = facts.get("doa_problems") or []
    rows = facts.get("doa_rows") or []
    reasons: list[str] = []
    for s in rows:
        for why in s.get("because") or []:
            if why not in reasons:
                reasons.append(why)
    if problems and not rows:
        return "; ".join(problems)
    if not reasons:
        return "; ".join(problems) or None
    out = "The rulebook asks for this: " + "; ".join(reasons)
    return f"{out}. {'; '.join(problems)}" if problems else out


async def _touches(session: AsyncSession, a: ApproveArgs) -> Touches:
    return Touches(records=(("quote", a.quote_id),),
                   accounts=((a.account_id,) if a.account_id else ()))


def plain(v: Any) -> str:
    """A percentage with no trailing zeros, for a sentence a person reads: 20%, not
    20.00%, and 7.5% rather than 7.50%."""
    d = _dec(v)
    return "0" if d is None else format(d.normalize(), "f")


async def _describe(session: AsyncSession, a: ApproveArgs, facts: dict[str, Any]) -> str:
    return f"Approve {plain(facts.get('discount_pct'))}% off {a.quote}"


async def _run(session: AsyncSession, ctx: AgentContext, a: ApproveArgs) -> dict[str, Any]:
    """What approving does: the quote may go out at this discount.

    A countered discount is written onto the lines that were discounted and the revision is
    marked for repricing, because the rep's ask is what pricing works from: the approver
    says what they will allow, and Forge prices it again rather than quietly editing the
    numbers a person already saw.
    """
    q = await session.get(Quote, a.quote_id)
    rev = await session.get(QuoteRevision, a.revision_id, with_for_update=True)
    if q is None or rev is None or rev.quote_id != q.id:
        raise AppError("The quote is no longer there")
    if rev.status != "draft":
        raise AppError("The revision was sent; its discount can't change any more")
    got = await look(session, q, rev)
    if got["fingerprint"] != a.fingerprint:
        raise AppError("The quote changed after this card was made, so the discount it "
                       "asks about is not the discount it has now")
    allowed = _dec(a.discount_pct_allowed)
    repriced: list[int] = []
    if allowed is not None:
        from app.cpq import quotes

        for ln in await quotes.lines_of(session, rev):
            if not any(r["position"] == ln.position
                       for r in got["facts"]["discounted_lines"]):
                continue
            ln.ask = {**(ln.ask or {}), "discount_pct": format(allowed, "f")}
            ln.ask.pop("unit_price", None)
            ln.updated_at = datetime.now(UTC)
            repriced.append(ln.position)
        rev.reprice_reason = (f"{plain(a.discount_pct_allowed)}% was approved instead "
                              f"of {plain(got['facts']['discount_pct'])}%")[:500]
        rev.reprice_at = datetime.now(UTC)
    await session.flush()
    await audit.append(session, q.tenant_id, "quote.discount.approved",
                       actor=ctx.approved_by or ctx.actor, agent_key=ctx.agent.key,
                       run_id=ctx.run_id, record_type="quote", record_id=q.id,
                       account_id=q.account_id, evidence_class="customer_money",
                       data={"number": q.number, "revision": rev.number,
                             "why": a.why, "competitor_claim": a.competitor,
                             # Kept in the chain's plain skeleton (Z10 A).
                             "discount_approval": {
                                 "revision": rev.number,
                                 "fingerprint": a.fingerprint,
                                 "discount_pct": got["facts"]["discount_pct"],
                                 "allowed_pct": a.discount_pct_allowed,
                                 "given_away": got["facts"]["given_away"],
                                 "margin_pct": got["facts"]["margin_pct"],
                                 "floor_margin_pct": got["facts"]["floor_margin_pct"],
                                 "matrix": got["matrix_id"], "lines": repriced}})
    return {"quote_id": str(a.quote_id), "revision": rev.number,
            "discount_pct": a.discount_pct_allowed or got["facts"]["discount_pct"],
            "reprice": repriced}


register_tool(ToolDef(
    key=DISCOUNT_TOOL,
    title="Approve a quote's discount",
    description="A quote asks for a discount the company's delegation of authority sends "
                "to a person. The approver sends it as it is, counters with the discount "
                "they will allow, or refuses it.",
    args=ApproveArgs, run=_run, compute_facts=_facts, touches=_touches, describe=_describe,
    facts=("discount_pct", "amount", "margin_pct", "floor_margin_pct"),
    permission="quotes.work", decider_permission="discounts.approve", four_eyes=True,
    counter_fields=("discount_pct_allowed",), ask_when=_ask_when, chain_for=_chain,
    evidence_class="customer_money",
))


# Asking -----------------------------------------------------------------------------------

def recommendation(got: dict[str, Any]) -> str:
    f, ch = got["facts"], got["chain"]
    if any(p.startswith(doa.NO_MATRIX) for p in ch.problems):
        return ("No delegation of authority has been set, so Forge cannot say who should "
                "sign this discount off. Write the rulebook on Quotes → Approval rules, "
                "then this card is asked of the right people. Approving it now approves "
                "the discount, not the rulebook.")
    floor = f.get("floor_margin_pct")
    margin = f.get("margin_pct")
    gap = (f" It leaves {margin}% against a {floor}% floor." if margin and floor else "")
    return (f"{plain(f['discount_pct'])}% off gives away {f['given_away']} on a quote worth "
            f"{f['amount']}.{gap} Approve to let it go out as it is, counter with the "
            "discount you will allow and Forge prices it again, or refuse and the rep "
            "reprices. The rep's reason and any competitor claim are their own words, not "
            "something Forge checked.")


async def ask_approval(session: AsyncSession, q: Quote, rev: QuoteRevision, actor: str, *,
                       why: str | None = None, competitor: str | None = None,
                       ) -> tuple[Decision | None, dict[str, Any]]:
    """One card for this quote's discount, or nothing if it needs none (Z5 A).

    A card already waiting on exactly these numbers is left alone; a card raised against
    numbers the quote no longer has is superseded, so an approver is never deciding about a
    quote that has moved on.
    """
    got = await look(session, q, rev)
    if not got["chain"].needed and not got["chain"].problems:
        if got["card"] is not None and got["card"].status == "pending":
            await runtime.supersede(session, f"discount:{rev.id}", actor,
                                    "The quote no longer needs a discount approval")
        return None, got
    card = got["card"]
    if card is not None and card.args.get("fingerprint") == got["fingerprint"] \
            and card.status in ("pending", "approved", "rejected"):
        return card, got
    await runtime.supersede(session, f"discount:{rev.id}", actor,
                            "The quote's discount changed; a new card asks again")
    ctx = await runtime.start_run(session, q.tenant_id, AGENT_KEY, "discount approval",
                                  trigger_ref=str(q.id))
    args = ApproveArgs(quote_id=q.id, revision_id=rev.id, account_id=q.account_id,
                       fingerprint=got["fingerprint"],
                       quote=f"{q.number} R{rev.number}", why=why, competitor=competitor)
    out = await runtime.call_tool(session, ctx, DISCOUNT_TOOL, args,
                                  recommendation=recommendation(got),
                                  requested_by=actor,
                                  dedupe_key=f"discount:{rev.id}")
    await runtime.finish_run(session, ctx, summary={"decision": str(out.decision_id)
                                                    if out.decision_id else None})
    await session.flush()
    return (await session.get(Decision, out.decision_id) if out.decision_id else None), got
