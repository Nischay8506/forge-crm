"""Enquiries as records: numbering, history, the customer behind one, and creating
one from what was read (F1.1) or typed by a person ("+ Manual enquiry")."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import chain
from app.core.errors import ValidationFailed
from app.events.outbox import publish
from app.models import Account, Contact, Enquiry, EnquiryEvent, EnquiryLine
from app.steward.identifiers import normalize_domain, normalize_email, normalize_phone
from app.steward.normalize import normalize_name

LINE_FIELDS = ("item_ref", "description", "material", "dimensions", "qty", "unit", "standard",
               "notes")
DETAIL_FIELDS = ("target_date", "target_date_text", "delivery_location",
                 "special_requirements", "tender_ref")
CUSTOMER_FIELDS = ("company", "contact_name", "designation", "phone", "email", "city",
                   "state")


async def next_ref(session: AsyncSession, tenant_id: uuid.UUID, at: datetime
                   ) -> tuple[int, str]:
    """ENQ-2026-00042: the year it came in and a number that only goes up."""
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                          {"k": f"enquiry_seq:{tenant_id}"})
    seq = (await session.scalar(select(func.max(Enquiry.seq)))) or 0
    return seq + 1, f"ENQ-{at.year}-{seq + 1:05d}"


async def event(session: AsyncSession, enq: Enquiry, kind: str, actor: str,
                **detail: Any) -> EnquiryEvent:
    ev = EnquiryEvent(tenant_id=enq.tenant_id, enquiry_id=enq.id, kind=kind, actor=actor,
                      detail=chain._plain(detail), at=datetime.now(UTC))
    session.add(ev)
    enq.last_activity_at = ev.at
    await session.flush()
    return ev


def _audit_kw(enq: Enquiry, actor: str, run_id: uuid.UUID | None = None) -> dict[str, Any]:
    return {"actor": actor, "agent_key": "enquiry" if actor == "agent:enquiry" else None,
            "run_id": run_id, "record_type": "enquiry", "record_id": enq.id,
            "account_id": enq.account_id, "contact_id": enq.contact_id,
            "evidence_class": "customer_money"}


async def audit(session: AsyncSession, enq: Enquiry, kind: str, actor: str,
                run_id: uuid.UUID | None = None, **data: Any) -> None:
    await chain.append(session, enq.tenant_id, kind, **_audit_kw(enq, actor, run_id),
                       data={"enquiry_id": enq.id, "ref": enq.ref, **data})


# The customer ---------------------------------------------------------------------

async def find_customer(session: AsyncSession, customer: dict[str, Any],
                        from_address: str | None) -> tuple[uuid.UUID | None, uuid.UUID | None,
                                                           str]:
    """(account, contact, how) for an existing customer: the one active contact with
    this email address; else an account with the same company name or email domain."""
    from app.channels.consent import contacts_for_address

    address = normalize_email(from_address) or normalize_email(customer.get("email"))
    if address:
        found = await contacts_for_address(session, address)
        if len(found) == 1:
            return found[0].account_id, found[0].id, "One contact has this email address"
        if len(found) > 1:
            return None, None, f"{len(found)} contacts have this email address; pick one"
    name = normalize_name(customer.get("company"))
    if name:
        ids = list(await session.scalars(select(Account.id).where(
            Account.status == "active", Account.normalized_name == name).limit(2)))
        if len(ids) == 1:
            return ids[0], None, "An account has this company name"
    domain = normalize_domain(address)
    if domain:
        ids = list(await session.scalars(select(Account.id).where(
            Account.status == "active", Account.domain == domain).limit(2)))
        if len(ids) == 1:
            return ids[0], None, "An account has this email domain"
    return None, None, "No account or contact matches yet"


async def add_customer(session: AsyncSession, tenant_id: uuid.UUID, customer: dict[str, Any],
                       from_address: str | None, account_id: uuid.UUID | None, *, actor: str,
                       source: str) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """Create the account (when none) and the contact from what the email says. The
    Data Steward then looks for duplicates of both, as for any new record."""
    from app.steward.records import create_account, create_contact

    company = (customer.get("company") or "").strip()
    if account_id is None and company:
        acc = await create_account(session, tenant_id, {
            "name": company[:300], "city": customer.get("city"), "state": customer.get("state"),
            "domain": normalize_domain(from_address or customer.get("email")),
        }, source=source, actor=actor, strict=False)
        account_id = acc.id
    contact_id = None
    name = (customer.get("contact_name") or "").strip()
    email = normalize_email(from_address) or normalize_email(customer.get("email"))
    if name or email:
        c = await create_contact(session, tenant_id, {
            "account_id": account_id, "full_name": (name or email or "")[:200],
            "email": email, "phone": normalize_phone(customer.get("phone")),
            "designation": (customer.get("designation") or None),
        }, source=source, actor=actor, strict=False)
        contact_id = c.id
    return account_id, contact_id


# Creating -------------------------------------------------------------------------

def _decimal(v: Any, places: int = 3) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return round(Decimal(str(v)), places)
    except Exception:  # noqa: BLE001
        return None


def line_values(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in LINE_FIELDS:
        v = data.get(f)
        if f == "qty":
            out[f] = _decimal(v)
        else:
            v = (str(v).strip() if v is not None else "") or None
            limit = EnquiryLine.__table__.c[f].type.length
            out[f] = v[:limit] if v and limit else v
    return out


def add_lines(session: AsyncSession, enq: Enquiry, lines: list[dict[str, Any]], *,
              source: str, start: int = 0) -> list[EnquiryLine]:
    rows = []
    for i, ln in enumerate(lines):
        vals = line_values(ln)
        if not vals.get("description"):
            continue
        conf = _decimal(ln.get("confidence"))
        src = source if source != "email" or ln.get("source") != "attachment" else "attachment"
        unsure = sorted({f for f in ln.get("unsure") or [] if f in LINE_FIELDS})
        row = EnquiryLine(tenant_id=enq.tenant_id, enquiry_id=enq.id, position=start + i + 1,
                          confidence=conf, source=src, unsure=unsure,
                          source_ref=(ln.get("source_file") or None) if src == "attachment"
                          else None,
                          original={k: (str(v) if isinstance(v, Decimal) else v)
                                    for k, v in vals.items()} if source != "person" else None,
                          **vals)
        session.add(row)
        rows.append(row)
    return rows


async def create(session: AsyncSession, tenant_id: uuid.UUID, *, source: str, actor: str,
                 data: dict[str, Any], account_id: uuid.UUID | None,
                 contact_id: uuid.UUID | None, product_family_id: uuid.UUID | None,
                 needs_review: bool, review_reasons: list[str], confidence: Decimal | None,
                 at: datetime | None = None, run_id: uuid.UUID | None = None) -> Enquiry:
    at = at or datetime.now(UTC)
    seq, ref = await next_ref(session, tenant_id, at)
    customer = {k: (data.get("customer") or {}).get(k) for k in CUSTOMER_FIELDS}
    details = {k: (str(data[k]) if data.get(k) is not None else None) for k in DETAIL_FIELDS}
    enq = Enquiry(tenant_id=tenant_id, seq=seq, ref=ref, status="new", source=source,
                  summary=(data.get("summary") or None) and str(data["summary"])[:300],
                  language=data.get("language"), account_id=account_id, contact_id=contact_id,
                  customer=customer, details=details, is_tender=bool(data.get("is_tender")),
                  needs_review=needs_review, review_reasons=review_reasons,
                  confidence=confidence, product_family_id=product_family_id,
                  waiting_on="us", questions=[], nudges=0, duplicate_reasons=[],
                  created_by=actor, created_at=at, updated_at=at, last_activity_at=at)
    session.add(enq)
    await session.flush()
    add_lines(session, enq, data.get("lines") or [],
              source="person" if source == "manual" else "email")
    await session.flush()
    await event(session, enq, "created", actor, source=source, needs_review=needs_review,
                reasons=review_reasons, lines=len(data.get("lines") or []))
    await audit(session, enq, "enquiry.created", actor, run_id, source=source,
                needs_review=needs_review, lines=len(data.get("lines") or []),
                is_tender=enq.is_tender, confidence=str(confidence) if confidence else None)
    await publish(session, tenant_id, "enquiry.created", {"enquiry_id": str(enq.id)})
    return enq


def manual_problems(data: dict[str, Any]) -> list[str]:
    out = []
    c = data.get("customer") or {}
    if not (c.get("company") or c.get("contact_name") or data.get("account_id")
            or data.get("contact_id")):
        out.append("Say who the enquiry is from")
    lines = data.get("lines") or []
    if not data.get("summary") and not any((ln.get("description") or "").strip()
                                           for ln in lines):
        out.append("Say what is asked for (a line or a summary)")
    if len(lines) > 200:
        out.append("Up to 200 lines")
    return out


async def check_links(session: AsyncSession, account_id: Any, contact_id: Any
                      ) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    acc = uuid.UUID(str(account_id)) if account_id else None
    con = uuid.UUID(str(contact_id)) if contact_id else None
    if con:
        c = await session.get(Contact, con)
        if c is None or c.status != "active":
            raise ValidationFailed(["That contact is not active"])
        if acc and c.account_id and c.account_id != acc:
            raise ValidationFailed(["That contact is not at that account"])
        acc = acc or c.account_id
    if acc:
        a = await session.get(Account, acc)
        if a is None or a.status != "active":
            raise ValidationFailed(["That account is not active"])
    return acc, con
