"""The engineering review (decision P2 A): an enquiry that needs engineering gets a
review step for the company's engineering role, with the lines, the files and the
reasons (the tech package), and its own timer (Enquiries → Setup → Timers).

Anyone in the role can take it. They answer "we can make it" (the enquiry goes on to
its quote) or "we can't make it" (it becomes no fit, and a decline is drafted for a
person to approve, P1 A; a strategic account's owner and manager are told instead).
M3's engineering review (F3.8) will start from the same package.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.enquiries import capability, records, timers
from app.models import Enquiry, EnquiryLine, EnquiryReview, InboundEmail, Role, User

STAGE = "engineering_review"
OUTCOMES = {"can_make": "We can make it", "cannot_make": "We can't make it"}


async def current(session: AsyncSession, enq: Enquiry) -> EnquiryReview | None:
    return await session.scalar(select(EnquiryReview).where(
        EnquiryReview.enquiry_id == enq.id, EnquiryReview.status == "open"))


async def role_people(session: AsyncSession, role_id: uuid.UUID | None) -> set[uuid.UUID]:
    from app.approvals.chain import people

    if role_id is None:
        return set()
    return {uid for uid, p in (await people(session)).items() if role_id in p.roles}


async def package(session: AsyncSession, enq: Enquiry, reasons: list[str]) -> dict[str, Any]:
    """What the engineer needs: the lines with their stamps, the files, the reasons."""
    lines = await session.scalars(select(EnquiryLine).where(
        EnquiryLine.enquiry_id == enq.id, EnquiryLine.removed_at.is_(None))
        .order_by(EnquiryLine.position))
    files = []
    for row in await session.scalars(select(InboundEmail).where(
            InboundEmail.enquiry_id == enq.id).order_by(InboundEmail.received_at)):
        files += [{"filename": a.get("filename"), "file_id": a.get("file_id"),
                   "content_type": a.get("content_type")}
                  for a in row.attachments or [] if a.get("file_id")]
    return {"summary": enq.summary, "customer": enq.customer.get("company"),
            "special_requirements": enq.details.get("special_requirements"),
            "lines": [{"position": ln.position, "description": ln.description,
                       "dimensions": ln.dimensions, "material": ln.material,
                       "standard": ln.standard, "qty": str(ln.qty) if ln.qty is not None
                       else None, "unit": ln.unit, "notes": ln.notes,
                       "make_ability": ln.make_ability, "reasons": ln.make_reasons}
                      for ln in lines],
            "files": files, "reasons": reasons}


async def open_review(session: AsyncSession, enq: Enquiry, reasons: list[str], *, actor: str,
                      at: datetime | None = None) -> EnquiryReview:
    """Open the review (or bring an open one up to date) and tell the role."""
    from app.enquiries.routing import notify_people
    from app.enquiries.timers import escalation_people

    at = at or datetime.now(UTC)
    rv = await current(session, enq)
    profile = await capability.load(session, enq.tenant_id)
    role_id = profile.engineering_role_id if profile else None
    pkg = await package(session, enq, reasons)
    if rv is not None:
        rv.reasons, rv.package = reasons[:10], pkg
        return rv
    rv = EnquiryReview(tenant_id=enq.tenant_id, enquiry_id=enq.id, kind="engineering",
                       status="open", role_id=role_id, reasons=reasons[:10], package=pkg,
                       opened_by=actor, opened_at=at)
    session.add(rv)
    await session.flush()
    await timers.start(session, enq, STAGE, at)
    await records.event(session, enq, "review_opened", actor, review_id=str(rv.id),
                        role_id=str(role_id) if role_id else None)
    to = await role_people(session, role_id)
    note = "" if to else " No engineering role is set up, so the escalation people are told."
    await notify_people(session, enq, to or await escalation_people(session, enq),
                        f"{enq.ref} needs an engineering review",
                        ("; ".join(reasons[:2]) or enq.summary or "Open the enquiry") + note)
    return rv


async def take(session: AsyncSession, enq: Enquiry, rv: EnquiryReview, user_id: uuid.UUID,
               actor: str) -> None:
    if rv.status != "open":
        raise AppError("This review is already finished")
    rv.assignee_id, rv.taken_at = user_id, datetime.now(UTC)
    await records.event(session, enq, "review_taken", actor, review_id=str(rv.id))


async def finish(session: AsyncSession, enq: Enquiry, rv: EnquiryReview, outcome: str,
                 notes: str | None, *, actor: str, user_id: uuid.UUID | None) -> None:
    """The engineer's answer. "We can't make it" turns the enquiry into no fit."""
    if rv.status != "open":
        raise AppError("This review is already finished")
    if outcome not in OUTCOMES:
        raise AppError("Say whether we can make it")
    notes = " ".join((notes or "").split())[:2000] or None
    if outcome == "cannot_make" and not notes:
        raise AppError("Say why we can't make it; the reason goes to the person who "
                       "approves the decline")
    now = datetime.now(UTC)
    rv.status, rv.outcome, rv.notes, rv.done_by, rv.done_at = "done", outcome, notes, actor, now
    if rv.assignee_id is None and user_id is not None:
        rv.assignee_id, rv.taken_at = user_id, now
    await timers.stop(session, enq, STAGE, now, actor=actor)
    await records.event(session, enq, "review_done", actor, review_id=str(rv.id),
                        outcome=outcome, notes=notes)


async def cancel(session: AsyncSession, enq: Enquiry, why: str, actor: str) -> None:
    rv = await current(session, enq)
    if rv is None:
        return
    now = datetime.now(UTC)
    rv.status, rv.notes, rv.done_by, rv.done_at = "cancelled", why[:2000], actor, now
    await timers.stop(session, enq, STAGE, now, "cancelled", actor)
    await records.event(session, enq, "review_cancelled", actor, review_id=str(rv.id), why=why)


async def view(session: AsyncSession, rv: EnquiryReview | None) -> dict[str, Any] | None:
    if rv is None:
        return None
    role = await session.get(Role, rv.role_id) if rv.role_id else None
    who = await session.get(User, rv.assignee_id) if rv.assignee_id else None
    return {"id": str(rv.id), "status": rv.status, "role": role.name if role else None,
            "assignee_id": str(rv.assignee_id) if rv.assignee_id else None,
            "assignee": who.full_name if who else None, "reasons": rv.reasons,
            "package": rv.package, "outcome": rv.outcome,
            "outcome_text": OUTCOMES.get(rv.outcome or ""), "notes": rv.notes,
            "opened_at": rv.opened_at, "taken_at": rv.taken_at, "done_by": rv.done_by,
            "done_at": rv.done_at}


async def latest(session: AsyncSession, enq: Enquiry) -> EnquiryReview | None:
    return await session.scalar(select(EnquiryReview).where(
        EnquiryReview.enquiry_id == enq.id).order_by(EnquiryReview.opened_at.desc()).limit(1))
