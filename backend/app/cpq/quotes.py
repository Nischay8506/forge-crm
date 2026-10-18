"""Quotes as records (decisions C12 A, C14 A): numbering per company and financial
year, starting a quote from an enquiry, and the lines of a draft revision.

- start(): a quote for an enquiry, numbered Q/2026-27/0001, with revision R1 as a draft
  whose lines are the enquiry's lines (with their configured products), valid for 15
  days. An enquiry has one quote; changes to it are revisions (F3.7).
- A draft revision's lines can be changed; a sent revision never changes.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals.calendar import calendar
from app.audit import chain
from app.core.errors import AppError, Conflict
from app.events.outbox import publish
from app.models import Configuration, Enquiry, EnquiryLine, Quote, QuoteLine, QuoteRevision

VALIDITY_DAYS = 15  # C12 A: editable per quote now, per family later
LINE_FIELDS = ("description", "qty", "unit", "item_code")
MAX_LINES = 200


def fy_of(d: date) -> str:
    """The Indian financial year a date falls in: 14 Oct 2026 -> "2026-27"."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


async def today(session: AsyncSession, tenant_id: uuid.UUID) -> date:
    cal, _ = await calendar(session, tenant_id)
    return datetime.now(cal.tz).date()


async def next_number(session: AsyncSession, tenant_id: uuid.UUID, day: date
                      ) -> tuple[str, int, str]:
    """(fy, seq, number): Q/2026-27/0001, a number per financial year that only goes up."""
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                          {"k": f"quote_seq:{tenant_id}"})
    fy = fy_of(day)
    seq = ((await session.scalar(select(func.max(Quote.seq)).where(Quote.fy == fy))) or 0) + 1
    return fy, seq, f"Q/{fy}/{seq:04d}"


def _describe(ln: EnquiryLine, cfg: Configuration | None) -> str:
    if cfg is not None:
        return f"{ln.description} ({', '.join(x['text'] for x in cfg.labels)})"[:500]
    extra = [x for x in (ln.dimensions, ln.material, ln.standard) if x
             and x.lower() not in ln.description.lower()]
    return " · ".join([ln.description, *extra])[:500]


async def start(session: AsyncSession, enq: Enquiry, actor: str) -> Quote:
    from app.enquiries import records

    have = await session.scalar(select(Quote).where(Quote.enquiry_id == enq.id))
    if have is not None:
        raise Conflict(f"This enquiry already has quote {have.number}; change it with a "
                       "revision")
    day = await today(session, enq.tenant_id)
    fy, seq, number = await next_number(session, enq.tenant_id, day)
    q = Quote(tenant_id=enq.tenant_id, seq=seq, fy=fy, number=number, enquiry_id=enq.id,
              account_id=enq.account_id, owner_id=enq.owner_id, status="draft",
              currency="INR", active_revision=1, created_by=actor)
    session.add(q)
    await session.flush()
    rev = QuoteRevision(tenant_id=enq.tenant_id, quote_id=q.id, number=1, status="draft",
                        valid_until=day + timedelta(days=VALIDITY_DAYS),
                        destination=_destination(enq), created_by=actor)
    session.add(rev)
    await session.flush()
    lines = list(await session.scalars(select(EnquiryLine).where(
        EnquiryLine.enquiry_id == enq.id, EnquiryLine.removed_at.is_(None))
        .order_by(EnquiryLine.position)))
    cfg_ids = [ln.configuration_id for ln in lines if ln.configuration_id]
    cfgs = {c.id: c for c in await session.scalars(
        select(Configuration).where(Configuration.id.in_(cfg_ids)))} if cfg_ids else {}
    for i, ln in enumerate(lines[:MAX_LINES], start=1):
        cfg = cfgs.get(ln.configuration_id)
        session.add(QuoteLine(
            tenant_id=enq.tenant_id, revision_id=rev.id, position=i, enquiry_line_id=ln.id,
            family_id=cfg.family_id if cfg else None, configuration_id=cfg.id if cfg else None,
            item_code=None, description=_describe(ln, cfg),
            variant_code=cfg.variant_code if cfg else None, qty=ln.qty, unit=ln.unit))
    await session.flush()
    await records.event(session, enq, "quote_started", actor, number=number)
    await chain.append(session, enq.tenant_id, "quote.created", actor=actor,
                       record_type="quote", record_id=q.id, account_id=enq.account_id,
                       evidence_class="customer_money",
                       data={"number": number, "enquiry_id": enq.id, "ref": enq.ref,
                             "lines": len(lines)})
    await publish(session, enq.tenant_id, "quote.created",
                  {"quote_id": str(q.id), "number": number, "enquiry_id": str(enq.id)})
    return q


def _destination(enq: Enquiry) -> dict[str, Any] | None:
    """Where the goods go, as far as the enquiry says (for freight, F3.2)."""
    place = (enq.details or {}).get("delivery_location") or None
    state = (enq.customer or {}).get("state") or None
    city = (enq.customer or {}).get("city") or None
    if not (place or state or city):
        return None
    return {"place": place, "city": city, "state": state}


async def revision(session: AsyncSession, q: Quote, number: int | None = None,
                   lock: bool = False) -> QuoteRevision:
    stmt = select(QuoteRevision).where(QuoteRevision.quote_id == q.id,
                                       QuoteRevision.number == (number or q.active_revision))
    if lock:
        stmt = stmt.with_for_update()
    rev = await session.scalar(stmt)
    if rev is None:
        raise AppError("That revision does not exist")
    return rev


def must_be_draft(q: Quote, rev: QuoteRevision) -> None:
    if rev.status != "draft":
        raise AppError(f"{q.number} R{rev.number} was sent and can't be changed; make a new "
                       "revision")


async def lines_of(session: AsyncSession, rev: QuoteRevision) -> list[QuoteLine]:
    return list(await session.scalars(select(QuoteLine).where(
        QuoteLine.revision_id == rev.id).order_by(QuoteLine.position)))


async def add_line(session: AsyncSession, q: Quote, rev: QuoteRevision,
                   data: dict[str, Any]) -> QuoteLine:
    must_be_draft(q, rev)
    have = await lines_of(session, rev)
    if len(have) >= MAX_LINES:
        raise AppError(f"A quote can have up to {MAX_LINES} lines")
    ln = QuoteLine(tenant_id=q.tenant_id, revision_id=rev.id, position=len(have) + 1,
                   **{k: data.get(k) for k in LINE_FIELDS})
    session.add(ln)
    rev.costed_at = None
    await session.flush()
    return ln


async def set_ask(session: AsyncSession, q: Quote, rev: QuoteRevision, ln: QuoteLine,
                  ask: dict[str, Any] | None) -> None:
    """What the rep asks for on a line (C2 A): a discount off the price Forge worked
    out, or a price of their own. The floor check and the approval card follow from
    pricing the revision again."""
    must_be_draft(q, rev)
    ln.ask = ask or None
    ln.updated_at = datetime.now(UTC)
    await session.flush()


async def change_line(session: AsyncSession, q: Quote, rev: QuoteRevision, ln: QuoteLine,
                      data: dict[str, Any]) -> list[str]:
    must_be_draft(q, rev)
    changed = [k for k in LINE_FIELDS if k in data and getattr(ln, k) != data[k]]
    for k in changed:
        setattr(ln, k, data[k])
    if changed:
        # Anything the cost depends on changed: the cost must be worked out again.
        ln.unit_cost = ln.cost_source = ln.cost_status = None
        ln.cost_flag = None
        ln.updated_at = datetime.now(UTC)
        rev.costed_at = None
    await session.flush()
    return changed


async def follow_configuration(session: AsyncSession, line: EnquiryLine, cfg: Configuration,
                               actor: str) -> tuple[Quote, QuoteRevision] | None:
    """An enquiry line configured after its quote started: the draft quote line copied
    from it takes the new configuration, so its cost must be worked out again. A sent
    revision never changes. Returns the quote and draft that changed, if any."""
    q = await session.scalar(select(Quote).where(Quote.enquiry_id == line.enquiry_id)
                             .with_for_update())
    if q is None:
        return None
    rev = await revision(session, q, lock=True)
    if rev.status != "draft":
        return None
    ln = await session.scalar(select(QuoteLine).where(
        QuoteLine.revision_id == rev.id, QuoteLine.enquiry_line_id == line.id)
        .with_for_update())
    if ln is None or ln.configuration_id == cfg.id:
        return None
    before = ln.variant_code
    ln.family_id, ln.configuration_id = cfg.family_id, cfg.id
    ln.variant_code, ln.description = cfg.variant_code, _describe(line, cfg)
    ln.unit_cost = ln.cost_source = ln.cost_status = None
    ln.cost_flag = None
    ln.updated_at = datetime.now(UTC)
    rev.costed_at = None
    await session.flush()
    await chain.append(session, q.tenant_id, "quote.line.configured", actor=actor,
                       record_type="quote", record_id=q.id, account_id=q.account_id,
                       evidence_class="customer_money",
                       data={"number": q.number, "revision": rev.number,
                             "line": ln.position, "from": before, "to": cfg.variant_code})
    return q, rev


async def remove_line(session: AsyncSession, q: Quote, rev: QuoteRevision,
                      ln: QuoteLine) -> None:
    must_be_draft(q, rev)
    await session.delete(ln)
    await session.flush()
    for i, x in enumerate(await lines_of(session, rev), start=1):
        x.position = i
    rev.costed_at = None
    await session.flush()


def money(v: Decimal | None) -> str | None:
    return None if v is None else format(v.quantize(Decimal("0.01")), "f")


def line_out(ln: QuoteLine, costs: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(ln.id), "position": ln.position, "description": ln.description,
        "item_code": ln.item_code, "variant_code": ln.variant_code,
        "family_id": str(ln.family_id) if ln.family_id else None,
        "configuration_id": str(ln.configuration_id) if ln.configuration_id else None,
        "enquiry_line_id": str(ln.enquiry_line_id) if ln.enquiry_line_id else None,
        "qty": None if ln.qty is None else format(ln.qty.normalize(), "f"), "unit": ln.unit,
        "cost_source": ln.cost_source, "cost_flag": ln.cost_flag,
        "cost_status": ln.cost_status,
        # F3.3: the price everyone in sales sees, with the margin against the floor.
        "price_source": ln.price_source, "price_status": ln.price_status,
        "list_price": money(ln.list_price), "unit_price": money(ln.unit_price),
        "amount": money(ln.unit_price * ln.qty) if (
            ln.unit_price is not None and ln.qty is not None) else None,
        "margin_pct": None if ln.margin_pct is None else format(
            ln.margin_pct.normalize(), "f"),
        "ask": ln.ask,
    }
    if costs:  # C17 B: everyone in sales sees costs, as a permission that can change
        out["unit_cost"] = money(ln.unit_cost)
        out["cost"] = ln.cost
        out["price"] = ln.price  # the whole waterfall, with the cost it was checked on
    return out
