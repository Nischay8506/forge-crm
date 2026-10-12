"""The one sending path (Step 6, F12.5).

    draft()      a message that has not been asked to go yet
    queue()      ask for it to go: the checks run (checks.py) and it is queued, held
                 until an allowed time, waiting for a language review, or blocked
    deliver_due  the worker, every 10 seconds: each due message is checked again and
                 handed to the adapter, at most once:
                   1. lock it, check it, mark it "sending" and commit;
                   2. call the adapter outside any transaction;
                   3. record what the adapter said.
                 When the adapter cannot say whether the message went (the connection
                 dropped mid-send), it is marked failed "check before sending again"
                 and never retried on its own; a message left in "sending" (the worker
                 died) is marked the same way after a few minutes.
    receive()    an incoming message
    send_anyway(), cancel(), resend()   a person's actions, each recorded

Every change of status is a message event; sending, blocking, bouncing and a
person's override also go into the audit trail.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import chain
from app.audit.redact import EMAIL, PHONE_IN, PHONE_INTL
from app.channels import accounts, checks
from app.channels import consent as consent_mod
from app.channels import rules as rules_mod
from app.channels import statuses as st
from app.channels.adapters.base import (
    BOUNCED,
    FAILED,
    RETRY,
    SENT,
    OutgoingEmail,
    SendResult,
)
from app.config import get_settings
from app.core.errors import AppError
from app.language.script import detect
from app.models import Contact, Message, MessageEvent, Tenant

log = logging.getLogger(__name__)

SYSTEM = "system:channels"
MAX_TRIES = 5
BACKOFF_MINUTES = (1, 5, 15, 60)
ACCOUNT_WAIT = timedelta(minutes=15)
LOST = ("The connection dropped while sending, so Forge cannot tell whether the email "
        "went. Check with the customer before sending it again.")

# The AI marker (R7), in the message's language. Native speakers check these in the
# language review like any other text.
AGENT_DISCLOSURE = {"en": "Sent by our AI assistant",
                    "hi": "यह संदेश हमारे AI सहायक ने भेजा है",
                    "te": "ఈ సందేశాన్ని మా AI సహాయకుడు పంపారు"}
DRAFTED_DISCLOSURE = {"en": "Written with help from our AI assistant",
                      "hi": "यह संदेश हमारे AI सहायक की मदद से लिखा गया है",
                      "te": "ఈ సందేశం మా AI సహాయకుడి సహాయంతో రాయబడింది"}
UNSUBSCRIBE_LINE = {"en": "To stop getting promotional emails from us, open: {link}",
                    "hi": "हमारे प्रचार ईमेल बंद करने के लिए यह लिंक खोलें: {link}",
                    "te": "మా ప్రచార ఈమెయిళ్లు ఆపడానికి ఈ లింక్ తెరవండి: {link}"}


def disclosure_for(language: str | None, *, agent: bool) -> str:
    table = AGENT_DISCLOSURE if agent else DRAFTED_DISCLOSURE
    return table.get(language or "en", table["en"])


def clean(reason: str | None) -> str | None:
    """A provider's reason without addresses or phone numbers."""
    if not reason:
        return None
    for rx in (EMAIL, PHONE_IN, PHONE_INTL):
        reason = rx.sub("[address]", reason)
    return reason[:500]


async def event(session: AsyncSession, msg: Message, status: str, source: str, *,
                actor: str | None = None, applied: bool = True,
                detail: dict[str, Any] | None = None, at: datetime | None = None
                ) -> MessageEvent:
    ev = MessageEvent(tenant_id=msg.tenant_id, message_id=msg.id, status=status, source=source,
                      actor=actor, applied=applied, detail=detail or {},
                      at=at or datetime.now(UTC))
    session.add(ev)
    await session.flush()
    return ev


def _set(msg: Message, status: str, now: datetime, reason: str | None = None) -> None:
    msg.status, msg.status_at, msg.reason = status, now, reason


def _kw(msg: Message, actor: str) -> dict[str, Any]:
    return {"actor": actor, "run_id": msg.run_id, "record_type": "message",
            "record_id": msg.id, "account_id": msg.account_id, "contact_id": msg.contact_id}


async def _audit(session: AsyncSession, msg: Message, kind: str, actor: str,
                 **data: Any) -> None:
    agent = checks.agent_of(msg)
    await chain.append(session, msg.tenant_id, kind, agent_key=agent, **_kw(msg, actor), data={
        "message_id": msg.id, "channel": msg.channel, "purpose": msg.purpose,
        "status": msg.status, **data})


# Writing and queueing ---------------------------------------------------------------

async def draft(session: AsyncSession, tenant_id: uuid.UUID, *, purpose: str, subject: str,
                body: str, created_by: str, channel: str = "email",
                contact_id: uuid.UUID | None = None, account_id: uuid.UUID | None = None,
                user_id: uuid.UUID | None = None, language: str | None = None,
                ai_generated: bool = False, disclosure: str | None = None,
                run_id: uuid.UUID | None = None, feature: str | None = None,
                in_reply_to_id: uuid.UUID | None = None, idempotency_key: str | None = None,
                simulator_only: bool = False) -> Message:
    """A message not yet asked to go. With an idempotency key, asking twice gives the
    first message back."""
    if purpose not in ("promotional", "transactional", "reply", "staff"):
        raise AppError("Unknown message purpose")
    if idempotency_key:
        existing = await session.scalar(select(Message).where(
            Message.idempotency_key == idempotency_key))
        if existing is not None:
            return existing
    if contact_id and not account_id:
        c = await session.get(Contact, contact_id)
        account_id = c.account_id if c else None
    found = detect(body)
    now = datetime.now(UTC)
    # A reply in an enquiry's conversation belongs to that enquiry (M1), and a reply to
    # something written in the Test inbox or the Test mailbox stays in the Test inbox.
    original = (await session.execute(select(Message.enquiry_id, Message.provider).where(
        Message.id == in_reply_to_id))).first() if in_reply_to_id else None
    enquiry_id = original.enquiry_id if original else None
    if original is not None and original.provider in ("simulator", "test"):
        simulator_only = True
    msg = Message(tenant_id=tenant_id, channel=channel, direction="out", status=st.DRAFT,
                  purpose=purpose, subject=subject, body=body, created_by=created_by,
                  contact_id=contact_id, account_id=account_id, user_id=user_id,
                  language=language or found.language, script=found.script,
                  ai_generated=ai_generated, disclosure=disclosure, run_id=run_id,
                  feature=feature, in_reply_to_id=in_reply_to_id,
                  idempotency_key=idempotency_key, status_at=now, attempts=0, checks=[],
                  provider="simulator" if simulator_only else None, enquiry_id=enquiry_id)
    session.add(msg)
    await session.flush()
    await event(session, msg, st.DRAFT, "person" if created_by.startswith("user:") else "forge",
                actor=created_by)
    return msg


async def queue(session: AsyncSession, msg: Message, *, actor: str,
                now: datetime | None = None, review: bool = True,
                writing_problems: list[str] | None = None) -> Message:
    """Ask for a draft to go. `actor` is who asks (a person, or "agent:<key>").
    `writing_problems`: what the AI writer could not fix in its one rewrite (L7); an
    agent's message with any waits for a person (a person sending sees them first)."""
    from app.language import review as review_mod
    from app.privacy.erasure import mask_for

    if msg.direction != "out" or msg.status not in (st.DRAFT, st.REVIEW):
        raise AppError(f"This message is {st.LABELS.get(msg.status, msg.status).lower()}, "
                       "so it cannot be sent from here")
    now = now or datetime.now(UTC)
    msg.sent_by = actor
    if msg.body:
        found = detect(msg.body)
        msg.script = found.script
        msg.language = msg.language or found.language
    if msg.contact_id:
        await checks.lock_contact(session, msg.contact_id)
    v = await checks.run(session, msg, now, erased=await mask_for(session, msg.tenant_id))
    msg.checks = v.checks
    msg.to_address = v.to_address or msg.to_address
    if v.outcome == "blocked":
        _set(msg, st.BLOCKED, now, v.reason)
        await event(session, msg, st.BLOCKED, "forge", actor=actor,
                    detail={"check": v.checks[-1]["check"]})
        await _audit(session, msg, "message.blocked", actor, reason=v.reason)
        return msg
    if review and msg.ai_generated and msg.purpose != "staff" and msg.status == st.DRAFT:
        agent = checks.agent_of(msg) is not None
        r = await review_mod.maybe_sample(session, msg, blocking=agent,
                                          problems=writing_problems if agent else None)
        if r is not None and r.blocking:
            _set(msg, st.REVIEW, now, "Waiting for a person to check the writing")
            await event(session, msg, st.REVIEW, "forge", actor=actor,
                        detail={"review_id": str(r.id), "why": r.why})
            await _audit(session, msg, "message.queued", actor, review=True)
            return msg
    if v.outcome == "held":
        _set(msg, st.HELD, now, v.reason)
        msg.send_after = v.send_after
        await event(session, msg, st.HELD, "forge", actor=actor,
                    detail={"wait": v.wait, "until": v.send_after.isoformat()
                            if v.send_after else None})
    else:
        _set(msg, st.QUEUED, now)
        msg.send_after = now
        await event(session, msg, st.QUEUED, "forge", actor=actor)
    await _audit(session, msg, "message.queued", actor)
    return msg


# Delivering -------------------------------------------------------------------------

def compose_text(msg: Message, link: str | None) -> str:
    lang = msg.language if msg.language in UNSUBSCRIBE_LINE else "en"
    footer = []
    if msg.ai_generated and msg.disclosure:
        footer.append(msg.disclosure)
    if link:
        footer.append(UNSUBSCRIBE_LINE[lang].format(link=link))
    text = (msg.body or "").rstrip()
    return text + ("\n\n-- \n" + "\n".join(footer) if footer else "") + "\n"


async def _prepare(tenant_id: uuid.UUID, message_id: uuid.UUID, now: datetime
                   ) -> tuple[str, Any, OutgoingEmail | None]:
    """Phase 1: (outcome, adapter, email). The message is "sending" when an email comes
    back, and that has been committed."""
    from app.db import tenant_session
    from app.privacy.erasure import mask_for

    async with tenant_session(tenant_id) as s:
        msg = await s.scalar(select(Message).where(
            Message.id == message_id, Message.status.in_(st.WAITING),
            Message.send_after <= now).with_for_update(skip_locked=True))
        if msg is None:
            return "skipped", None, None
        if msg.contact_id:
            await checks.lock_contact(s, msg.contact_id)
        v = await checks.run(s, msg, now, erased=await mask_for(s, tenant_id))
        msg.checks = v.checks
        actor = SYSTEM
        if v.outcome == "blocked":
            _set(msg, st.BLOCKED, now, v.reason)
            await event(s, msg, st.BLOCKED, "forge", actor=actor,
                        detail={"check": v.checks[-1]["check"], "at_delivery": True})
            await _audit(s, msg, "message.blocked", actor, reason=v.reason, at_delivery=True)
            return "blocked", None, None
        if v.outcome == "held":
            if msg.status != st.HELD or msg.send_after != v.send_after:
                _set(msg, st.HELD, now, v.reason)
                msg.send_after = v.send_after
                await event(s, msg, st.HELD, "forge", actor=actor, detail={
                    "wait": v.wait, "until": v.send_after.isoformat() if v.send_after else None})
            return "held", None, None
        cfg = await rules_mod.load(s, tenant_id)
        adapter, sender = await accounts.sender(s, cfg["email_mode"],
                                                force_simulator=msg.provider == "simulator")
        if adapter is None:
            r, _ = await rules_mod.rules(s, tenant_id)
            until = now + ACCOUNT_WAIT
            reason = rules_mod.describe_wait("account", until, r.tz)
            if msg.status != st.HELD or msg.reason != reason:
                _set(msg, st.HELD, now, reason)
                msg.send_after = until
                await event(s, msg, st.HELD, "forge", actor=actor, detail={"wait": "account"})
            else:
                msg.send_after = until
            return "held", None, None
        msg.to_address = v.to_address
        msg.from_address = sender["from_address"]
        msg.provider = adapter.name
        link = None
        headers: dict[str, str] = {}
        if msg.purpose == "promotional":
            token, msg.link_token_hash = consent_mod.new_link_token(tenant_id, msg.id)
            link = consent_mod.link_url(token)
            headers["List-Unsubscribe"] = f"<{link}>"
            headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        if msg.in_reply_to_id:
            original = await s.get(Message, msg.in_reply_to_id)
            if original is not None and original.provider_ref:
                headers["In-Reply-To"] = original.provider_ref
                headers["References"] = original.provider_ref
        mail = OutgoingEmail(message_id=msg.id, to=v.to_address or "",
                             from_address=sender["from_address"],
                             from_name=sender.get("from_name"), reply_to=sender.get("reply_to"),
                             subject=(msg.subject or "").strip(), text=compose_text(msg, link),
                             headers=headers)
        msg.attempts += 1
        _set(msg, st.SENDING, now)
        await event(s, msg, st.SENDING, "forge", actor=actor,
                    detail={"attempt": msg.attempts, "provider": adapter.name})
        return "sending", adapter, mail


async def _record(tenant_id: uuid.UUID, message_id: uuid.UUID, result: SendResult,
                  source: str) -> str:
    """Phase 3: what the adapter said."""
    from app.db import tenant_session

    now = datetime.now(UTC)
    async with tenant_session(tenant_id) as s:
        msg = await s.get(Message, message_id, with_for_update=True, populate_existing=True)
        if msg is None:
            return "gone"
        reason = clean(result.reason)
        detail = {"outcome": result.outcome, "reason": reason, "attempt": msg.attempts}
        late = msg.status == st.FAILED and msg.reason == LOST
        if msg.status != st.SENDING and not (late and result.outcome == SENT):
            # Marked lost by the worker meanwhile, and the adapter did not send it.
            await event(s, msg, result.outcome, source, applied=False, detail=detail)
            return "late"
        if result.account_problem:
            await accounts.note_problem(s, reason or "The mail server refused the account")
        if result.outcome == SENT:
            _set(msg, st.SENT, now)
            msg.sent_at, msg.provider_ref = now, result.provider_ref
            if late:
                detail["after_marked_lost"] = True
            await event(s, msg, st.SENT, source, detail=detail)
            await _audit(s, msg, "message.sent", SYSTEM, provider=msg.provider,
                         attempt=msg.attempts)
            if msg.enquiry_id:
                from app.enquiries.intake import on_message_sent

                await on_message_sent(s, msg, now)
            return "sent"
        if result.outcome == RETRY or (result.outcome == FAILED and result.account_problem):
            if msg.attempts < MAX_TRIES:
                wait = BACKOFF_MINUTES[min(msg.attempts, len(BACKOFF_MINUTES)) - 1]
                r, _ = await rules_mod.rules(s, tenant_id)
                msg.send_after = now + timedelta(minutes=wait)
                why = "account" if result.account_problem else "retry"
                _set(msg, st.QUEUED, now, rules_mod.describe_wait(why, msg.send_after, r.tz))
                await event(s, msg, st.QUEUED, source, detail={**detail, "retry_in_min": wait})
                return "retry"
            _set(msg, st.FAILED, now, f"{reason or 'It could not be sent'}. Tried "
                                      f"{msg.attempts} times.")
        elif result.outcome == BOUNCED:
            _set(msg, st.BOUNCED, now, reason or "The mail server refused this address")
        elif result.outcome == FAILED:
            _set(msg, st.FAILED, now, reason or "The mail server refused the email")
        else:
            _set(msg, st.FAILED, now, LOST)
        await event(s, msg, msg.status, source, detail=detail)
        await _audit(s, msg, f"message.{msg.status}", SYSTEM, reason=msg.reason,
                     attempt=msg.attempts)
        return msg.status


async def deliver_one(tenant_id: uuid.UUID, message_id: uuid.UUID,
                      now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    outcome, adapter, mail = await _prepare(tenant_id, message_id, now)
    if outcome != "sending":
        return outcome
    try:
        result = await adapter.send(mail)
    except Exception as exc:  # noqa: BLE001 - we cannot tell whether it went
        log.exception("adapter %s failed for message %s", adapter.name, message_id)
        result = SendResult("unknown", reason=f"Unexpected error ({type(exc).__name__})")
    return await _record(tenant_id, message_id, result,
                         "simulator" if adapter.name == "simulator" else "provider")


async def _housekeeping(tenant_id: uuid.UUID, now: datetime) -> list[uuid.UUID]:
    """Lost "sending" messages, the email account's automatic check, and the due list."""
    from app.db import tenant_session

    stuck_after = timedelta(minutes=get_settings().channels_stuck_sending_minutes)
    async with tenant_session(tenant_id) as s:
        for msg in await s.scalars(select(Message).where(
                Message.status == st.SENDING, Message.status_at < now - stuck_after)
                .with_for_update(skip_locked=True)):
            _set(msg, st.FAILED, now, LOST)
            await event(s, msg, st.FAILED, "forge", actor=SYSTEM, detail={"stuck": True})
            await _audit(s, msg, "message.failed", SYSTEM, reason=LOST, stuck=True)
        cfg = await rules_mod.load(s, tenant_id)
        a = await accounts.get(s)
        if (cfg["email_mode"] == "live" and a is not None and a.status == "active"
                and a.last_check_ok is not True
                and (a.last_check_at is None or a.last_check_at < now - ACCOUNT_WAIT)):
            await accounts.check(s, a, SYSTEM)
        return list(await s.scalars(select(Message.id).where(
            Message.status.in_(st.WAITING), Message.send_after <= now)
            .order_by(Message.send_after).limit(50)))


async def deliver_due(tenant_id: uuid.UUID, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(UTC)
    counts: dict[str, int] = {}
    for mid in await _housekeeping(tenant_id, now):
        out = await deliver_one(tenant_id, mid, now)
        counts[out] = counts.get(out, 0) + 1
    return counts


async def tick_all(now: datetime | None = None) -> dict[str, int]:
    """The worker's job across companies (every 10 seconds)."""
    from app.db import get_sessionmaker

    async with get_sessionmaker()() as s:
        tenant_ids = list(await s.scalars(select(Tenant.id)))
    total: dict[str, int] = {}
    for tid in tenant_ids:
        try:
            for k, v in (await deliver_due(tid, now)).items():
                total[k] = total.get(k, 0) + v
        except Exception:  # one company's failure must not stop the others
            log.exception("channels tick for tenant %s failed", tid)
    return total


# Receipts and incoming messages --------------------------------------------------------

async def record_receipt(session: AsyncSession, msg: Message, status: str, *, source: str,
                         reason: str | None = None, at: datetime | None = None,
                         actor: str | None = None) -> bool:
    """A provider's (or the simulator's) word on a sent message. Never moves it back."""
    if status not in (st.SENT, st.DELIVERED, st.READ, st.FAILED, st.BOUNCED):
        raise AppError("Unknown receipt")
    at = at or datetime.now(UTC)
    applied = st.receipt_applies(msg.status, status)
    reason = clean(reason)
    await event(session, msg, status, source, actor=actor, applied=applied,
                detail={"reason": reason} if reason else {}, at=at)
    if applied:
        _set(msg, status, at, reason if status in (st.FAILED, st.BOUNCED) else None)
        if status in (st.BOUNCED, st.FAILED):
            await _audit(session, msg, f"message.{status}", actor or SYSTEM, reason=reason,
                         receipt=True)
    return applied


async def receive(session: AsyncSession, tenant_id: uuid.UUID, *, from_address: str,
                  subject: str | None, body: str, provider: str, provider_ref: str,
                  in_reply_to_ref: str | None = None, source: str = "provider",
                  channel: str = "email", at: datetime | None = None) -> Message:
    """An incoming message. It is matched to a contact only when its address belongs to
    exactly one contact, or it answers a message we sent to that address. STOP asks
    for promotional messages to stop everywhere; the customer gets a confirmation."""
    from app.steward.identifiers import normalize_email

    existing = await session.scalar(select(Message).where(
        Message.provider == provider, Message.provider_ref == provider_ref))
    if existing is not None:
        return existing
    at = at or datetime.now(UTC)
    address = normalize_email(from_address) or from_address.strip().lower()
    original = await session.scalar(select(Message).where(
        Message.provider_ref == in_reply_to_ref, Message.direction == "out")) \
        if in_reply_to_ref else None
    contact, why = None, "no contact has this address"
    if original is not None and original.contact_id and original.to_address == address:
        contact, why = await session.get(Contact, original.contact_id), "answers our message"
    else:
        found = await consent_mod.contacts_for_address(session, address)
        if len(found) == 1:
            contact, why = found[0], "one contact has this address"
        elif len(found) > 1:
            why = f"{len(found)} contacts have this address; a person picks"
    if contact is not None and contact.status != "active":
        contact, why = None, "the contact is no longer active"
    found_lang = detect(body)
    msg = Message(tenant_id=tenant_id, channel=channel, direction="in", status=st.RECEIVED,
                  subject=(subject or "")[:300] or None, body=body, created_by=f"{source}:in",
                  contact_id=contact.id if contact else None,
                  account_id=contact.account_id if contact else None,
                  from_address=address, language=found_lang.language, script=found_lang.script,
                  provider=provider, provider_ref=provider_ref, status_at=at, attempts=0,
                  checks=[], in_reply_to_id=original.id if original else None, created_at=at)
    session.add(msg)
    await session.flush()
    stop = consent_mod.is_stop(body)
    await event(session, msg, st.RECEIVED, source, at=at, detail={
        "matched": why, "language": found_lang.language, "sure": found_lang.sure,
        "stop": stop})
    await chain.append(session, tenant_id, "message.received", **_kw(msg, f"{source}:in"),
                       data={"message_id": msg.id, "channel": channel, "matched": why,
                             "stop": stop})
    if stop and contact is not None:
        await handle_stop(session, tenant_id, contact, msg)
    return msg


async def handle_stop(session: AsyncSession, tenant_id: uuid.UUID, contact: Contact,
                      inbound: Message | None, *, how: str = "stop_word") -> bool:
    """Stop promotional messages everywhere and confirm it to the customer once."""
    _, newly = await consent_mod.withdraw_everywhere(
        session, tenant_id, contact, how=how, actor=SYSTEM,
        source_ref=f"message:{inbound.id}" if inbound else None)
    if not newly:
        return False
    tenant = await session.get(Tenant, tenant_id)
    lang = (inbound.language if inbound else None) or contact.preferred_language or "en"
    subject, body = consent_mod.CONFIRMATION.get(lang, consent_mod.CONFIRMATION["en"])
    reply = await draft(session, tenant_id, purpose="reply" if inbound else "transactional",
                        subject=subject, body=body.format(company=tenant.name if tenant else
                                                          "us"),
                        created_by=SYSTEM, contact_id=contact.id, language=lang,
                        feature="consent.confirm", in_reply_to_id=inbound.id if inbound else None,
                        idempotency_key=f"stop-confirm:{inbound.id if inbound else contact.id}:"
                                        f"{datetime.now(UTC):%Y%m%d%H%M}",
                        simulator_only=bool(inbound and inbound.provider == "simulator"))
    if reply.status == st.DRAFT:
        await queue(session, reply, actor=SYSTEM)
    return True


# A person's actions ---------------------------------------------------------------------

async def send_anyway(session: AsyncSession, msg: Message, actor: str, why: str) -> Message:
    """Past quiet hours and the limits, never past a block (consent, a stop, an erased
    contact). The message is checked again before it goes."""
    if msg.status != st.HELD:
        raise AppError("Only a held message can be sent anyway")
    if not why or len(why.strip()) < 5:
        raise AppError("Say why it should go now")
    now = datetime.now(UTC)
    msg.override_by = actor
    msg.send_after = now
    _set(msg, st.QUEUED, now)
    await event(session, msg, st.QUEUED, "person", actor=actor, detail={"sent_anyway": True})
    await _audit(session, msg, "message.sent_anyway", actor, why=why.strip())
    return msg


async def cancel(session: AsyncSession, msg: Message, actor: str) -> Message:
    from app.language import review as review_mod

    if msg.direction != "out" or msg.status not in st.OPEN:
        raise AppError("This message can no longer be cancelled")
    now = datetime.now(UTC)
    _set(msg, st.CANCELLED, now)
    await review_mod.close_for(session, msg)
    await event(session, msg, st.CANCELLED, "person" if actor.startswith("user:") else "forge",
                actor=actor)
    await _audit(session, msg, "message.cancelled", actor)
    return msg


async def resend(session: AsyncSession, msg: Message, actor: str) -> Message:
    """A failed message, sent again after a person checked it did not arrive."""
    if msg.direction != "out" or msg.status != st.FAILED:
        raise AppError("Only a failed message can be sent again")
    _set(msg, st.DRAFT, datetime.now(UTC))
    msg.override_by = None
    await event(session, msg, st.DRAFT, "person", actor=actor, detail={"resend": True})
    await _audit(session, msg, "message.resent", actor)
    return await queue(session, msg, actor=actor, review=False)
