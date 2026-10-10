"""People checking the writing of AI-written messages (decisions L7 A, L11 A).

Two reasons a message gets a review:
  checks  it still broke a rule (glossary, wrong language, a placeholder) after one
          rewrite; it waits for a person
  sample  native-speaker sampling: every AI-written message in a language until the
          company's first 10 in that language are reviewed, then one in 20 (both
          numbers are on the Channel settings screen)

A sampled message an agent wants to send waits for the review. One a person already
chose to send goes, and is reviewed afterwards for quality (the score and comments
build the Done-When report: F12.5 stays "waiting for review" until each writing
language has its 10 reviewed messages).
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import chain
from app.core.errors import AppError
from app.language import WRITING, name
from app.models import LanguageReview, Message

DONE = ("approved", "changed", "rejected")
SAMPLE_TARGET = 10  # per language, for F12.5's Done-When (L11 A)


async def maybe_sample(session: AsyncSession, msg: Message, *, blocking: bool,
                       problems: list[str] | None = None) -> LanguageReview | None:
    """Open a review for this AI-written message if the sampling says so (or if it
    has problems left), else None."""
    from app.channels.rules import load

    lang = msg.language or "en"
    if problems:
        return await open_review(session, msg, why="checks", blocking=True, problems=problems)
    cfg = await load(session, msg.tenant_id)
    reviewed = await session.scalar(select(func.count()).select_from(LanguageReview).where(
        LanguageReview.language == lang, LanguageReview.status != "closed")) or 0
    if reviewed < cfg["review_first"]:
        return await open_review(session, msg, why="sample", blocking=blocking)
    sent = await session.scalar(select(func.count()).select_from(Message).where(
        Message.ai_generated.is_(True), Message.direction == "out", Message.language == lang,
        Message.status.not_in(("draft", "cancelled", "blocked")))) or 0
    if (sent + 1) % max(1, cfg["review_every"]) == 0:
        return await open_review(session, msg, why="sample", blocking=blocking)
    return None


async def open_review(session: AsyncSession, msg: Message, *, why: str, blocking: bool,
                      problems: list[str] | None = None) -> LanguageReview:
    existing = await session.scalar(select(LanguageReview).where(
        LanguageReview.message_id == msg.id, LanguageReview.status == "waiting"))
    if existing is not None:
        return existing
    r = LanguageReview(tenant_id=msg.tenant_id, message_id=msg.id, language=msg.language or "en",
                       why=why, blocking=blocking, problems=problems or [], status="waiting")
    session.add(r)
    await session.flush()
    return r


async def close_for(session: AsyncSession, msg: Message) -> None:
    """The message was cancelled: its waiting review has nothing left to decide."""
    for r in await session.scalars(select(LanguageReview).where(
            LanguageReview.message_id == msg.id, LanguageReview.status == "waiting",
            LanguageReview.blocking.is_(True))):
        r.status = "closed"


async def decide(session: AsyncSession, review_id: uuid.UUID, *, verdict: str, actor: str,
                 score: int | None = None, comment: str | None = None,
                 subject: str | None = None, body: str | None = None) -> LanguageReview:
    """approve: the writing is fine. change: a person corrected it (the original is
    kept). reject: it must not go (a waiting message is cancelled)."""
    from app.channels import send

    r = await session.get(LanguageReview, review_id, with_for_update=True)
    if r is None:
        raise AppError("Review not found")
    if r.status != "waiting":
        raise AppError(f"This review is already {r.status}")
    if verdict not in ("approve", "change", "reject"):
        raise AppError("Choose approve, change or reject")
    if score is not None and not 1 <= score <= 5:
        raise AppError("The score is from 1 to 5")
    msg = await session.get(Message, r.message_id)
    if msg is None:
        raise AppError("The message no longer exists")
    waiting = r.blocking and msg.status == "review"
    now = datetime.now(UTC)
    if verdict == "change":
        if not body or not body.strip():
            raise AppError("Give the corrected text")
        if not waiting:
            raise AppError("This message has already gone; give a score and say what "
                           "should change instead")
        r.original_body = msg.body
        msg.body = body.strip()
        if subject is not None and subject.strip():
            msg.subject = subject.strip()[:300]
    r.status = {"approve": "approved", "change": "changed", "reject": "rejected"}[verdict]
    r.score, r.comment = score, (comment or "").strip()[:1000] or None
    r.reviewed_by, r.reviewed_at = actor, now
    await chain.append(session, r.tenant_id, "language.review_decided", actor=actor,
                       record_type="message", record_id=msg.id, account_id=msg.account_id,
                       contact_id=msg.contact_id, data={
                           "review_id": r.id, "message_id": msg.id, "status": r.status,
                           "score": score, "why": r.why, "comment": r.comment})
    if waiting:
        if verdict == "reject":
            await send.cancel(session, msg, actor)
        else:
            await send.queue(session, msg, actor=msg.sent_by or actor)
    return r


async def report(session: AsyncSession) -> list[dict[str, Any]]:
    """Per writing language: reviews done, average score and how many needed changes,
    against the 10 per language F12.5's Done-When needs (L11 A)."""
    rows = (await session.execute(
        select(LanguageReview.language, LanguageReview.status, func.count())
        .where(LanguageReview.status.in_((*DONE, "waiting")))
        .group_by(LanguageReview.language, LanguageReview.status))).all()
    by: dict[str, dict[str, Any]] = {}
    for lang, status, n in rows:
        d = by.setdefault(lang, {"done": 0, "waiting": 0, "changed": 0, "rejected": 0})
        if status == "waiting":
            d["waiting"] += n
            continue
        d["done"] += n
        if status in ("changed", "rejected"):
            d[status] += n
    scores = (await session.execute(
        select(LanguageReview.language, func.avg(LanguageReview.score),
               func.count(LanguageReview.score))
        .where(LanguageReview.status.in_(DONE), LanguageReview.score.is_not(None))
        .group_by(LanguageReview.language))).all()
    avg = {lang: (float(a) if a is not None else None, n) for lang, a, n in scores}
    out = []
    for lang in sorted(set(WRITING) | set(by), key=lambda c: (c not in WRITING, c)):
        d = by.get(lang, {"done": 0, "waiting": 0, "changed": 0, "rejected": 0})
        a, scored = avg.get(lang, (None, 0))
        out.append({"language": lang, "name": name(lang), "reviewed": d["done"],
                    "waiting": d["waiting"], "changed": d["changed"],
                    "rejected": d["rejected"], "average_score": round(a, 2) if a else None,
                    "scored": scored, "target": SAMPLE_TARGET,
                    "met": d["done"] >= SAMPLE_TARGET})
    return out
