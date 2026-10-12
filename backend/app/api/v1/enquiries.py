"""Enquiries (Phase 2, M1): the queue, one enquiry, Other mail, the Test mailbox and
the setup (mailbox, routing, timers, product families, people).

The work itself is in app/enquiries/; these endpoints check who may do what. The
mailbox password and sign-in tokens go in and never come out.
"""

import base64
import binascii
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import load_definitions
from app.api.v1.audit import _message, _names
from app.approvals.calendar import calendar
from app.core.deps import CurrentUser, require_permission
from app.core.errors import AppError, NotFound
from app.core.permissions import has_permission
from app.db import get_db
from app.enquiries import ack, dedupe, families, intake, mime, records, routing, timers
from app.enquiries import settings as settings_mod
from app.enquiries.agent import MessageArgs, acknowledge
from app.enquiries.mail import oauth
from app.enquiries.mail import service as mail
from app.enquiries.mail.base import MailError
from app.files import store
from app.models import (
    Account,
    Contact,
    Enquiry,
    EnquiryAssignment,
    EnquiryEvent,
    EnquiryLine,
    InboundEmail,
    Message,
    ProductFamily,
    StoredFile,
    User,
)
from app.models.enquiries import OPEN_STATUSES
from app.sla import engine

router = APIRouter(prefix="/enquiries", tags=["enquiries"])

READ = require_permission("enquiries.read")
WORK = require_permission("enquiries.work")
ASSIGN = require_permission("enquiries.assign")
MANAGE = require_permission("enquiries.manage")

LABEL_NAMES = {"enquiry": "Enquiry", "complaint": "Complaint", "payment": "Payment",
               "order_status": "Order status", "vendor_mail": "Vendor mail", "spam": "Spam"}
STATUS_NAMES = {"new": "New", "open": "Open", "waiting_customer": "Waiting on customer",
                "parked": "Parked", "closed": "Closed", "merged": "Merged"}
VIEWS = ("open", "mine", "unassigned", "review", "duplicates", "waiting", "parked", "closed",
         "all")


def _actor(u: CurrentUser) -> str:
    return f"user:{u.user.id}"


async def _enq(db: AsyncSession, enquiry_id: uuid.UUID, lock: bool = False) -> Enquiry:
    e = await db.get(Enquiry, enquiry_id, with_for_update=lock)
    if e is None or e.erased_at is not None:
        raise NotFound("Enquiry not found")
    return e


async def _user_names(db: AsyncSession) -> dict[uuid.UUID, str]:
    return dict((await db.execute(select(User.id, User.full_name))).all())


def _who(e: Enquiry, contact: str | None, account: str | None) -> str:
    c = e.customer or {}
    return contact or c.get("contact_name") or c.get("email") or "Unknown sender"


def _stage_now(ts: list[Any], now: datetime, cal: Any) -> dict[str, Any] | None:
    """The timer a person should look at first: the running one closest to its end."""
    running = [t for t in ts if t.stopped_at is None]
    if not running:
        return None
    t = max(running, key=lambda t: engine.share(t, now, cal))
    out = engine.out(t, now, cal)
    out["label"] = timers.LABELS.get(t.stage, t.stage).capitalize()
    return out


async def _rows(db: AsyncSession, enquiries: list[Enquiry]) -> list[dict[str, Any]]:
    if not enquiries:
        return []
    names = await _user_names(db)
    ids = [e.id for e in enquiries]
    accounts = dict((await db.execute(select(Account.id, Account.name).where(
        Account.id.in_({e.account_id for e in enquiries if e.account_id})))).all())
    tiers = dict((await db.execute(select(Account.id, Account.tier).where(
        Account.id.in_({e.account_id for e in enquiries if e.account_id})))).all())
    contacts = dict((await db.execute(select(Contact.id, Contact.full_name).where(
        Contact.id.in_({e.contact_id for e in enquiries if e.contact_id})))).all())
    lines = dict((await db.execute(select(EnquiryLine.enquiry_id, func.count()).where(
        EnquiryLine.enquiry_id.in_(ids), EnquiryLine.removed_at.is_(None))
        .group_by(EnquiryLine.enquiry_id))).all())
    from app.models import SlaTimer

    by: dict[uuid.UUID, list[Any]] = {}
    for t in await db.scalars(select(SlaTimer).where(SlaTimer.subject_type == "enquiry",
                                                     SlaTimer.subject_id.in_(ids))):
        by.setdefault(t.subject_id, []).append(t)
    cal, _ = await calendar(db, enquiries[0].tenant_id)
    now = datetime.now(UTC)
    out = []
    for e in enquiries:
        account, contact = accounts.get(e.account_id), contacts.get(e.contact_id)
        out.append({
            "id": str(e.id), "ref": e.ref, "status": e.status,
            "status_label": STATUS_NAMES.get(e.status, e.status), "source": e.source,
            "summary": e.summary, "who": _who(e, contact, account),
            "company": account or (e.customer or {}).get("company"),
            "account_id": str(e.account_id) if e.account_id else None,
            "account_tier": tiers.get(e.account_id), "contact_id": str(e.contact_id)
            if e.contact_id else None, "owner_id": str(e.owner_id) if e.owner_id else None,
            "owner_name": names.get(e.owner_id), "assigned_rule": e.assigned_rule,
            "is_tender": e.is_tender, "needs_review": e.needs_review,
            "review_reasons": e.review_reasons, "language": e.language,
            "waiting_on": e.waiting_on, "lines": lines.get(e.id, 0),
            "possible_duplicate": dedupe.out_pair(e), "confidence": e.confidence,
            "timer": _stage_now(by.get(e.id, []), now, cal),
            "first_response_at": e.first_response_at, "quote_sent_at": e.quote_sent_at,
            "created_at": e.created_at, "last_activity_at": e.last_activity_at})
    return out


def _filter(stmt: Any, view: str, me: uuid.UUID) -> Any:
    open_ = Enquiry.status.in_(("new", "open", "waiting_customer"))
    return {
        "open": stmt.where(open_),
        "mine": stmt.where(open_, Enquiry.owner_id == me),
        "unassigned": stmt.where(Enquiry.status.in_(OPEN_STATUSES), Enquiry.owner_id.is_(None)),
        "review": stmt.where(Enquiry.status.in_(OPEN_STATUSES), Enquiry.needs_review.is_(True)),
        "duplicates": stmt.where(Enquiry.status.in_(OPEN_STATUSES),
                                 Enquiry.possible_duplicate_of_id.is_not(None)),
        "waiting": stmt.where(Enquiry.status == "waiting_customer"),
        "parked": stmt.where(Enquiry.status == "parked"),
        "closed": stmt.where(Enquiry.status.in_(("closed", "merged"))),
        "all": stmt,
    }[view]


@router.get("")
async def queue(view: str = Query("open"), q: str | None = None,
                owner_id: uuid.UUID | None = None, limit: int = Query(100, le=300),
                current: CurrentUser = Depends(READ),
                db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    if view not in VIEWS:
        raise AppError("Unknown view")
    stmt = _filter(select(Enquiry).where(Enquiry.erased_at.is_(None)), view, current.user.id)
    if owner_id:
        stmt = stmt.where(Enquiry.owner_id == owner_id)
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Enquiry.ref.ilike(like), Enquiry.summary.ilike(like),
                              Enquiry.customer["company"].astext.ilike(like),
                              Enquiry.customer["contact_name"].astext.ilike(like),
                              Enquiry.customer["email"].astext.ilike(like)))
    rows = list(await db.scalars(stmt.order_by(Enquiry.created_at.desc()).limit(limit)))
    return await _rows(db, rows)


@router.get("/counts")
async def counts(current: CurrentUser = Depends(READ),
                 db: AsyncSession = Depends(get_db)) -> dict[str, int]:
    out = {}
    for v in VIEWS[:-1]:
        stmt = _filter(select(func.count()).select_from(Enquiry)
                       .where(Enquiry.erased_at.is_(None)), v, current.user.id)
        out[v] = await db.scalar(stmt) or 0
    out["other_mail"] = await db.scalar(select(func.count()).select_from(InboundEmail).where(
        InboundEmail.outcome == "other", InboundEmail.relabelled_at.is_(None))) or 0
    out["failed"] = await db.scalar(select(func.count()).select_from(InboundEmail).where(
        InboundEmail.stage == "failed")) or 0
    return out


# One enquiry -----------------------------------------------------------------------

def _line(ln: EnquiryLine) -> dict[str, Any]:
    return {"id": str(ln.id), "position": ln.position, "item_ref": ln.item_ref,
            "description": ln.description, "material": ln.material,
            "dimensions": ln.dimensions, "qty": ln.qty, "unit": ln.unit,
            "standard": ln.standard, "notes": ln.notes, "confidence": ln.confidence,
            "unsure": ln.unsure, "source": ln.source, "source_ref": ln.source_ref,
            "original": ln.original, "corrected_by": ln.corrected_by,
            "corrected_at": ln.corrected_at}


def _email(row: InboundEmail, full: bool = True) -> dict[str, Any]:
    out = {"id": str(row.id), "provider": row.provider, "from_address": row.from_address,
           "from_name": row.from_name, "to": row.to_addresses, "cc": row.cc_addresses,
           "subject": row.subject, "received_at": row.received_at,
           "attachments": row.attachments, "auto_kind": row.auto_kind, "label": row.label,
           "label_name": LABEL_NAMES.get(row.label or "", row.label),
           "label_confidence": row.label_confidence, "label_source": row.label_source,
           "label_reason": row.label_reason, "original_label": row.original_label,
           "relabelled_by": row.relabelled_by, "relabelled_at": row.relabelled_at,
           "stage": row.stage, "outcome": row.outcome, "attempts": row.attempts,
           "last_error": row.last_error, "size_bytes": row.size_bytes,
           "enquiry_id": str(row.enquiry_id) if row.enquiry_id else None,
           "message_id": str(row.message_id) if row.message_id else None,
           "erased": row.erased_at is not None}
    out["body"] = row.body_text if full else (row.body_text or "")[:300]
    return out


async def _detail(db: AsyncSession, e: Enquiry, current: CurrentUser) -> dict[str, Any]:
    names = await _user_names(db)
    account = await db.get(Account, e.account_id) if e.account_id else None
    contact = await db.get(Contact, e.contact_id) if e.contact_id else None
    row = (await _rows(db, [e]))[0]
    merged_in = list(await db.scalars(select(Enquiry).where(Enquiry.merged_into_id == e.id)))
    ids = [e.id, *[m.id for m in merged_in]]
    lines = list(await db.scalars(select(EnquiryLine).where(
        EnquiryLine.enquiry_id.in_(ids), EnquiryLine.removed_at.is_(None))
        .order_by(EnquiryLine.position)))
    emails = list(await db.scalars(select(InboundEmail).where(
        InboundEmail.enquiry_id.in_(ids)).order_by(InboundEmail.received_at)))
    msgs = list(await db.scalars(select(Message).where(Message.enquiry_id.in_(ids))
                                 .order_by(Message.created_at)))
    events = list(await db.scalars(select(EnquiryEvent).where(EnquiryEvent.enquiry_id == e.id)
                                   .order_by(EnquiryEvent.id)))
    assigns = list(await db.scalars(select(EnquiryAssignment).where(
        EnquiryAssignment.enquiry_id == e.id).order_by(EnquiryAssignment.id)))
    cal, _ = await calendar(db, e.tenant_id)
    now = datetime.now(UTC)
    items = await ack.checklist(db, e)
    family = await db.get(ProductFamily, e.product_family_id) if e.product_family_id else None
    other = await db.get(Enquiry, e.possible_duplicate_of_id) \
        if e.possible_duplicate_of_id else None
    into = await db.get(Enquiry, e.merged_into_id) if e.merged_into_id else None
    p = current.permissions
    may_work = has_permission(p, "enquiries.work")
    return {
        **row, "customer": e.customer, "details": e.details, "questions": e.questions,
        "account": {"id": str(account.id), "name": account.name, "tier": account.tier,
                    "city": account.city, "state": account.state} if account else None,
        "contact": {"id": str(contact.id), "full_name": contact.full_name,
                    "email": contact.email, "phone": contact.phone,
                    "designation": contact.designation} if contact else None,
        "product_family": {"id": str(family.id), "name": family.name} if family else None,
        "checklist": [{"key": i["key"], "label": i.get("label"), "required": i.get("required")}
                      for i in items],
        "lines": [{**_line(ln), "from_ref": None if ln.enquiry_id == e.id else
                   next((m.ref for m in merged_in if m.id == ln.enquiry_id), None)}
                  for ln in lines],
        "merged_in": [{"id": str(m.id), "ref": m.ref, "merged_at": m.merged_at,
                       "merged_by": m.merged_by} for m in merged_in],
        "merged_into": {"id": str(into.id), "ref": into.ref} if into else None,
        "duplicate_of": {"id": str(other.id), "ref": other.ref, "summary": other.summary,
                         "status": other.status} if other else None,
        "emails": [_email(r) for r in emails],
        "messages": [_message(m, *(await _names(db, m))) for m in msgs],
        "events": [{"kind": ev.kind, "actor": ev.actor, "actor_name": _actor_name(ev.actor, names),
                    "detail": ev.detail, "at": ev.at} for ev in events],
        "assignments": [{"user_id": str(a.user_id) if a.user_id else None,
                         "user_name": names.get(a.user_id), "rule": a.rule,
                         "rule_label": settings_mod.STEP_LABELS.get(a.rule, a.rule),
                         "reason": a.reason, "overrode_rule": a.overrode_rule,
                         "actor": a.actor, "actor_name": _actor_name(a.actor, names),
                         "at": a.at} for a in assigns],
        "timers": [{**engine.out(t, now, cal),
                    "label": timers.LABELS.get(t.stage, t.stage).capitalize()}
                   for t in await engine.of(db, "enquiry", e.id)],
        "close_reason": e.close_reason, "closed_at": e.closed_at, "closed_by": e.closed_by,
        "owner_seen_at": e.owner_seen_at, "quote_sent_by": e.quote_sent_by,
        "first_response_by": e.first_response_by, "nudges": e.nudges,
        "waiting_since": e.waiting_since, "parked_at": e.parked_at,
        "may": {"work": may_work, "assign": has_permission(p, "enquiries.assign"),
                "send": may_work and has_permission(p, "messages.send")},
    }


def _actor_name(actor: str | None, names: dict[uuid.UUID, str]) -> str:
    if not actor:
        return ""
    if actor.startswith("user:"):
        try:
            return names.get(uuid.UUID(actor[5:]), "A person")
        except ValueError:
            return "A person"
    if actor == "agent:enquiry":
        return "Enquiry Agent"
    if actor.startswith("system"):
        return "Forge"
    return actor


@router.get("/{enquiry_id}")
async def get_enquiry(enquiry_id: uuid.UUID, current: CurrentUser = Depends(READ),
                      db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await _detail(db, await _enq(db, enquiry_id), current)


@router.post("/{enquiry_id}/seen")
async def seen(enquiry_id: uuid.UUID, current: CurrentUser = Depends(READ),
               db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    e = await _enq(db, enquiry_id, lock=True)
    return {"first_look": await intake.seen(db, e, current.user)}


class LineIn(BaseModel):
    item_ref: str | None = Field(default=None, max_length=100)
    description: str = Field(min_length=1, max_length=1000)
    material: str | None = Field(default=None, max_length=200)
    dimensions: str | None = Field(default=None, max_length=300)
    qty: float | None = Field(default=None, ge=0, le=1e9)
    unit: str | None = Field(default=None, max_length=20)
    standard: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=1000)


class CustomerIn(BaseModel):
    company: str | None = Field(default=None, max_length=200)
    contact_name: str | None = Field(default=None, max_length=200)
    designation: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=320)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)


class DetailsIn(BaseModel):
    target_date: str | None = Field(default=None, max_length=10)
    target_date_text: str | None = Field(default=None, max_length=200)
    delivery_location: str | None = Field(default=None, max_length=300)
    special_requirements: str | None = Field(default=None, max_length=1000)
    tender_ref: str | None = Field(default=None, max_length=100)


class ManualIn(BaseModel):
    account_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    customer: CustomerIn = Field(default_factory=CustomerIn)
    summary: str | None = Field(default=None, max_length=300)
    details: DetailsIn = Field(default_factory=DetailsIn)
    lines: list[LineIn] = Field(default_factory=list, max_length=200)
    is_tender: bool = False
    product_family_id: uuid.UUID | None = None
    language: str | None = Field(default=None, max_length=10)
    received_how: str | None = Field(default=None, max_length=100)


@router.post("", status_code=201)
async def manual(body: ManualIn, current: CurrentUser = Depends(WORK),
                 db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """An enquiry a person took by phone, at a visit or anywhere else ("+ Manual enquiry")."""
    data = body.model_dump(mode="json")
    problems = records.manual_problems(data)
    if problems:
        raise AppError("; ".join(problems))
    account_id, contact_id = await records.check_links(db, body.account_id, body.contact_id)
    if body.product_family_id and await db.get(ProductFamily, body.product_family_id) is None:
        raise AppError("Unknown product family")
    actor = _actor(current)
    now = datetime.now(UTC)
    payload = {**data, **data["details"], "lines": data["lines"]}
    if not (payload.get("summary") or "").strip():
        first = next((ln["description"].strip() for ln in data["lines"]
                      if (ln.get("description") or "").strip()), None)
        more = len([ln for ln in data["lines"] if (ln.get("description") or "").strip()]) - 1
        payload["summary"] = f"{first} and {more} more" if first and more > 0 else first
    e = await records.create(db, current.tenant_id, source="manual", actor=actor, data=payload,
                             account_id=account_id, contact_id=contact_id,
                             product_family_id=body.product_family_id, needs_review=False,
                             review_reasons=[], confidence=None, at=now)
    # A person who took the enquiry has already answered the customer.
    e.first_response_at, e.first_response_by = now, actor
    if body.received_how:
        await records.event(db, e, "received_how", actor, how=body.received_how)
    await timers.start(db, e, "quote", now)
    cfg = await settings_mod.load(db, current.tenant_id)
    kind, other, score, why = await dedupe.check(db, e, now)
    if other is not None and kind in ("flag", "merge"):
        await dedupe.flag(db, e, other, score, why)  # a person's enquiry is never merged alone
    await routing.route(db, e, cfg["routing"], actor=actor, now=now)
    return await _detail(db, e, current)


class EditIn(BaseModel):
    summary: str | None = Field(default=None, max_length=300)
    customer: CustomerIn | None = None
    details: DetailsIn | None = None
    is_tender: bool | None = None
    product_family_id: uuid.UUID | None = None
    clear_product_family: bool = False
    language: str | None = Field(default=None, max_length=10)
    account_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    checked: bool | None = None  # a person checked what was read


@router.patch("/{enquiry_id}")
async def edit(enquiry_id: uuid.UUID, body: EditIn, current: CurrentUser = Depends(WORK),
               db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    e = await _enq(db, enquiry_id, lock=True)
    actor = _actor(current)
    changed: dict[str, Any] = {}
    if body.summary is not None and body.summary != e.summary:
        changed["summary"] = [e.summary, body.summary]
        e.summary = body.summary.strip() or None
    if body.customer is not None:
        new = {k: (v or None) for k, v in body.customer.model_dump().items()}
        if new != {k: e.customer.get(k) for k in new}:
            changed["customer"] = True
            e.customer = {**e.customer, **new}
    if body.details is not None:
        new = {k: (v or None) for k, v in body.details.model_dump().items()}
        if new != {k: e.details.get(k) for k in new}:
            changed["details"] = True
            e.details = {**e.details, **new}
    if body.is_tender is not None and body.is_tender != e.is_tender:
        changed["is_tender"] = [e.is_tender, body.is_tender]
        e.is_tender = body.is_tender
    if body.clear_product_family or (body.product_family_id and
                                     body.product_family_id != e.product_family_id):
        if body.product_family_id and await db.get(ProductFamily, body.product_family_id) is None:
            raise AppError("Unknown product family")
        changed["product_family_id"] = [str(e.product_family_id) if e.product_family_id
                                        else None, str(body.product_family_id)
                                        if body.product_family_id else None]
        e.product_family_id = None if body.clear_product_family else body.product_family_id
    if body.language is not None and body.language != e.language:
        changed["language"] = [e.language, body.language]
        e.language = body.language or None
    if body.account_id is not None or body.contact_id is not None:
        acc, con = await records.check_links(db, body.account_id or None,
                                             body.contact_id or None)
        if (acc, con) != (e.account_id, e.contact_id):
            changed["customer_link"] = [str(acc) if acc else None, str(con) if con else None]
            e.account_id, e.contact_id = acc, con
    if body.checked:
        e.needs_review, changed["checked"] = False, True
    if changed:
        e.updated_at = datetime.now(UTC)
        await records.event(db, e, "edited", actor, changed=changed)
        await records.audit(db, e, "enquiry.edited", actor, fields=sorted(changed))
    return await _detail(db, e, current)


@router.post("/{enquiry_id}/lines", status_code=201)
async def add_line(enquiry_id: uuid.UUID, body: LineIn, current: CurrentUser = Depends(WORK),
                   db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    e = await _enq(db, enquiry_id, lock=True)
    last = await db.scalar(select(func.max(EnquiryLine.position)).where(
        EnquiryLine.enquiry_id == e.id)) or 0
    rows = records.add_lines(db, e, [body.model_dump()], source="person", start=last)
    await db.flush()
    await records.event(db, e, "line_added", _actor(current), line_id=str(rows[0].id))
    return _line(rows[0])


async def _line_of(db: AsyncSession, e: Enquiry, line_id: uuid.UUID) -> EnquiryLine:
    ln = await db.get(EnquiryLine, line_id)
    if ln is None or ln.enquiry_id != e.id or ln.removed_at is not None:
        raise NotFound("Line not found")
    return ln


@router.patch("/{enquiry_id}/lines/{line_id}")
async def correct_line(enquiry_id: uuid.UUID, line_id: uuid.UUID, body: LineIn,
                       current: CurrentUser = Depends(WORK),
                       db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """A person's correction. The line as first read stays in `original`, so each
    correction is a pair the reader can learn from (F1.4)."""
    e = await _enq(db, enquiry_id, lock=True)
    ln = await _line_of(db, e, line_id)
    vals = records.line_values(body.model_dump())
    before = {k: getattr(ln, k) for k in vals}
    if before == vals:
        return _line(ln)
    for k, v in vals.items():
        setattr(ln, k, v)
    now = datetime.now(UTC)
    ln.corrected_by, ln.corrected_at, ln.updated_at = _actor(current), now, now
    ln.unsure = []  # a person has checked the whole line
    await records.event(db, e, "line_corrected", _actor(current), line_id=str(ln.id),
                        fields=sorted(k for k in vals if before[k] != vals[k]))
    return _line(ln)


@router.delete("/{enquiry_id}/lines/{line_id}")
async def remove_line(enquiry_id: uuid.UUID, line_id: uuid.UUID,
                      current: CurrentUser = Depends(WORK),
                      db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    e = await _enq(db, enquiry_id, lock=True)
    ln = await _line_of(db, e, line_id)
    ln.removed_at = datetime.now(UTC)
    await records.event(db, e, "line_removed", _actor(current), line_id=str(ln.id),
                        description=ln.description[:100])
    return {"removed": True}


@router.post("/{enquiry_id}/add-customer")
async def add_customer(enquiry_id: uuid.UUID, current: CurrentUser = Depends(WORK),
                       db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Add the sender as a contact (and the company as an account when it is new)."""
    if not has_permission(current.permissions, "accounts.write"):
        raise AppError("Adding contacts needs the permission to edit accounts and contacts")
    e = await _enq(db, enquiry_id, lock=True)
    if e.contact_id:
        raise AppError("This enquiry already has a contact")
    first = await db.scalar(select(InboundEmail.from_address).where(
        InboundEmail.enquiry_id == e.id).order_by(InboundEmail.received_at).limit(1))
    acc, con = await records.add_customer(db, e.tenant_id, e.customer, first, e.account_id,
                                          actor=_actor(current), source="manual")
    e.account_id, e.contact_id = acc, con
    await db.execute(InboundEmail.__table__.update().where(
        InboundEmail.enquiry_id == e.id, InboundEmail.contact_id.is_(None)).values(contact_id=con))
    await db.execute(Message.__table__.update().where(
        Message.enquiry_id == e.id, Message.direction == "in", Message.contact_id.is_(None))
        .values(contact_id=con, account_id=acc))
    await records.event(db, e, "customer_added", _actor(current), account_id=acc, contact_id=con)
    return await _detail(db, e, current)


class AssignIn(BaseModel):
    user_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=300)


@router.post("/{enquiry_id}/assign")
async def assign(enquiry_id: uuid.UUID, body: AssignIn, current: CurrentUser = Depends(ASSIGN),
                 db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    e = await _enq(db, enquiry_id, lock=True)
    await routing.reassign(db, e, body.user_id, reason=body.reason, actor=_actor(current))
    return await _detail(db, e, current)


@router.get("/{enquiry_id}/acknowledgement")
async def ack_draft(enquiry_id: uuid.UUID, current: CurrentUser = Depends(WORK),
                    db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """The acknowledgement Forge would send, for a person to check and send."""
    e = await _enq(db, enquiry_id)
    contact = await db.get(Contact, e.contact_id) if e.contact_id else None
    first = await db.scalar(select(InboundEmail).where(InboundEmail.enquiry_id == e.id)
                            .order_by(InboundEmail.received_at).limit(1))
    given = {q["key"] for q in e.questions if q.get("answered_at")}
    lines = list(await db.scalars(select(EnquiryLine).where(
        EnquiryLine.enquiry_id == e.id, EnquiryLine.removed_at.is_(None))))
    if lines:
        given.add("product")
        if all(ln.qty is not None for ln in lines):
            given.add("qty")
        if any(ln.material or ln.standard for ln in lines):
            given.add("grade")
        if any(ln.dimensions for ln in lines):
            given |= {"size", "dimensions"}
    if e.details.get("delivery_location"):
        given.add("delivery_location")
    if e.details.get("target_date") or e.details.get("target_date_text"):
        given.add("required_date")
    out = await ack.compose(db, e, contact, given, first.subject if first else None)
    out["sent_before"] = any(ev for ev in await db.scalars(select(EnquiryEvent.id).where(
        EnquiryEvent.enquiry_id == e.id, EnquiryEvent.kind == "acknowledged")))
    out["checklist"] = [{"key": i["key"], "label": i.get("label")}
                        for i in await ack.checklist(db, e) if i.get("required")]
    return out


class AckIn(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=8000)
    language: Literal["en", "hi", "te"] = "en"
    question_keys: list[str] = Field(default_factory=list, max_length=10)


@router.post("/{enquiry_id}/acknowledge")
async def send_ack(enquiry_id: uuid.UUID, body: AckIn, current: CurrentUser = Depends(WORK),
                   db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    if not has_permission(current.permissions, "messages.send"):
        raise AppError("Sending to customers needs the permission to send messages")
    load_definitions()
    e = await _enq(db, enquiry_id, lock=True)
    msg = await acknowledge(db, e, MessageArgs(enquiry_id=e.id, **body.model_dump()),
                            actor=_actor(current), ai=False, agent_sent=False)
    out = await _detail(db, e, current)
    out["sent_message_id"] = str(msg.id)
    return out


@router.post("/{enquiry_id}/quote-sent")
async def mark_quote_sent(enquiry_id: uuid.UUID, current: CurrentUser = Depends(WORK),
                          db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    e = await _enq(db, enquiry_id, lock=True)
    await intake.quote_sent(db, e, _actor(current))
    return await _detail(db, e, current)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=1, max_length=300)


@router.post("/{enquiry_id}/close")
async def close(enquiry_id: uuid.UUID, body: ReasonIn, current: CurrentUser = Depends(WORK),
                db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    e = await _enq(db, enquiry_id, lock=True)
    await intake.close(db, e, _actor(current), body.reason)
    return await _detail(db, e, current)


@router.post("/{enquiry_id}/reopen")
async def reopen(enquiry_id: uuid.UUID, current: CurrentUser = Depends(WORK),
                 db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    e = await _enq(db, enquiry_id, lock=True)
    await intake.reopen(db, e, _actor(current))
    return await _detail(db, e, current)


class LabelIn(BaseModel):
    label: Literal["enquiry", "complaint", "payment", "order_status", "vendor_mail", "spam"]


@router.post("/{enquiry_id}/not-enquiry")
async def not_enquiry(enquiry_id: uuid.UUID, body: LabelIn, current: CurrentUser = Depends(WORK),
                      db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    e = await _enq(db, enquiry_id, lock=True)
    await intake.not_enquiry(db, e, body.label, _actor(current))
    return await _detail(db, e, current)


class DuplicateIn(BaseModel):
    other_id: uuid.UUID
    same: bool


@router.post("/{enquiry_id}/duplicate")
async def duplicate(enquiry_id: uuid.UUID, body: DuplicateIn,
                    current: CurrentUser = Depends(WORK),
                    db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    e = await _enq(db, enquiry_id, lock=True)
    other = await _enq(db, body.other_id, lock=True)
    await dedupe.decide(db, e, other, body.same, actor=_actor(current))
    return await _detail(db, e, current)


@router.post("/{enquiry_id}/undo-merge")
async def undo_merge(enquiry_id: uuid.UUID, current: CurrentUser = Depends(WORK),
                     db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    e = await _enq(db, enquiry_id, lock=True)
    await dedupe.undo(db, e, actor=_actor(current))
    return await _detail(db, e, current)


@router.get("/files/{file_id}")
async def download(file_id: uuid.UUID, current: CurrentUser = Depends(READ),
                   db: AsyncSession = Depends(get_db)) -> Response:
    """Files are downloaded, never opened in Forge's page (N9 A)."""
    f = await db.get(StoredFile, file_id)
    if f is None:
        raise NotFound("File not found")
    data = store.read(f)
    return Response(content=data, media_type="application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="{store.safe_name(f.filename)}"',
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


# Other mail ------------------------------------------------------------------------

@router.get("/mail/other")
async def other_mail(label: str | None = None, show: str = Query("other"),
                     q: str | None = None, limit: int = Query(100, le=300),
                     _: CurrentUser = Depends(READ),
                     db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    stmt = select(InboundEmail).where(InboundEmail.erased_at.is_(None))
    if show == "other":
        stmt = stmt.where(InboundEmail.outcome == "other")
    elif show == "automatic":
        stmt = stmt.where(InboundEmail.outcome.in_(("auto", "receipt")))
    elif show == "failed":
        stmt = stmt.where(InboundEmail.stage == "failed")
    elif show == "waiting":
        stmt = stmt.where(InboundEmail.stage == "new")
    else:
        raise AppError("Unknown list")
    if label:
        stmt = stmt.where(InboundEmail.label == label)
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(InboundEmail.subject.ilike(like),
                              InboundEmail.from_address.ilike(like),
                              InboundEmail.from_name.ilike(like)))
    rows = await db.scalars(stmt.order_by(InboundEmail.received_at.desc()).limit(limit))
    return [_email(r, full=False) for r in rows]


async def _row(db: AsyncSession, email_id: uuid.UUID, lock: bool = False) -> InboundEmail:
    r = await db.get(InboundEmail, email_id, with_for_update=lock)
    if r is None:
        raise NotFound("Email not found")
    return r


@router.get("/mail/{email_id}")
async def get_email(email_id: uuid.UUID, _: CurrentUser = Depends(READ),
                    db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return _email(await _row(db, email_id))


@router.post("/mail/{email_id}/label")
async def label_email(email_id: uuid.UUID, body: LabelIn, current: CurrentUser = Depends(WORK),
                      db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    r = await _row(db, email_id, lock=True)
    if body.label == "enquiry":
        raise AppError("To make it an enquiry, use 'Make it an enquiry'")
    await intake.relabel(db, r, body.label, _actor(current))
    return _email(r)


@router.post("/mail/{email_id}/convert")
async def convert(email_id: uuid.UUID, current: CurrentUser = Depends(WORK),
                  db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    r = await _row(db, email_id, lock=True)
    e = await intake.convert(db, r, _actor(current))
    return {"enquiry_id": str(e.id), "ref": e.ref}


@router.post("/mail/{email_id}/retry")
async def retry(email_id: uuid.UUID, current: CurrentUser = Depends(WORK),
                db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    r = await _row(db, email_id, lock=True)
    if r.stage != "failed":
        raise AppError("Only an email that could not be read is tried again")
    r.stage, r.attempts, r.next_attempt_at, r.last_error = "new", 0, None, None
    return _email(r)


# The Test mailbox ---------------------------------------------------------------------

class FileIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="application/octet-stream", max_length=150)
    data_base64: str


class TestMailIn(BaseModel):
    from_address: str = Field(min_length=3, max_length=320)
    from_name: str | None = Field(default=None, max_length=200)
    subject: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=100_000)
    reply_to_message_id: uuid.UUID | None = None
    attachments: list[FileIn] = Field(default_factory=list, max_length=10)


@router.post("/test-mailbox", status_code=201)
async def test_mailbox(body: TestMailIn, current: CurrentUser = Depends(WORK),
                       db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Write an email as a customer would. It goes through everything a real email
    does; Forge's answers land in the Test inbox, never in a real mailbox."""
    from app.steward.identifiers import normalize_email

    address = normalize_email(body.from_address)
    if not address:
        raise AppError("Give a valid sender address")
    files = []
    for f in body.attachments:
        try:
            files.append(mime.Attachment(store.safe_name(f.filename), f.content_type,
                                         base64.b64decode(f.data_base64, validate=True)))
        except (binascii.Error, ValueError) as exc:
            raise AppError(f"{f.filename} could not be read") from exc
    in_reply_to = None
    refs: list[str] = []
    if body.reply_to_message_id:
        m = await db.get(Message, body.reply_to_message_id)
        if m is None or m.direction != "out" or not m.provider_ref:
            raise AppError("Reply to a message Forge sent")
        in_reply_to, refs = m.provider_ref, [m.provider_ref]
    raw = mime.build(from_address=address, from_name=body.from_name, to="sales@forge.test",
                     subject=body.subject, body=body.body or " ", attachments=files,
                     in_reply_to=in_reply_to, references=refs)
    if len(raw) > intake.max_bytes():
        raise AppError("A test email can bring up to 25 MB")
    row, _ = await intake.ingest(db, current.tenant_id, raw, provider="test",
                                 provider_id=f"test-{uuid.uuid4()}", actor=_actor(current))
    outcome = await intake.process(db, row) if row.stage == "new" else row.outcome
    enq = await db.get(Enquiry, row.enquiry_id) if row.enquiry_id else None
    return {"email_id": str(row.id), "outcome": outcome, "label": row.label,
            "label_source": row.label_source,
            "enquiry_id": str(enq.id) if enq else None, "ref": enq.ref if enq else None}


@router.get("/test-mailbox/recent")
async def test_recent(_: CurrentUser = Depends(READ),
                      db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = list(await db.scalars(select(InboundEmail).where(InboundEmail.provider == "test")
                                 .order_by(InboundEmail.received_at.desc()).limit(30)))
    refs = dict((await db.execute(select(Enquiry.id, Enquiry.ref).where(
        Enquiry.id.in_({r.enquiry_id for r in rows if r.enquiry_id})))).all())
    replies: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for m in await db.scalars(select(Message).where(
            Message.enquiry_id.in_(list(refs)), Message.direction == "out")
            .order_by(Message.created_at)):
        replies.setdefault(m.enquiry_id, []).append(
            {"id": str(m.id), "subject": m.subject, "status": m.status, "body": m.body,
             "feature": m.feature, "created_at": m.created_at})
    return [{**_email(r, full=False), "ref": refs.get(r.enquiry_id),
             "replies": replies.get(r.enquiry_id, [])} for r in rows]


# Setup ---------------------------------------------------------------------------

@router.get("/setup/settings")
async def get_settings_(current: CurrentUser = Depends(READ),
                        db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    out = await settings_mod.load(db, current.tenant_id)
    out["suggestions"] = await routing.suggestions(db)
    out["step_labels"] = settings_mod.STEP_LABELS
    return out


@router.put("/setup/settings/{part}")
async def put_settings(part: Literal["routing", "timers", "intake"], body: dict[str, Any],
                       current: CurrentUser = Depends(MANAGE),
                       db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    from app.audit import chain

    current_cfg = (await settings_mod.load(db, current.tenant_id))[part]
    data = {**current_cfg, **{k: v for k, v in body.items() if k in current_cfg}}
    problems = await settings_mod.problems(db, part, data)
    if problems:
        raise AppError("; ".join(problems))
    before, after = await settings_mod.save(db, current.tenant_id, part, data, _actor(current))
    await chain.append(db, current.tenant_id, "enquiry.settings.changed",
                       actor=_actor(current), data={"part": part, "before": before[part],
                                                    "after": after[part]})
    if part == "timers":
        await timers.apply_settings(db, current.tenant_id)
    after["suggestions"] = await routing.suggestions(db)
    after["step_labels"] = settings_mod.STEP_LABELS
    return after


@router.get("/setup/people")
async def people(_: CurrentUser = Depends(READ),
                 db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    counts = await routing.open_counts(db)
    gone = await routing.away(db, now)
    names = await _user_names(db)
    return [{"id": str(u.id), "full_name": u.full_name, "email": u.email,
             "is_active": u.is_active, "takes_enquiries": u.takes_enquiries,
             "product_family_ids": u.product_family_ids, "regions": u.regions,
             "max_open_enquiries": u.max_open_enquiries,
             "reports_to_id": str(u.reports_to_id) if u.reports_to_id else None,
             "reports_to_name": names.get(u.reports_to_id), "open": counts.get(u.id, 0),
             "away": u.id in gone, "stand_in_name": names.get(gone.get(u.id))}
            for u in await db.scalars(select(User).order_by(User.full_name))]


class PersonIn(BaseModel):
    takes_enquiries: bool
    product_family_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    regions: list[str] = Field(default_factory=list, max_length=50)
    max_open_enquiries: int | None = Field(default=None, ge=1, le=500)
    reports_to_id: uuid.UUID | None = None


@router.put("/setup/people/{user_id}")
async def put_person(user_id: uuid.UUID, body: PersonIn, current: CurrentUser = Depends(MANAGE),
                     db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    from app.audit import chain

    u = await db.get(User, user_id)
    if u is None:
        raise NotFound("User not found")
    if body.reports_to_id:
        if body.reports_to_id == u.id:
            raise AppError("A person cannot report to themselves")
        boss = await db.get(User, body.reports_to_id)
        if boss is None or not boss.is_active:
            raise AppError("The manager is not an active user")
        seen_ids, b = {u.id}, boss
        while b is not None and b.reports_to_id:  # no circles
            if b.reports_to_id in seen_ids:
                raise AppError("That would make a circle of managers")
            seen_ids.add(b.id)
            b = await db.get(User, b.reports_to_id)
    known = {f for f in await db.scalars(select(ProductFamily.id))}
    if any(f not in known for f in body.product_family_ids):
        raise AppError("Unknown product family")
    before = {"takes_enquiries": u.takes_enquiries, "product_family_ids": u.product_family_ids,
              "regions": u.regions, "max_open_enquiries": u.max_open_enquiries,
              "reports_to_id": str(u.reports_to_id) if u.reports_to_id else None}
    u.takes_enquiries = body.takes_enquiries
    u.product_family_ids = [str(f) for f in body.product_family_ids]
    u.regions = sorted({r.strip() for r in body.regions if r.strip()})
    u.max_open_enquiries, u.reports_to_id = body.max_open_enquiries, body.reports_to_id
    await chain.append(db, current.tenant_id, "user.enquiry_profile.changed",
                       actor=_actor(current), record_type="user", record_id=u.id,
                       data={"user_id": u.id, "before": before})
    return {"id": str(u.id), "saved": True}


@router.get("/setup/families")
async def list_families(_: CurrentUser = Depends(READ),
                        db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = await db.scalars(select(ProductFamily).order_by(ProductFamily.name))
    return {"families": [families.out(f) for f in rows],
            "templates": [{"key": k, "name": t["name"], "keywords": t["keywords"],
                           "checklist": t["checklist"]} for k, t in families.TEMPLATES.items()],
            "general": families.GENERAL}


class FamilyIn(BaseModel):
    template: str | None = None
    name: str | None = Field(default=None, max_length=100)
    keywords: list[str] | None = None
    checklist: list[dict[str, Any]] | None = None
    active: bool | None = None


@router.post("/setup/families", status_code=201)
async def create_family(body: FamilyIn, current: CurrentUser = Depends(MANAGE),
                        db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    data = {k: v for k, v in body.model_dump().items() if v is not None and k != "template"}
    f = await families.create(db, current.tenant_id, data, _actor(current), body.template)
    return families.out(f)


@router.put("/setup/families/{family_id}")
async def update_family(family_id: uuid.UUID, body: FamilyIn,
                        current: CurrentUser = Depends(MANAGE),
                        db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    f = await db.get(ProductFamily, family_id)
    if f is None:
        raise NotFound("Product family not found")
    data = {k: v for k, v in body.model_dump().items() if v is not None and k != "template"}
    return families.out(await families.update(db, f, data, _actor(current)))


@router.get("/setup/mailbox")
async def get_mailbox(_: CurrentUser = Depends(READ),
                      db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return {"mailbox": mail.view(await mail.get(db)),
            "gmail": {"ready": oauth.configured("gmail"), "missing": None
                      if oauth.configured("gmail") else oauth.missing("gmail"),
                      "redirect_uri": oauth.redirect_uri("gmail")},
            "microsoft": {"ready": oauth.configured("microsoft"), "missing": None
                          if oauth.configured("microsoft") else oauth.missing("microsoft"),
                          "redirect_uri": oauth.redirect_uri("microsoft")}}


class ImapIn(BaseModel):
    address: str = Field(max_length=320)
    imap_host: str = Field(max_length=255)
    imap_port: int | None = None
    imap_security: Literal["ssl", "starttls"] = "ssl"
    username: str | None = Field(default=None, max_length=320)
    password: str | None = Field(default=None, max_length=500)
    folder: str = Field(default="INBOX", max_length=100)


@router.put("/setup/mailbox/imap")
async def put_imap(body: ImapIn, current: CurrentUser = Depends(MANAGE),
                   db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    mb = await mail.connect_imap(db, current.tenant_id, body.model_dump(), _actor(current))
    return {"mailbox": mail.view(mb)}


@router.post("/setup/mailbox/oauth/{provider}")
async def start_oauth(provider: Literal["gmail", "microsoft"],
                      current: CurrentUser = Depends(MANAGE)) -> dict[str, str]:
    try:
        return {"url": oauth.auth_url(provider, oauth.state_for(current.tenant_id,
                                                                current.user.id, provider))}
    except MailError as e:
        raise AppError(str(e)) from e


@router.post("/setup/mailbox/check")
async def check_now(current: CurrentUser = Depends(MANAGE),
                    db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    mb = await mail.get(db)
    if mb is None or mb.secret_hint == "-":
        raise AppError("Connect the mailbox first")
    out = await mail.check(db, mb)
    return {**out, "mailbox": mail.view(mb)}


class PauseIn(BaseModel):
    paused: bool


@router.post("/setup/mailbox/pause")
async def pause_mailbox(body: PauseIn, current: CurrentUser = Depends(MANAGE),
                        db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    mb = await mail.get(db)
    if mb is None:
        raise AppError("No mailbox is connected")
    await mail.pause(db, mb, _actor(current), body.paused)
    return {"mailbox": mail.view(mb)}


@router.delete("/setup/mailbox")
async def disconnect_mailbox(current: CurrentUser = Depends(MANAGE),
                             db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    mb = await mail.get(db)
    if mb is None:
        raise AppError("No mailbox is connected")
    await mail.disconnect(db, mb, _actor(current))
    return {"mailbox": mail.view(mb)}


class OwnerIn(BaseModel):
    user_id: uuid.UUID | None = None


@router.put("/setup/account-owner/{account_id}")
async def account_owner(account_id: uuid.UUID, body: OwnerIn,
                        current: CurrentUser = Depends(require_permission("accounts.write")),
                        db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Who owns an account: its enquiries go to them first (N12 A)."""
    from app.audit import chain

    acc = await db.get(Account, account_id)
    if acc is None or acc.status != "active":
        raise NotFound("Account not found")
    if body.user_id:
        u = await db.get(User, body.user_id)
        if u is None or not u.is_active:
            raise AppError("The owner must be an active user")
    before = acc.owner_id
    acc.owner_id = body.user_id
    await chain.append(db, current.tenant_id, "account.owner.changed", actor=_actor(current),
                       record_type="account", record_id=acc.id, account_id=acc.id,
                       data={"account_id": acc.id, "before": before, "after": body.user_id})
    return {"account_id": str(acc.id), "owner_id": str(acc.owner_id) if acc.owner_id else None}
