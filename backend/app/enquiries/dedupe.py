"""Duplicate enquiries (F1.5, decision N11 A).

The same RFQ often arrives twice: sent again in the same thread, or from the same
buyer's phone and mail within days. Forge merges on its own only when it is sure:
the same email thread, or the same phone number with overlapping lines, within 30
days. Anything else that looks alike is flagged "possible duplicate" for a person,
who says "same" (merge) or "different" (remembered, never flagged again). Every
merge can be undone.

A merge keeps both enquiries: the later one is marked merged into the earlier one,
whose page shows its lines and emails too. Nothing is moved, so undo is exact.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.enquiries import records, timers
from app.models import Enquiry, EnquiryLine, EnquiryPair, InboundEmail
from app.models.enquiries import OPEN_STATUSES
from app.steward.identifiers import normalize_email, normalize_phone
from app.steward.normalize import normalize_name

WINDOW_DAYS = 30
LINE_MATCH = 85  # rapidfuzz token-set score for two lines to count as the same item
FLAG_AT = 60


async def _lines(session: AsyncSession, enquiry_id: uuid.UUID) -> list[str]:
    return [f"{d} {m or ''} {s or ''}".strip() for d, m, s in (await session.execute(
        select(EnquiryLine.description, EnquiryLine.dimensions, EnquiryLine.material).where(
            EnquiryLine.enquiry_id == enquiry_id, EnquiryLine.removed_at.is_(None)))).all()]


def overlap(a: list[str], b: list[str]) -> float:
    """Share of the shorter list's lines found in the other (0..1)."""
    if not a or not b:
        return 0.0
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    hits = sum(1 for x in short if any(fuzz.token_set_ratio(x.lower(), y.lower()) >= LINE_MATCH
                                       for y in long_))
    return hits / len(short)


async def _thread(session: AsyncSession, enquiry_id: uuid.UUID) -> set[str]:
    return {k for k in await session.scalars(select(InboundEmail.thread_key).where(
        InboundEmail.enquiry_id == enquiry_id, InboundEmail.thread_key.is_not(None)))}


def _phone(enq: Enquiry) -> str | None:
    return normalize_phone(enq.customer.get("phone"))


def _email(enq: Enquiry) -> str | None:
    return normalize_email(enq.customer.get("email"))


async def compare(session: AsyncSession, new: Enquiry, old: Enquiry,
                  new_lines: list[str], new_threads: set[str]) -> tuple[str, int, list[str]]:
    """("merge" | "flag" | "none", score 0-100, reasons)."""
    reasons: list[str] = []
    old_threads = await _thread(session, old.id)
    if new_threads & old_threads:
        return "merge", 100, ["The same email thread"]
    old_lines = await _lines(session, old.id)
    share = overlap(new_lines, old_lines)
    same_phone = _phone(new) and _phone(new) == _phone(old)
    if same_phone and share > 0:
        return "merge", 95, ["The same phone number", f"{round(share * 100)}% of lines match"]
    score = 0
    if same_phone:
        score += 40
        reasons.append("The same phone number")
    if new.contact_id and new.contact_id == old.contact_id:
        score += 35
        reasons.append("The same contact")
    elif _email(new) and _email(new) == _email(old):
        score += 35
        reasons.append("The same email address")
    elif new.account_id and new.account_id == old.account_id:
        score += 20
        reasons.append("The same account")
    elif normalize_name(new.customer.get("company")) and normalize_name(
            new.customer.get("company")) == normalize_name(old.customer.get("company")):
        score += 20
        reasons.append("The same company name")
    if share:
        score += round(45 * share)
        reasons.append(f"{round(share * 100)}% of lines match")
    elif new.summary and old.summary and fuzz.token_set_ratio(
            new.summary.lower(), old.summary.lower()) >= 85:
        score += 25
        reasons.append("Very similar summaries")
    return ("flag" if score >= FLAG_AT and reasons else "none"), min(score, 99), reasons


async def check(session: AsyncSession, new: Enquiry, now: datetime | None = None
                ) -> tuple[str, Enquiry | None, int, list[str]]:
    """The best earlier match for a new enquiry within 30 days."""
    now = now or datetime.now(UTC)
    olds = list(await session.scalars(select(Enquiry).where(
        Enquiry.id != new.id, Enquiry.created_at >= now - timedelta(days=WINDOW_DAYS),
        Enquiry.created_at <= new.created_at, Enquiry.status.in_(OPEN_STATUSES),
        Enquiry.erased_at.is_(None)).order_by(Enquiry.created_at.desc()).limit(300)))
    if not olds:
        return "none", None, 0, []
    told = {(p.first_id, p.second_id) for p in await session.scalars(select(EnquiryPair).where(
        EnquiryPair.decision == "different"))}
    new_lines, new_threads = await _lines(session, new.id), await _thread(session, new.id)
    best: tuple[str, Enquiry | None, int, list[str]] = ("none", None, 0, [])
    for old in olds:
        if tuple(sorted((new.id, old.id), key=str)) in told:
            continue
        kind, score, reasons = await compare(session, new, old, new_lines, new_threads)
        if kind == "merge":
            return kind, old, score, reasons
        if kind == "flag" and score > best[2]:
            best = (kind, old, score, reasons)
    return best


async def flag(session: AsyncSession, new: Enquiry, old: Enquiry, score: int,
               reasons: list[str]) -> None:
    new.possible_duplicate_of_id, new.duplicate_score, new.duplicate_reasons = (
        old.id, score, reasons)
    await records.event(session, new, "possible_duplicate", "system:forge",
                        of=str(old.id), ref=old.ref, score=score, reasons=reasons)


async def merge(session: AsyncSession, merged: Enquiry, into: Enquiry, *, actor: str,
                reasons: list[str], automatic: bool, run_id: uuid.UUID | None = None) -> None:
    if merged.id == into.id:
        raise AppError("An enquiry cannot be merged into itself")
    if merged.status == "merged":
        raise AppError(f"{merged.ref} is already merged")
    if into.status not in OPEN_STATUSES:
        raise AppError(f"{into.ref} is {into.status}; merge into an open enquiry")
    now = datetime.now(UTC)
    before = merged.status
    merged.status, merged.merged_into_id, merged.merged_at, merged.merged_by = (
        "merged", into.id, now, actor)
    merged.possible_duplicate_of_id = None
    await timers.stop_all(session, merged, now, actor)
    await records.event(session, merged, "merged", actor, into=str(into.id), into_ref=into.ref,
                        reasons=reasons, automatic=automatic, status_before=before)
    await records.event(session, into, "merged_in", actor, other=str(merged.id),
                        other_ref=merged.ref, reasons=reasons, automatic=automatic)
    await records.audit(session, merged, "enquiry.merged", actor, run_id, into_id=into.id,
                        into_ref=into.ref, reasons=reasons, automatic=automatic)
    a, b = sorted((merged.id, into.id), key=str)
    await _remember(session, merged.tenant_id, a, b, "same", actor)


async def undo(session: AsyncSession, merged: Enquiry, *, actor: str) -> None:
    if merged.status != "merged" or not merged.merged_into_id:
        raise AppError(f"{merged.ref} is not merged")
    into = await session.get(Enquiry, merged.merged_into_id)
    now = datetime.now(UTC)
    merged.status = "open" if merged.owner_id else "new"
    merged.merged_into_id = merged.merged_at = merged.merged_by = None
    await records.event(session, merged, "merge_undone", actor,
                        was_in=str(into.id) if into else None)
    if into is not None:
        await records.event(session, into, "merge_undone", actor, other=str(merged.id),
                            other_ref=merged.ref)
        a, b = sorted((merged.id, into.id), key=str)
        await _remember(session, merged.tenant_id, a, b, "different", actor)
    await records.audit(session, merged, "enquiry.merge_undone", actor,
                        was_in_id=into.id if into else None)
    if merged.quote_sent_at is None:
        await timers.start(session, merged, "quote", now)
    if merged.owner_id:
        await timers.start(session, merged, "first_look", now)


async def decide(session: AsyncSession, enq: Enquiry, other: Enquiry, same: bool, *,
                 actor: str) -> None:
    """A person's word on a flagged pair."""
    if same:
        first, second = sorted((enq, other), key=lambda e: (e.created_at, e.seq))
        await merge(session, second, first, actor=actor,
                    reasons=["A person said they are the same"], automatic=False)
        return
    a, b = sorted((enq.id, other.id), key=str)
    await _remember(session, enq.tenant_id, a, b, "different", actor)
    for e in (enq, other):
        if e.possible_duplicate_of_id in (enq.id, other.id):
            e.possible_duplicate_of_id, e.duplicate_score, e.duplicate_reasons = None, None, []
    await records.event(session, enq, "not_duplicate", actor, other=str(other.id),
                        other_ref=other.ref)


async def _remember(session: AsyncSession, tenant_id: uuid.UUID, a: uuid.UUID, b: uuid.UUID,
                    decision: str, actor: str) -> None:
    row = await session.scalar(select(EnquiryPair).where(EnquiryPair.first_id == a,
                                                         EnquiryPair.second_id == b))
    if row is None:
        session.add(EnquiryPair(tenant_id=tenant_id, first_id=a, second_id=b, decision=decision,
                                decided_by=actor))
    else:
        row.decision, row.decided_by, row.decided_at = decision, actor, datetime.now(UTC)
    await session.flush()


def out_pair(enq: Enquiry) -> dict[str, Any] | None:
    if not enq.possible_duplicate_of_id:
        return None
    return {"of_id": str(enq.possible_duplicate_of_id), "score": enq.duplicate_score,
            "reasons": enq.duplicate_reasons}
