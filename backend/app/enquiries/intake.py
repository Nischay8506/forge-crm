"""From an arrived email to an enquiry, a reply on one, or Other mail (F1.1, F1.5,
F1.8, F1.9, F1.10; decisions N1-N13).

ingest()  keeps the email: the raw message and its attachments go to the file store
          (encrypted, up to 25 MB an email, N9 A), duplicates of the same message are
          dropped, bounces of our own messages become delivery receipts, and our own
          copies and out-of-office replies are put aside. It is quick and never calls
          the AI, so mail sources can call it for every message they find.
process() reads it (the Enquiry Agent, app/enquiries/reader.py), then:
          a reply in an enquiry's thread joins that enquiry (the customer answered:
          the questions are ticked off, the quote timer runs again, the owner is told);
          an enquiry is opened, checked for duplicates (F1.5), given to an owner
          (F1.9), its timers start (F1.10) and the customer is acknowledged (F1.8);
          anything else goes to Other mail (N5 A), and a complaint tells the chosen role.
Nothing read is ever dropped. Without an AI key every email that is not automatic
becomes an enquiry marked for a person to check, so no enquiry is missed.
"""

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import runtime
from app.agents.registry import load_definitions
from app.config import get_settings
from app.enquiries import (
    ack,
    dedupe,
    documents,
    families,
    mime,
    qualify,
    reader,
    records,
    routing,
    scoring,
    timers,
)
from app.enquiries import settings as settings_mod
from app.enquiries.agent import ACK_TOOL, AGENT_KEY, MERGE_TOOL, NUDGE_TOOL, PARK_TOOL, MessageArgs
from app.files import store
from app.models import (
    Contact,
    Enquiry,
    InboundEmail,
    Mailbox,
    Message,
    StoredFile,
    Tenant,
    User,
)
from app.models.enquiries import OPEN_STATUSES

log = logging.getLogger(__name__)

SYSTEM = "system:forge"
AGENT = f"agent:{AGENT_KEY}"
RETRY_AFTER = (timedelta(minutes=1), timedelta(minutes=5))
MAX_TRIES = 3
REF = re.compile(r"\[(ENQ-\d{4}-\d{5,})\]")


def max_bytes() -> int:
    return int(get_settings().email_max_mb) * 1024 * 1024


# Keeping ---------------------------------------------------------------------------

async def _own_addresses(session: AsyncSession) -> set[str]:
    from app.channels import accounts

    return await accounts.our_addresses(session) | {
        m.lower() for m in await session.scalars(select(Mailbox.address))}


async def ingest(session: AsyncSession, tenant_id: uuid.UUID, raw: bytes, *, provider: str,
                 provider_id: str, mailbox_id: uuid.UUID | None = None,
                 received_at: datetime | None = None, actor: str = SYSTEM
                 ) -> tuple[InboundEmail, bool]:
    """Keep one email. Returns (row, new); the same message twice gives the first row."""
    existing = await session.scalar(select(InboundEmail).where(
        InboundEmail.provider == provider, InboundEmail.provider_id == provider_id[:300]))
    if existing is not None:
        return existing, False
    p = mime.parse(raw)
    if p.message_id:
        same = await session.scalar(select(InboundEmail).where(
            InboundEmail.internet_message_id == p.message_id))
        if same is not None:
            return same, False
    now = datetime.now(UTC)
    too_big = len(raw) > max_bytes()
    row = InboundEmail(
        tenant_id=tenant_id, mailbox_id=mailbox_id, provider=provider,
        provider_id=provider_id[:300], internet_message_id=p.message_id,
        thread_key=p.thread_root, in_reply_to=p.in_reply_to,
        references=" ".join(p.references) or None, from_address=p.from_address,
        from_name=p.from_name, to_addresses=p.to, cc_addresses=p.cc,
        subject=p.subject or None, body_text=p.text[:200_000],
        received_at=received_at or p.date or now, size_bytes=len(raw), attachments=[],
        stage="new", attempts=0, created_at=now, updated_at=now)
    session.add(row)
    await session.flush()
    if too_big:
        row.attachments = [{"filename": a.filename, "content_type": a.content_type,
                            "size": len(a.data), "skipped": "The email is over "
                            f"{get_settings().email_max_mb} MB, so its files were not kept"}
                           for a in p.attachments]
    else:
        raw_file = await store.save(session, tenant_id, raw, filename=f"email-{row.id}.eml",
                                    content_type="message/rfc822", kind="raw_email",
                                    created_by=actor)
        row.raw_file_id = raw_file.id
        kept = []
        for a in p.attachments:
            f = await store.save(session, tenant_id, a.data, filename=a.filename,
                                 content_type=a.content_type, kind="attachment",
                                 created_by=actor)
            kept.append({"file_id": str(f.id), "filename": f.filename,
                         "content_type": f.content_type, "size": f.size_bytes})
        row.attachments = kept
    own = await _own_addresses(session)
    if p.from_address and p.from_address in own:
        row.auto_kind, row.stage, row.outcome = "own", "done", "auto"
    elif p.auto_kind == "bounce":
        row.auto_kind, row.stage = "bounce", "done"
        row.outcome = await _bounce(session, p) or "auto"
    elif p.auto_kind == "auto_reply":
        row.auto_kind, row.stage, row.outcome = "auto_reply", "done", "auto"
    elif p.auto_kind == "bulk":
        row.auto_kind = "bulk"  # still read: a newsletter can hide an RFQ
    await session.flush()
    return row, True


async def _bounce(session: AsyncSession, p: mime.ParsedEmail) -> str | None:
    """A bounce of one of our messages becomes its delivery receipt."""
    from app.channels import send

    b = p.bounce
    ref = b.original_message_id if b else None
    if not ref:
        return None
    msg = await session.scalar(select(Message).where(Message.provider_ref == ref[:200],
                                                     Message.direction == "out"))
    if msg is None:
        return None
    await send.record_receipt(session, msg, "bounced", source="provider",
                              reason=(b.reason or b.status) if b else None)
    return "receipt"


# Reading ---------------------------------------------------------------------------

async def _docs(session: AsyncSession, row: InboundEmail) -> list[documents.DocText]:
    out = []
    for a in row.attachments:
        if not a.get("file_id"):
            out.append(documents.DocText(a.get("filename") or "file", "other",
                                         note=a.get("skipped")))
            continue
        f = await session.get(StoredFile, uuid.UUID(a["file_id"]))
        if f is None or f.removed_at is not None:
            continue
        d = await documents.read(f.filename, f.content_type, store.read(f))
        d.file_id = str(f.id)
        out.append(d)
    return out


def _email(row: InboundEmail) -> dict[str, Any]:
    return {"from_address": row.from_address, "from_name": row.from_name,
            "to": row.to_addresses, "date": row.received_at.isoformat(),
            "subject": row.subject, "body": row.body_text}


async def _thread_enquiry(session: AsyncSession, row: InboundEmail) -> Enquiry | None:
    """The enquiry this email answers: by the thread's message ids, else by the
    reference number in the subject from the same sender."""
    ids = [i for i in [row.in_reply_to, *(row.references or "").split()] if i]
    enq_id = None
    if ids:
        enq_id = await session.scalar(select(InboundEmail.enquiry_id).where(
            InboundEmail.internet_message_id.in_(ids), InboundEmail.enquiry_id.is_not(None),
            InboundEmail.id != row.id).limit(1))
        if enq_id is None:
            enq_id = await session.scalar(select(Message.enquiry_id).where(
                Message.provider_ref.in_([i[:200] for i in ids]),
                Message.enquiry_id.is_not(None)).limit(1))
    if enq_id is None and row.subject:
        m = REF.search(row.subject)
        if m:
            enq = await session.scalar(select(Enquiry).where(Enquiry.ref == m.group(1)))
            if enq is not None and row.from_address and row.from_address in {
                    (enq.customer.get("email") or "").lower(), *[
                        (e or "").lower() for e in await session.scalars(
                            select(InboundEmail.from_address).where(
                                InboundEmail.enquiry_id == enq.id))]}:
                enq_id = enq.id
    if enq_id is None:
        return None
    enq = await session.get(Enquiry, enq_id)
    for _ in range(5):  # a merged enquiry's replies go to the one it was merged into
        if enq is None or enq.status != "merged" or not enq.merged_into_id:
            break
        enq = await session.get(Enquiry, enq.merged_into_id)
    return enq


async def _message(session: AsyncSession, row: InboundEmail, enquiry_id: uuid.UUID | None
                   ) -> Message:
    """The email as an incoming message in the conversation (Messages screen,
    replies, STOP)."""
    from app.channels import send

    msg = await send.receive(
        session, row.tenant_id, from_address=row.from_address or "unknown@unknown.invalid",
        subject=row.subject, body=row.body_text or "(no text)", provider=row.provider,
        provider_ref=(row.internet_message_id or f"<{row.provider_id}@{row.provider}>")[:200],
        in_reply_to_ref=(row.in_reply_to or None) and row.in_reply_to[:200],
        source="provider", at=row.received_at)
    if enquiry_id and msg.enquiry_id is None:
        msg.enquiry_id = enquiry_id
    row.message_id = msg.id
    row.contact_id = row.contact_id or msg.contact_id
    return msg


async def _complaint(session: AsyncSession, row: InboundEmail, cfg: dict[str, Any]) -> None:
    from app.approvals.chain import people
    from app.approvals.notify import tell

    role = cfg["intake"].get("complaints_role_id")
    everyone = await people(session)
    to = {uid for uid, p in everyone.items() if role and uuid.UUID(str(role)) in p.roles}
    if not to:
        from app.core.permissions import ALL

        to = {uid for uid, p in everyone.items() if ALL in p.permissions}
    if to:
        await tell(session, row.tenant_id, to, "A customer complaint arrived",
                   f"{row.from_name or row.from_address}: {row.subject or '(no subject)'}",
                   f"/enquiries/other-mail?email={row.id}", f"complaint-{row.id}")


def _decide(reading: reader.Reading, unsure_below: float) -> tuple[bool, list[str]]:
    """(open an enquiry?, why a person should check it)."""
    reasons: list[str] = []
    conf = float(reading.confidence)
    if reading.source == "rules":
        reasons.append(reading.why_rules or "Not read by AI")
        if reading.label != "enquiry":
            reasons.append(f"By its words it looks like {reading.label.replace('_', ' ')} mail")
        return True, reasons
    if reading.label == "enquiry":
        if conf < unsure_below:
            reasons.append(f"The AI was not sure this is an enquiry ({round(conf * 100)}%)")
        return True, reasons
    if reading.data.get("could_be_enquiry") and conf < unsure_below:
        reasons.append(f"The AI thought this was {reading.label.replace('_', ' ')} mail, but "
                       f"was not sure ({round(conf * 100)}%)")
        return True, reasons
    return False, reasons


async def _family_id(session: AsyncSession, name: str | None, text: str
                     ) -> uuid.UUID | None:
    fams = await families.active(session)
    if name:
        for f in fams:
            if f.name.lower() == name.lower():
                return f.id
    f = families.match(text, fams)
    return f.id if f else None


async def _open_enquiry(session: AsyncSession, ctx: Any, row: InboundEmail,
                        reading: reader.Reading, reasons: list[str], docs: list[Any],
                        cfg: dict[str, Any], now: datetime, auto: bool = True) -> Enquiry:
    """`auto`: the agent may add the customer and acknowledge on its own (not when a
    person moved the email here; they check it first)."""
    data = reading.data
    lines = data.get("lines") or []
    if not lines:
        reasons.append("No items were found; check the email and its files")
    for d in docs:
        if d.note and not d.text:
            reasons.append(f"{d.filename}: {d.note}")
        elif d.text and "unreadable" in d.text.lower():
            reasons.append(f"{d.filename}: parts could not be read and were left empty")
    unsure = sum(1 for ln in lines if ln.get("unsure"))
    if unsure:
        reasons.append(f"The reader was not sure of some details on {unsure} "
                       f"line{'s' if unsure > 1 else ''}; they are highlighted")
    account_id, contact_id, how = await records.find_customer(session, data.get("customer")
                                                              or {}, row.from_address)
    confident = auto and reading.source == "model" and reading.label == "enquiry" and \
        float(reading.confidence) >= float(cfg["intake"]["unsure_below"])
    if contact_id is None and confident and "pick one" not in how:
        account_id, contact_id = await records.add_customer(
            session, row.tenant_id, data.get("customer") or {}, row.from_address, account_id,
            actor=AGENT, source="signature")
    fam = await _family_id(session, data.get("product_family"),
                           f"{row.subject or ''}\n{row.body_text}\n"
                           + "\n".join(ln.get("description") or "" for ln in lines))
    enq = await records.create(
        session, row.tenant_id, source="email", actor=AGENT, data=data, account_id=account_id,
        contact_id=contact_id, product_family_id=fam, needs_review=bool(reasons),
        review_reasons=reasons[:10], confidence=reading.confidence, at=now, run_id=ctx.run_id)
    await records.event(session, enq, "customer_matched", AGENT, how=how,
                        account_id=account_id, contact_id=contact_id)
    row.enquiry_id, row.outcome, row.contact_id = enq.id, "enquiry", contact_id
    await _message(session, row, enq.id)
    await timers.start(session, enq, "first_response",
                       timers.first_response_start(row.received_at, now))
    await timers.start(session, enq, "quote", now)
    kind, other, score, why = await dedupe.check(session, enq, now)
    if kind == "merge" and other is not None:
        out = await runtime.call_tool(session, ctx, MERGE_TOOL, {
            "enquiry_id": str(enq.id), "into_id": str(other.id), "reasons": why},
            dedupe_key=f"merge:{enq.id}")
        if out.status == "done":
            await routing.notify_people(session, other, {other.owner_id} if other.owner_id
                                        else set(), f"{other.ref}: the customer sent it again",
                                        f"{enq.ref} was merged into it ({'; '.join(why)})")
            await scoring.rescore(session, other, actor=AGENT, why="sent again", now=now)
            return enq
        await dedupe.flag(session, enq, other, score, why)
    elif kind == "flag" and other is not None:
        await dedupe.flag(session, enq, other, score, why)
    await routing.route(session, enq, cfg["routing"], actor=AGENT, now=now)
    # Can we make it (F1.6), and what follows (P1 A, P2 A); then the score (F1.7).
    before = enq.make_ability
    await qualify.check(session, enq, actor=AGENT, ctx=ctx, at=now)
    await qualify.follow_up(session, enq, before, actor=AGENT, ctx=ctx,
                            original_subject=row.subject)
    await scoring.rescore(session, enq, actor=AGENT, why="new enquiry", now=now, notify=False)
    # A no fit is answered by the decline a person approves, not by an acknowledgement.
    declined = enq.make_ability == "no_fit" and not await qualify.strategic(session, enq)
    if cfg["intake"]["auto_acknowledge"] and confident and not declined:
        await _auto_acknowledge(session, ctx, enq, data, row)
    return enq


async def _auto_acknowledge(session: AsyncSession, ctx: Any, enq: Enquiry,
                            data: dict[str, Any], row: InboundEmail) -> None:
    contact = await session.get(Contact, enq.contact_id) if enq.contact_id else None
    text = await ack.compose(session, enq, contact, set(data.get("given") or []), row.subject)
    asked = ", ".join(text["question_keys"]) or "nothing"
    await runtime.call_tool(session, ctx, ACK_TOOL, {"enquiry_id": str(enq.id), **text},
                            recommend="approve",
                            recommendation=f"A fixed thank-you with the reference number; it "
                                           f"asks for: {asked}. No prices, dates or promises.",
                            dedupe_key=f"ack:{enq.id}")


async def _reply(session: AsyncSession, ctx: Any, row: InboundEmail, enq: Enquiry,
                 docs: list[Any], company: str, now: datetime) -> None:
    """The customer wrote again in the enquiry's thread."""
    row.enquiry_id, row.outcome = enq.id, "reply"
    await _message(session, row, enq.id)
    fams = await families.active(session)
    reading = await reader.read(session, ctx, email=_email(row), docs=docs, families=fams,
                                company=company, today=now.date(), last_try=True)
    row.label, row.label_source = reading.label, reading.source
    row.label_confidence, row.label_reason = reading.confidence, reading.data.get("reason")
    given = set(reading.data.get("given") or [])
    answered = ack.mark_answered(enq, given, now)
    new_lines = reading.data.get("lines") or []
    if new_lines:
        start = len(await dedupe._lines(session, enq.id))
        records.add_lines(session, enq, [{**ln, "source": "email"} for ln in new_lines],
                          source="reply", start=start)
    await records.event(session, enq, "customer_replied", AGENT, email_id=str(row.id),
                        answered=answered, lines=len(new_lines), read_by=reading.source)
    if new_lines:
        before = enq.make_ability
        await qualify.check(session, enq, actor=AGENT, ctx=ctx, at=now)
        await qualify.follow_up(session, enq, before, actor=AGENT, ctx=ctx)
    await scoring.rescore(session, enq, actor=AGENT, why="customer replied", now=now)
    if enq.status in ("waiting_customer", "parked"):
        enq.status, enq.waiting_on, enq.waiting_since, enq.parked_at = "open", "us", None, None
        await timers.resume(session, enq, "quote", now)
    if enq.owner_id:
        await routing.notify_people(session, enq, {enq.owner_id},
                                    f"{enq.ref}: the customer replied",
                                    (row.subject or "") + (f" (answered: {', '.join(answered)})"
                                                           if answered else ""))


async def process(session: AsyncSession, row: InboundEmail, now: datetime | None = None
                  ) -> str:
    """Read one kept email and act on it. Returns the outcome."""
    load_definitions()
    now = now or datetime.now(UTC)
    if row.stage != "new":
        return row.outcome or row.stage
    cfg = await settings_mod.load(session, row.tenant_id)
    tenant = await session.get(Tenant, row.tenant_id)
    company = tenant.name if tenant else "the company"
    ctx = await runtime.start_run(session, row.tenant_id, AGENT_KEY, "email", str(row.id))
    row.run_id = ctx.run_id
    docs = await _docs(session, row)
    thread = await _thread_enquiry(session, row)
    if thread is not None:
        await _reply(session, ctx, row, thread, docs, company, now)
        row.stage = "done"
        await runtime.finish_run(session, ctx, summary={"outcome": "reply",
                                                        "enquiry": thread.ref})
        return "reply"
    try:
        reading = await reader.read(session, ctx, email=_email(row), docs=docs,
                                    families=await families.active(session), company=company,
                                    today=now.date(), last_try=row.attempts + 1 >= MAX_TRIES)
    except reader.ModelUnavailable as exc:
        row.attempts += 1
        row.last_error = f"The AI could not be reached: {exc}"[:500]
        row.next_attempt_at = now + RETRY_AFTER[min(row.attempts, len(RETRY_AFTER)) - 1]
        await runtime.finish_run(session, ctx, "failed", {"error": row.last_error})
        return "retry"
    row.label, row.label_source = reading.label, reading.source
    row.label_confidence = reading.confidence
    row.label_reason = (reading.data.get("reason") or "")[:300] or None
    row.could_be_enquiry = bool(reading.data.get("could_be_enquiry"))
    is_enquiry, reasons = _decide(reading, float(cfg["intake"]["unsure_below"]))
    if is_enquiry:
        enq = await _open_enquiry(session, ctx, row, reading, reasons, docs, cfg, now)
        outcome, summary = "enquiry", {"outcome": "enquiry", "enquiry": enq.ref,
                                       "read_by": reading.source}
    else:
        row.outcome = outcome = "other"
        if row.in_reply_to:  # an answer to one of our messages (e.g. STOP) stays in it
            original = await session.scalar(select(Message.id).where(
                Message.provider_ref == row.in_reply_to[:200], Message.direction == "out"))
            if original is not None:
                await _message(session, row, None)
        if reading.label == "complaint":
            await _complaint(session, row, cfg)
        summary = {"outcome": "other", "label": reading.label}
    row.stage, row.last_error, row.next_attempt_at = "done", None, None
    row.updated_at = now
    await runtime.finish_run(session, ctx, summary=summary)
    return outcome


async def failed(session: AsyncSession, row: InboundEmail, error: str, now: datetime) -> None:
    row.attempts += 1
    row.last_error = error[:500]
    if row.attempts >= MAX_TRIES:
        row.stage = "failed"
        row.next_attempt_at = None
    else:
        row.next_attempt_at = now + RETRY_AFTER[min(row.attempts, len(RETRY_AFTER)) - 1]


# The worker -------------------------------------------------------------------------

async def process_due(tenant_id: uuid.UUID, now: datetime | None = None, limit: int = 20
                      ) -> dict[str, int]:
    """Read the emails waiting in one company, each in its own transaction."""
    from app.db import tenant_session

    now = now or datetime.now(UTC)
    async with tenant_session(tenant_id) as s:
        ids = list(await s.scalars(select(InboundEmail.id).where(
            InboundEmail.stage == "new",
            or_(InboundEmail.next_attempt_at.is_(None), InboundEmail.next_attempt_at <= now))
            .order_by(InboundEmail.received_at).limit(limit)))
    counts: dict[str, int] = {}
    for eid in ids:
        try:
            async with tenant_session(tenant_id) as s:
                row = await s.scalar(select(InboundEmail).where(InboundEmail.id == eid)
                                     .with_for_update(skip_locked=True))
                if row is None or row.stage != "new":
                    continue
                outcome = await process(s, row, now)
        except Exception as exc:  # one bad email must not stop the others
            log.exception("reading email %s failed", eid)
            async with tenant_session(tenant_id) as s:
                row = await s.get(InboundEmail, eid)
                if row is not None and row.stage == "new":
                    await failed(s, row, f"{type(exc).__name__}: {exc}", now)
            outcome = "error"
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


async def followups(session: AsyncSession, tenant_id: uuid.UUID, now: datetime | None = None
                    ) -> dict[str, int]:
    """Nudge on the set days while we wait on the customer, then park (F1.8)."""
    from app.agents import controls

    load_definitions()
    now = now or datetime.now(UTC)
    for ks in await controls.active_switches(session):
        if ks.scope == "tenant" or (ks.scope == "agent" and ks.agent_key == AGENT_KEY):
            return {}
    cfg = (await settings_mod.load(session, tenant_id))["intake"]
    days, park_after = list(cfg["nudge_days"]), int(cfg["park_after_days"])
    counts: dict[str, int] = {}
    ctx = None
    for enq in await session.scalars(select(Enquiry).where(
            Enquiry.status == "waiting_customer", Enquiry.waiting_since.is_not(None))):
        waited = (now - enq.waiting_since).total_seconds() / 86400
        if ctx is None:
            ctx = await runtime.start_run(session, tenant_id, AGENT_KEY, "followups")
        if enq.nudges < len(days) and waited >= days[enq.nudges]:
            contact = await session.get(Contact, enq.contact_id) if enq.contact_id else None
            text = await ack.compose_nudge(session, enq, contact)
            if text is None:
                continue
            out = await runtime.call_tool(
                session, ctx, NUDGE_TOOL, MessageArgs(enquiry_id=enq.id, **text),
                recommend="approve", recommendation="The customer has not sent the details "
                f"asked for {int(waited)} days ago.", dedupe_key=f"nudge:{enq.id}:{enq.nudges + 1}")
            counts[out.status] = counts.get(out.status, 0) + 1
        elif enq.nudges >= len(days) and waited >= park_after:
            out = await runtime.call_tool(session, ctx, PARK_TOOL, {"enquiry_id": str(enq.id)},
                                          dedupe_key=f"park:{enq.id}")
            counts["parked" if out.status == "done" else out.status] = counts.get(
                "parked" if out.status == "done" else out.status, 0) + 1
    if ctx is not None:
        await runtime.finish_run(session, ctx, summary=counts)
    return counts


async def on_message_sent(session: AsyncSession, msg: Message, at: datetime) -> None:
    """Our first message about an enquiry ends its first-response timer."""
    if msg.enquiry_id is None or msg.direction != "out":
        return
    enq = await session.get(Enquiry, msg.enquiry_id)
    if enq is None:
        return
    if enq.first_response_at is None:
        enq.first_response_at, enq.first_response_by = at, msg.sent_by or msg.created_by
        await timers.stop(session, enq, "first_response", at)
    await records.event(session, enq, "message_sent", msg.sent_by or msg.created_by,
                        message_id=str(msg.id), feature=msg.feature)


async def tick_all(now: datetime | None = None) -> dict[str, int]:
    """Every company: read waiting emails and run the timers."""
    from app.db import get_sessionmaker, tenant_session

    now = now or datetime.now(UTC)
    async with get_sessionmaker()() as s:
        tenant_ids = list(await s.scalars(select(Tenant.id)))
    total: dict[str, int] = {}
    for tid in tenant_ids:
        try:
            for k, v in (await process_due(tid, now)).items():
                total[k] = total.get(k, 0) + v
            async with tenant_session(tid) as s:
                for k, v in (await timers.tick(s, tid, now)).items():
                    total[k] = total.get(k, 0) + v
        except Exception:  # one company's failure must not stop the others
            log.exception("enquiry tick for tenant %s failed", tid)
    return total


async def followups_all(now: datetime | None = None) -> dict[str, int]:
    from app.db import get_sessionmaker, tenant_session

    async with get_sessionmaker()() as s:
        tenant_ids = list(await s.scalars(select(Tenant.id)))
    total: dict[str, int] = {}
    for tid in tenant_ids:
        try:
            async with tenant_session(tid) as s:
                for k, v in (await followups(s, tid, now)).items():
                    total[k] = total.get(k, 0) + v
                rescored = await scoring.rescore_open(s, now)  # ERP money, dates (F1.7)
                total["rescored"] = total.get("rescored", 0) + rescored
        except Exception:
            log.exception("enquiry follow-ups for tenant %s failed", tid)
    return total


# People acting on email -----------------------------------------------------------------

async def relabel(session: AsyncSession, row: InboundEmail, label: str, actor: str) -> None:
    from app.models.enquiries import LABELS

    if label not in LABELS:
        from app.core.errors import AppError

        raise AppError("Unknown label")
    if row.original_label is None:
        row.original_label = row.label
    row.label, row.label_source = label, "person"
    row.relabelled_by, row.relabelled_at = actor, datetime.now(UTC)


async def convert(session: AsyncSession, row: InboundEmail, actor: str) -> Enquiry:
    """Other mail a person says is an enquiry (one click, N5 A)."""
    from app.core.errors import AppError

    load_definitions()
    if row.enquiry_id:
        raise AppError("This email is already part of an enquiry")
    if row.stage == "new":
        raise AppError("This email has not been read yet")
    now = datetime.now(UTC)
    await relabel(session, row, "enquiry", actor)
    cfg = await settings_mod.load(session, row.tenant_id)
    tenant = await session.get(Tenant, row.tenant_id)
    ctx = await runtime.start_run(session, row.tenant_id, AGENT_KEY, "convert", str(row.id))
    docs = await _docs(session, row)
    reading = await reader.read(session, ctx, email=_email(row), docs=docs,
                                families=await families.active(session),
                                company=tenant.name if tenant else "the company",
                                today=now.date(), last_try=True)
    reading.data["label"] = "enquiry"
    reasons = ["Moved from Other mail by a person"]
    if reading.source == "rules":
        reasons.append(reading.why_rules or "Not read by AI")
    enq = await _open_enquiry(session, ctx, row, reading, reasons, docs, cfg, now, auto=False)
    row.stage = "done"
    await records.event(session, enq, "converted", actor, email_id=str(row.id))
    await runtime.finish_run(session, ctx, summary={"outcome": "enquiry", "enquiry": enq.ref})
    return enq


async def not_enquiry(session: AsyncSession, enq: Enquiry, label: str, actor: str) -> None:
    """A person says an enquiry is really other mail: it closes and its email moves to
    Other mail with the person's label (kept for teaching the reader, N5 A)."""
    from app.core.errors import AppError

    if label == "enquiry":
        raise AppError("Pick what kind of mail it is")
    if enq.status not in OPEN_STATUSES:
        raise AppError(f"{enq.ref} is {enq.status}")
    now = datetime.now(UTC)
    for row in await session.scalars(select(InboundEmail).where(
            InboundEmail.enquiry_id == enq.id)):
        await relabel(session, row, label, actor)
        row.outcome = "other"
    await close(session, enq, actor, f"Not an enquiry: {label.replace('_', ' ')} mail", now)


async def close(session: AsyncSession, enq: Enquiry, actor: str, reason: str,
                now: datetime | None = None) -> None:
    from app.core.errors import AppError

    now = now or datetime.now(UTC)
    if enq.status not in OPEN_STATUSES:
        raise AppError(f"{enq.ref} is already {enq.status}")
    if not (reason or "").strip():
        raise AppError("Say why it is closed")
    enq.status, enq.closed_at, enq.closed_by, enq.close_reason = (
        "closed", now, actor, reason.strip()[:300])
    await timers.stop_all(session, enq, now, actor)
    await records.event(session, enq, "closed", actor)  # the reason is on the enquiry
    await records.audit(session, enq, "enquiry.closed", actor, reason=enq.close_reason)


async def reopen(session: AsyncSession, enq: Enquiry, actor: str) -> None:
    from app.core.errors import AppError

    if enq.status not in ("closed", "parked"):
        raise AppError(f"{enq.ref} is {enq.status}")
    now = datetime.now(UTC)
    was = enq.status
    enq.status = "open" if enq.owner_id else "new"
    enq.closed_at = enq.closed_by = enq.close_reason = enq.parked_at = None
    enq.waiting_on, enq.waiting_since = "us", None
    if enq.quote_sent_at is None:
        t = await timers.engine.current(session, timers.SUBJECT, enq.id, "quote")
        if t is None:
            await timers.start(session, enq, "quote", now)
        else:
            await timers.resume(session, enq, "quote", now)
    await records.event(session, enq, "reopened", actor, was=was)


async def quote_sent(session: AsyncSession, enq: Enquiry, actor: str) -> str | None:
    from app.core.errors import AppError

    if enq.status not in OPEN_STATUSES:
        raise AppError(f"{enq.ref} is {enq.status}")
    if enq.quote_sent_at:
        raise AppError("The quote is already marked as sent")
    now = datetime.now(UTC)
    t = await timers.engine.current(session, timers.SUBJECT, enq.id, "quote")
    if t is not None and t.running_since is None:
        await timers.resume(session, enq, "quote", now)
    enq.quote_sent_at, enq.quote_sent_by = now, actor
    if enq.status in ("waiting_customer", "parked"):
        enq.status, enq.waiting_on = "open", "us"
    outcome = await timers.stop(session, enq, "quote", now, actor=actor)
    await records.event(session, enq, "quote_sent", actor, timer=outcome)
    await records.audit(session, enq, "enquiry.quote_sent", actor)
    return outcome


async def seen(session: AsyncSession, enq: Enquiry, user: User) -> bool:
    """The owner opened it: their first look is done."""
    if enq.owner_id != user.id or enq.owner_seen_at is not None:
        return False
    now = datetime.now(UTC)
    enq.owner_seen_at = now
    await timers.stop(session, enq, "first_look", now, actor=f"user:{user.id}")
    await records.event(session, enq, "owner_opened", f"user:{user.id}")
    return True


def as_decimal(v: float | None) -> Decimal | None:
    return None if v is None else Decimal(str(round(v, 3)))
