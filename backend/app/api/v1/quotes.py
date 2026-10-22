"""Quotes (decisions C12 A, C14 A): started from an enquiry, numbered per financial year,
with revisions R1, R2 ... A draft revision's lines can be changed; each line's cost and
its breakdown (F3.2) are shown to people with the costs.view permission (C17 B). A
draft's costs are worked out when it starts and after every change to its lines
(app/cpq/costing.py), and its prices follow at once (F3.3, app/cpq/pricing.py): where
each line's price came from, the discounts on it, and the margin against the family's
floor. A sent one shows "cost changed: reprice recommended" (K9 A)."""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_permission
from app.core.errors import AppError, Forbidden, NotFound
from app.core.permissions import has_permission
from app.cpq import costing, pricing, quotes
from app.db import get_db
from app.models import Account, Decision, Enquiry, Quote, QuoteLine, QuoteRevision, User
from app.models.quotes import OPEN_QUOTE_STATUSES

router = APIRouter(prefix="/quotes", tags=["quotes"])

READ = require_permission("quotes.read")
WORK = require_permission("quotes.work")

STATUS_LABELS = {"draft": "Draft", "pending_approval": "Waiting for approval", "sent": "Sent",
                 "accepted": "Accepted", "lost": "Lost", "expired": "Expired"}


def _actor(u: CurrentUser) -> str:
    return f"user:{u.user.id}"


def _costs(u: CurrentUser) -> bool:
    return has_permission(u.permissions, "costs.view")


async def _quote(db: AsyncSession, quote_id: uuid.UUID, lock: bool = False) -> Quote:
    q = await db.get(Quote, quote_id, with_for_update=lock)
    if q is None:
        raise NotFound("Quote not found")
    return q


class StartIn(BaseModel):
    enquiry_id: uuid.UUID


class RevisionIn(BaseModel):
    valid_until: date | None = None
    delivery_terms: str | None = Field(default=None, max_length=20)
    destination: dict[str, Any] | None = None
    round_off: bool | None = None  # Y5: round prices up to the policy's step


class LineIn(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    qty: Decimal | None = Field(default=None, ge=0, le=Decimal("1e12"))
    unit: str | None = Field(default=None, max_length=20)
    item_code: str | None = Field(default=None, max_length=140)


class LinePatch(BaseModel):
    description: str | None = Field(default=None, min_length=1, max_length=500)
    qty: Decimal | None = Field(default=None, ge=0, le=Decimal("1e12"))
    unit: str | None = Field(default=None, max_length=20)
    item_code: str | None = Field(default=None, max_length=140)


class AskIn(BaseModel):
    """What the rep asks for on a line (C2 A): one of the two, or neither to clear it."""

    discount_pct: Decimal | None = Field(default=None, ge=0, lt=100)
    unit_price: Decimal | None = Field(default=None, ge=0, le=Decimal("1e12"))


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    for k in ("unit", "item_code"):
        if k in out:
            out[k] = (out[k] or "").strip() or None
    if out.get("description") is not None:
        out["description"] = out["description"].strip()
    return out


async def _row(db: AsyncSession, q: Quote, names: dict[uuid.UUID, str] | None = None
               ) -> dict[str, Any]:
    enq = await db.get(Enquiry, q.enquiry_id)
    acc = await db.get(Account, q.account_id) if q.account_id else None
    rev = await quotes.revision(db, q)
    owner = (names or {}).get(q.owner_id) if q.owner_id else None
    if q.owner_id and owner is None:
        u = await db.get(User, q.owner_id)
        owner = u.full_name if u else None
    customer = acc.name if acc else ((enq.customer or {}).get("company") if enq else None)
    return {"id": str(q.id), "number": q.number, "status": q.status,
            "status_label": STATUS_LABELS.get(q.status, q.status), "revision": rev.number,
            "valid_until": rev.valid_until, "currency": q.currency,
            "reprice_reason": rev.reprice_reason, "reprice_at": rev.reprice_at,
            "enquiry": {"id": str(enq.id), "ref": enq.ref, "summary": enq.summary} if enq else None,
            "customer": customer, "account_id": str(q.account_id) if q.account_id else None,
            "owner_id": str(q.owner_id) if q.owner_id else None, "owner_name": owner,
            "created_at": q.created_at, "updated_at": q.updated_at}


@router.get("")
async def list_quotes(status: str | None = Query(None, max_length=20),
                      mine: bool = False, reprice: bool = False,
                      current: CurrentUser = Depends(READ),
                      db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    stmt = select(Quote).order_by(Quote.created_at.desc()).limit(200)
    if reprice:  # sent quotes whose costs changed since (K9 A)
        stmt = stmt.where(Quote.id.in_(select(QuoteRevision.quote_id).where(
            QuoteRevision.number == Quote.active_revision,
            QuoteRevision.reprice_reason.is_not(None))))
    if status == "open":
        stmt = stmt.where(Quote.status.in_(OPEN_QUOTE_STATUSES))
    elif status:
        stmt = stmt.where(Quote.status == status)
    if mine:
        stmt = stmt.where(Quote.owner_id == current.user.id)
    names = dict((await db.execute(select(User.id, User.full_name))).all())
    return {"quotes": [await _row(db, q, names) for q in await db.scalars(stmt)]}


@router.post("", status_code=201)
async def start_quote(body: StartIn, current: CurrentUser = Depends(WORK),
                      db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    enq = await db.get(Enquiry, body.enquiry_id, with_for_update=True)
    if enq is None or enq.erased_at is not None or enq.merged_into_id is not None:
        raise NotFound("Enquiry not found")
    if enq.status == "closed":
        raise AppError("The enquiry is closed; reopen it to quote")
    q = await quotes.start(db, enq, _actor(current))
    await costing.cost_revision(db, q, await quotes.revision(db, q), _actor(current),
                                why="quote started")
    return await _detail(db, q, current)


async def _detail(db: AsyncSession, q: Quote, current: CurrentUser) -> dict[str, Any]:
    rev = await quotes.revision(db, q)
    revs = list(await db.scalars(select(QuoteRevision).where(
        QuoteRevision.quote_id == q.id).order_by(QuoteRevision.number)))
    costs = _costs(current)
    lines = await quotes.lines_of(db, rev)
    card_ids = [x for ln in lines for x in (ln.cost_review_id, ln.price_review_id) if x]
    cards = {d.id: d for d in await db.scalars(select(Decision).where(
        Decision.id.in_(card_ids)))} if card_ids else {}

    def _card(decision_id: uuid.UUID | None) -> dict[str, Any] | None:
        d = cards.get(decision_id) if decision_id else None
        return {"id": str(d.id), "status": d.status, "decided_by": d.decided_by,
                "note": d.note} if d else None

    def line(ln: QuoteLine) -> dict[str, Any]:
        out = quotes.line_out(ln, costs)
        if costs:
            out["cost_review"] = _card(ln.cost_review_id)
        out["price_review"] = _card(ln.price_review_id)
        return out

    return {**await _row(db, q),
            "active": {"number": rev.number, "status": rev.status, "reason": rev.reason,
                       "reason_note": rev.reason_note, "valid_until": rev.valid_until,
                       "delivery_terms": rev.delivery_terms, "destination": rev.destination,
                       "costed_at": rev.costed_at, "sent_at": rev.sent_at,
                       "priced_at": rev.priced_at, "price_status": rev.price_status,
                       "round_off": rev.round_off,
                       "totals": pricing.totals(lines),
                       "reprice_reason": rev.reprice_reason, "reprice_at": rev.reprice_at,
                       "lines": [line(ln) for ln in lines]},
            "revisions": [{"number": r.number, "status": r.status, "reason": r.reason,
                           "created_at": r.created_at, "sent_at": r.sent_at} for r in revs],
            "may": {"work": has_permission(current.permissions, "quotes.work")
                    and rev.status == "draft", "see_costs": costs}}


@router.get("/{quote_id}")
async def get_quote(quote_id: uuid.UUID, current: CurrentUser = Depends(READ),
                    db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await _detail(db, await _quote(db, quote_id), current)


@router.patch("/{quote_id}")
async def edit_revision(quote_id: uuid.UUID, body: RevisionIn,
                        current: CurrentUser = Depends(WORK),
                        db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    q = await _quote(db, quote_id, lock=True)
    rev = await quotes.revision(db, q, lock=True)
    quotes.must_be_draft(q, rev)
    data = body.model_dump(exclude_unset=True)
    if "valid_until" in data:
        day = await quotes.today(db, q.tenant_id)
        v = data["valid_until"]
        if v is None or v < day or v > day + timedelta(days=366):
            raise AppError("The quote must be valid from today up to a year ahead")
        rev.valid_until = v
    if "delivery_terms" in data:
        rev.delivery_terms = (data["delivery_terms"] or "").strip() or None
        rev.costed_at = None
    if "destination" in data:
        d = data["destination"] or {}
        rev.destination = {k: str(d[k]).strip()[:100] for k in ("place", "city", "state")
                           if d.get(k)} or None
        rev.costed_at = None
    await db.flush()
    if data.get("round_off") is not None and data["round_off"] != rev.round_off:
        rev.round_off = data["round_off"]
        await pricing.price_revision(
            db, q, rev, _actor(current),
            why="rounding switched on" if rev.round_off else "rounding switched off")
    return await _detail(db, q, current)


async def _line(db: AsyncSession, rev: QuoteRevision, line_id: uuid.UUID) -> QuoteLine:
    ln = await db.get(QuoteLine, line_id)
    if ln is None or ln.revision_id != rev.id:
        raise NotFound("Quote line not found")
    return ln


@router.post("/{quote_id}/lines", status_code=201)
async def add_line(quote_id: uuid.UUID, body: LineIn, current: CurrentUser = Depends(WORK),
                   db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    q = await _quote(db, quote_id, lock=True)
    rev = await quotes.revision(db, q, lock=True)
    await quotes.add_line(db, q, rev, _clean(body.model_dump()))
    await costing.cost_revision(db, q, rev, _actor(current), why="line added")
    return await _detail(db, q, current)


@router.patch("/{quote_id}/lines/{line_id}")
async def change_line(quote_id: uuid.UUID, line_id: uuid.UUID, body: LinePatch,
                      current: CurrentUser = Depends(WORK),
                      db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    q = await _quote(db, quote_id, lock=True)
    rev = await quotes.revision(db, q, lock=True)
    ln = await _line(db, rev, line_id)
    data = _clean(body.model_dump(exclude_unset=True))
    if data.get("description") is None:
        data.pop("description", None)
    if ln.configuration_id and "description" in data:
        raise Forbidden("A configured line's description comes from its configuration")
    if await quotes.change_line(db, q, rev, ln, data):
        await costing.cost_revision(db, q, rev, _actor(current), why="line changed")
    return await _detail(db, q, current)


@router.delete("/{quote_id}/lines/{line_id}")
async def remove_line(quote_id: uuid.UUID, line_id: uuid.UUID,
                      current: CurrentUser = Depends(WORK),
                      db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    q = await _quote(db, quote_id, lock=True)
    rev = await quotes.revision(db, q, lock=True)
    ln = await _line(db, rev, line_id)
    if ln.cost_review_id is not None or ln.price_review_id is not None:
        from app.agents import runtime

        for key in ("cost", "price"):
            await runtime.supersede(db, f"{key}:{ln.id}", _actor(current),
                                    "The line was removed")
    await quotes.remove_line(db, q, rev, ln)
    await costing.cost_revision(db, q, rev, _actor(current), why="line removed")
    return await _detail(db, q, current)


@router.post("/{quote_id}/cost")
async def work_out_costs(quote_id: uuid.UUID, current: CurrentUser = Depends(WORK),
                         db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Work out the draft's costs again with today's rates, models and BOMs (F3.2)."""
    q = await _quote(db, quote_id, lock=True)
    rev = await quotes.revision(db, q, lock=True)
    await costing.cost_revision(db, q, rev, _actor(current), why="asked for")
    return await _detail(db, q, current)


@router.put("/{quote_id}/lines/{line_id}/ask")
async def set_ask(quote_id: uuid.UUID, line_id: uuid.UUID, body: AskIn,
                  current: CurrentUser = Depends(WORK),
                  db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """The rep's own ask on a line: a discount off Forge's price, or a price of their
    own. It is priced again at once, so a line under the floor goes for approval (C2 A)."""
    q = await _quote(db, quote_id, lock=True)
    rev = await quotes.revision(db, q, lock=True)
    ln = await _line(db, rev, line_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("discount_pct") is not None and data.get("unit_price") is not None:
        raise AppError("Ask for a discount or a price, not both")
    ask = {k: format(v, "f") for k, v in data.items() if v is not None}
    await quotes.set_ask(db, q, rev, ln, ask)
    await pricing.price_revision(db, q, rev, _actor(current), why="the rep's ask")
    return await _detail(db, q, current)


@router.post("/{quote_id}/price")
async def work_out_prices(quote_id: uuid.UUID, current: CurrentUser = Depends(WORK),
                          db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Price the draft again with today's policy, ERP prices and costs (F3.3)."""
    q = await _quote(db, quote_id, lock=True)
    rev = await quotes.revision(db, q, lock=True)
    await pricing.price_revision(db, q, rev, _actor(current), why="asked for")
    return await _detail(db, q, current)


@router.get("/{quote_id}/price-seal")
async def check_price_seal(quote_id: uuid.UUID, revision: int | None = None,
                           current: CurrentUser = Depends(READ),
                           db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Is each line's kept price still the one Forge worked out? Checked against the
    fingerprints in the audit chain, as the cost seals are."""
    if not _costs(current):
        raise Forbidden("You need the costs.view permission to check a quote's prices")
    q = await _quote(db, quote_id)
    return await pricing.seals(db, q, await quotes.revision(db, q, revision))


@router.get("/{quote_id}/seal")
async def check_seal(quote_id: uuid.UUID, revision: int | None = None,
                     current: CurrentUser = Depends(READ),
                     db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Is each line's kept cost still the one Forge worked out? Checked against the
    fingerprints in the audit chain, which its hashes and hourly signatures protect."""
    if not _costs(current):
        raise Forbidden("You need the costs.view permission to check a quote's costs")
    q = await _quote(db, quote_id)
    return await costing.seals(db, q, await quotes.revision(db, q, revision))
