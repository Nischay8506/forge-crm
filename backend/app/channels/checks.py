"""The checks every outgoing message passes, in order, when it is queued and again
just before it goes (a contact can be erased, a kill-switch pressed or a STOP
received in between). Each check is recorded with its result.

  1. recipient   the contact is active and at the account the message is about (or,
                 for staff, the person is an active user)
  2. address     the address comes from the contact at send time; a raw address only
                 when replying to a message from it; never an erased address
  3. controls    an agent's message stops for a kill-switch or a person's takeover
  4. content     a subject and body, the AI marker on AI-written text, no placeholder
                 left in
  5. consent     promotional messages only to contacts who have not asked to stop
  6. timing      quiet hours, Sundays and holidays, and the per-contact limits (L8);
                 a person can send a held message anyway (recorded), never a blocked one

Outcome: ok (it may go now), held (it may go later, at `send_after`) or blocked.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import consent as consent_mod
from app.channels import rules as rules_mod
from app.channels import statuses as st
from app.models import Account, Contact, Message, User
from app.steward.identifiers import normalize_email

MAX_SUBJECT, MAX_BODY = 300, 20000
PLACEHOLDER = re.compile(r"\{\{.*?\}\}|\[(?:name|customer|company|date|amount|insert[^\]]*)\]"
                         r"|<insert[^>]*>|\bTODO\b|XXX", re.IGNORECASE)
PROMOTIONAL = "promotional"


@dataclass
class Verdict:
    outcome: str = "ok"  # ok | held | blocked
    checks: list[dict[str, Any]] = field(default_factory=list)
    reason: str | None = None
    send_after: datetime | None = None
    wait: str | None = None  # quiet_hours | rest_day | day_cap | week_cap
    to_address: str | None = None

    def check(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append({"check": name, "ok": ok, "detail": detail})

    def block(self, name: str, detail: str) -> "Verdict":
        self.check(name, False, detail)
        self.outcome, self.reason = "blocked", detail
        return self


def agent_of(msg: Message) -> str | None:
    """The agent that asked for this message to go, if an agent did."""
    who = msg.sent_by or msg.created_by or ""
    return who.split(":", 1)[1] if who.startswith("agent:") else None


async def lock_contact(session: AsyncSession, contact_id: uuid.UUID) -> None:
    """Held to the end of the transaction, so two messages to one contact cannot both
    pass the limits at the same moment."""
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                          {"k": f"contact-send:{contact_id}"})


async def _promo_sent_since(session: AsyncSession, contact_id: uuid.UUID, since: datetime,
                            exclude: uuid.UUID) -> list[datetime]:
    when = func.coalesce(Message.sent_at, Message.status_at)
    return list(await session.scalars(
        select(when).where(Message.contact_id == contact_id, Message.direction == "out",
                           Message.purpose == PROMOTIONAL, Message.status.in_(st.GONE),
                           Message.id != exclude, when >= since).order_by(when)))


async def _replying_in_window(session: AsyncSession, msg: Message, now: datetime,
                              window: timedelta) -> bool:
    if msg.purpose != "reply" or not msg.in_reply_to_id:
        return False
    original = await session.get(Message, msg.in_reply_to_id)
    return bool(original and original.direction == "in" and original.created_at
                and now - original.created_at <= window)


async def run(session: AsyncSession, msg: Message, now: datetime, *,
              erased: Any = None) -> Verdict:
    """All the checks for `msg` at `now`. `erased` is app.privacy.erasure.mask_for's
    function (or None when nothing was ever erased)."""
    v = Verdict()

    # 1. recipient
    contact = None
    if msg.purpose == "staff":
        user = await session.get(User, msg.user_id) if msg.user_id else None
        if user is None or not user.is_active:
            return v.block("recipient", "This person is no longer an active user")
        v.check("recipient", True, "An active member of staff")
    elif msg.contact_id:
        contact = await session.get(Contact, msg.contact_id)
        if contact is None:
            return v.block("recipient", "The contact no longer exists")
        if contact.status == "erased":
            return v.block("recipient", "This contact's personal data was erased")
        if contact.status == "merged":
            return v.block("recipient", "This contact was merged into another contact; "
                                        "write to that contact instead")
        if contact.status != "active":
            return v.block("recipient", f"This contact is {contact.status}")
        if msg.account_id and contact.account_id != msg.account_id:
            acc = await session.get(Account, msg.account_id)
            return v.block("recipient", "This contact is not at "
                                        f"{acc.name if acc else 'the account it is about'}")
        v.check("recipient", True, "An active contact" + (" at the account" if msg.account_id
                                                          else ""))
    else:
        original = await session.get(Message, msg.in_reply_to_id) if msg.in_reply_to_id \
            else None
        if original is None or original.direction != "in":
            return v.block("recipient", "No contact chosen")
        v.check("recipient", True, "A reply to someone who wrote to us and is not a known "
                                   "contact")

    # 2. address (the current one, not the one when it was written)
    if msg.channel != "email":
        return v.block("address", f"{msg.channel.capitalize()} is not set up yet; only "
                                  "email is (decision L1)")
    if msg.purpose == "staff":
        address = normalize_email(user.email)  # type: ignore[union-attr]
    elif contact is not None:
        address = normalize_email(contact.email)
    else:
        address = normalize_email(original.from_address)  # type: ignore[union-attr]
    if not address:
        return v.block("address", "No valid email address for this contact")
    if erased is not None and erased("email", address) == "[erased]":
        return v.block("address", "This address belongs to someone whose data was erased")
    v.to_address = address
    v.check("address", True, "Email address on file" if contact else "Email address")

    # 3. agent controls (kill-switches and takeovers, checked again just before sending)
    agent_key = agent_of(msg)
    if agent_key:
        from app.agents import controls

        accounts = {a for a in (msg.account_id, contact.account_id if contact else None) if a}
        for ks in await controls.active_switches(session):
            if (ks.scope == "tenant" or (ks.scope == "agent" and ks.agent_key == agent_key)
                    or (ks.scope == "account" and ks.account_id in accounts)):
                return v.block("controls", f"Agents are stopped ({ks.reason})")
        records = [("account", a) for a in accounts]
        if contact is not None:
            records.append(("contact", contact.id))
        if await controls.active_takeovers(session, records):
            return v.block("controls", "A person has taken over this customer from agents")
        v.check("controls", True, "No kill-switch or takeover")
    else:
        v.check("controls", True, "Sent by a person")

    # 4. content
    subject, body = (msg.subject or "").strip(), (msg.body or "").strip()
    if not subject:
        return v.block("content", "An email needs a subject")
    if len(subject) > MAX_SUBJECT or len(body) > MAX_BODY:
        return v.block("content", "The subject or the message is too long")
    if not body:
        return v.block("content", "The message is empty")
    hole = PLACEHOLDER.search(f"{subject}\n{body}")
    if hole:
        return v.block("content", f"Something still needs filling in: {hole.group(0)}")
    if msg.ai_generated and not msg.disclosure:
        return v.block("content", "AI-written text must say so (decision R7)")
    v.check("content", True, "Subject and message present" +
            (", marked as written with Forge's AI assistant" if msg.ai_generated else ""))

    # 5. consent
    if msg.purpose == PROMOTIONAL:
        if contact is None:
            return v.block("consent", "Promotional messages go only to known contacts")
        cs = await consent_mod.state(session, contact.id, msg.channel)
        if not cs.may_promote:
            how = consent_mod.HOW.get(cs.how or "", cs.how)
            return v.block("consent", f"Asked not to get promotional messages ({how}"
                                      f"{f', {cs.at:%d %b %Y}' if cs.at else ''})")
        v.check("consent", True, "Has not asked to stop" if cs.status != "granted"
                else "Agreed to promotional messages")
    else:
        v.check("consent", True, "Not needed: not a promotional message")

    # 6. timing
    if msg.override_by:
        v.check("timing", True, "Sent anyway by a person, past quiet hours and limits")
        return v
    if msg.purpose == "staff":
        v.check("timing", True, "Notifications to staff go at once")
        return v
    rules, _ = await rules_mod.rules(session, msg.tenant_id)
    promotional = msg.purpose == PROMOTIONAL
    quiet = not await _replying_in_window(session, msg, now, rules.reply_window)
    at, why = rules.next_allowed(max(now, msg.send_after or now), promotional=promotional,
                                 quiet=quiet)
    if promotional and contact is not None:
        for _ in range(20):  # the limits, then the hours again, until both agree
            day = await _promo_sent_since(session, contact.id, rules.day_start(at), msg.id)
            week = await _promo_sent_since(session, contact.id, at - timedelta(days=7),
                                           msg.id)
            if rules.per_day == 0 or rules.per_week == 0:
                return v.block("timing", "This company sends no promotional messages "
                                         "(its limit is 0)")
            if len(day) >= rules.per_day:
                at, why = rules.next_allowed(rules.next_day_start(at), promotional=True)
                why = "day_cap"
            elif len(week) >= rules.per_week:
                at = week[len(week) - rules.per_week] + timedelta(days=7)
                at, _w = rules.next_allowed(at, promotional=True)
                why = "week_cap"
            else:
                break
    if at > now:
        v.outcome, v.send_after, v.wait = "held", at, why
        v.reason = rules_mod.describe_wait(why or "", at, rules.tz)
        v.check("timing", False, v.reason)
        return v
    detail = "Inside sending hours"
    if not quiet:
        detail = "A reply to a message the customer just sent: quiet hours do not apply"
    if promotional:
        detail += "; within the promotional limits"
    v.check("timing", True, detail)
    return v
