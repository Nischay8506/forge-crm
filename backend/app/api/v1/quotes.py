"""Quotes (decisions C12 A, C14 A): started from an enquiry, numbered per financial year,
with revisions R1, R2 ... A draft revision's lines can be changed; each line's cost and
its breakdown (F3.2) are shown to people with the costs.view permission (C17 B)."""

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
from app.cpq import quotes
from app.db import get_db
from app.models import Account, Enquiry, Quote, QuoteLine, QuoteRevision, User
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
            "enquiry": {"id": str(enq.id), "ref": enq.ref, "summary": enq.summary} if enq else None,
            "customer": customer, "account_id": str(q.account_id) if q.account_id else None,
            "owner_id": str(q.owner_id) if q.owner_id else None, "owner_name": owner,
            "created_at": q.created_at, "updated_at": q.updated_at}


@router.get("")
async def list_quotes(status: str | None = Query(None, max_length=20),
                      mine: bool = False, current: CurrentUser = Depends(READ),
                      db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    stmt = select(Quote).order_by(Quote.created_at.desc()).limit(200)
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
    return await _detail(db, q, current)


async def _detail(db: AsyncSession, q: Quote, current: CurrentUser) -> dict[str, Any]:
    rev = await quotes.revision(db, q)
    revs = list(await db.scalars(select(QuoteRevision).where(
        QuoteRevision.quote_id == q.id).order_by(QuoteRevision.number)))
    costs = _costs(current)
    return {**await _row(db, q),
            "active": {"number": rev.number, "status": rev.status, "reason": rev.reason,
                       "reason_note": rev.reason_note, "valid_until": rev.valid_until,
                       "delivery_terms": rev.delivery_terms, "destination": rev.destination,
                       "costed_at": rev.costed_at, "sent_at": rev.sent_at,
                       "lines": [quotes.line_out(ln, costs)
                                 for ln in await quotes.lines_of(db, rev)]},
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
    await quotes.change_line(db, q, rev, ln, data)
    return await _detail(db, q, current)


@router.delete("/{quote_id}/lines/{line_id}")
async def remove_line(quote_id: uuid.UUID, line_id: uuid.UUID,
                      current: CurrentUser = Depends(WORK),
                      db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    q = await _quote(db, quote_id, lock=True)
    rev = await quotes.revision(db, q, lock=True)
    await quotes.remove_line(db, q, rev, await _line(db, rev, line_id))
    return await _detail(db, q, current)
