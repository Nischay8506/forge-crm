"""Consent per contact and channel (decision L9 A).

Promotional email works on opt-out: it may go unless the contact asked to stop
(WhatsApp's own opt-in rule applies when WhatsApp is added). Transactional messages
(replies, order and invoice updates, approvals they asked for) are not affected.

"STOP" or "unsubscribe" on any channel stops promotional messages on every channel
at once ("all"), is confirmed to the customer and recorded. Every promotional email
carries an unsubscribe link (and the List-Unsubscribe header mail apps show as a
button); the link opens a page with one button, so mail scanners that open links
do not unsubscribe anyone, and the one-click header works too.

The latest record for a channel, or for "all", decides. Records are appended, never
changed; consent follows a contact through merges (app/steward/merge.py).
"""

import hashlib
import hmac
import re
import secrets
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import chain
from app.config import get_settings
from app.models import Consent, Contact, Message

GRANTED, WITHDRAWN = "granted", "withdrawn"
CHANNELS = ("email", "whatsapp", "sms")
HOW = {
    "form": "A form the customer filled in", "in_person": "Told us in person",
    "phone": "Told us on the phone", "email": "Told us by email",
    "import": "Imported from an earlier system", "link": "The unsubscribe link",
    "stop_word": "Replied STOP", "person": "Recorded by a person",
}

# A whole message that is one of these (any case, punctuation ignored) asks to stop.
STOP_WORDS = frozenset({
    "stop", "stop all", "stopall", "unsubscribe", "opt out", "optout", "remove me",
    "बंद", "बंद करो", "बंद करें", "रोकें",  # Hindi
    "ఆపండి", "ఆపు",  # Telugu
    "நிறுத்து", "நிறுத்தவும்",  # Tamil
})

CONFIRMATION = {
    "en": ("You are unsubscribed",
           "You will not get promotional messages from {company} any more. Messages about "
           "your orders, invoices and replies to you will still reach you."),
    "hi": ("आपकी सदस्यता समाप्त कर दी गई है",
           "अब आपको {company} से प्रचार संदेश नहीं मिलेंगे। आपके ऑर्डर, बिल और आपके "
           "सवालों के जवाब पहले की तरह मिलते रहेंगे।"),
    "te": ("మీ చందా రద్దు చేయబడింది",
           "ఇకపై మీకు {company} నుండి ప్రచార సందేశాలు రావు. మీ ఆర్డర్లు, బిల్లులు మరియు మీ "
           "ప్రశ్నలకు సమాధానాలు యథావిధిగా వస్తాయి."),
}


@dataclass(frozen=True)
class State:
    channel: str
    status: str  # granted | withdrawn | unknown (opt-out: may receive)
    how: str | None = None
    at: datetime | None = None
    record_id: uuid.UUID | None = None

    @property
    def may_promote(self) -> bool:
        return self.status != WITHDRAWN


def is_stop(text: str | None) -> bool:
    if not text:
        return False
    first = text.strip().splitlines()[0] if text.strip() else ""
    cleaned = "".join(ch for ch in first if not unicodedata.category(ch).startswith("P"))
    return " ".join(cleaned.lower().split()) in STOP_WORDS


async def _rows(session: AsyncSession, contact_id: uuid.UUID) -> list[Consent]:
    return list(await session.scalars(select(Consent).where(Consent.contact_id == contact_id)
                                      .order_by(Consent.recorded_at, Consent.id)))


async def states(session: AsyncSession, contact_id: uuid.UUID) -> dict[str, State]:
    """Each channel's state: its latest record, or a later one for "all"."""
    latest: dict[str, Consent] = {}
    for r in await _rows(session, contact_id):
        latest[r.channel] = r
    out = {}
    for ch in CHANNELS:
        cands = [r for r in (latest.get(ch), latest.get("all")) if r is not None]
        if not cands:
            out[ch] = State(ch, "unknown")
            continue
        r = max(cands, key=lambda c: c.recorded_at)
        out[ch] = State(ch, r.status, r.how, r.recorded_at, r.id)
    return out


async def state(session: AsyncSession, contact_id: uuid.UUID, channel: str) -> State:
    return (await states(session, contact_id))[channel]


async def history(session: AsyncSession, contact_id: uuid.UUID) -> list[Consent]:
    return list(reversed(await _rows(session, contact_id)))


async def record(session: AsyncSession, tenant_id: uuid.UUID, contact: Contact, *,
                 channel: str, status: str, how: str, actor: str,
                 source_ref: str | None = None, note: str | None = None) -> Consent:
    if channel not in (*CHANNELS, "all") or status not in (GRANTED, WITHDRAWN) \
            or how not in HOW:
        raise ValueError("Unknown consent channel, status or source")
    row = Consent(tenant_id=tenant_id, contact_id=contact.id, channel=channel, status=status,
                  how=how, source_ref=source_ref, note=(note or "").strip() or None,
                  recorded_by=actor)
    session.add(row)
    await session.flush()
    await chain.append(session, tenant_id, f"consent.{status}", actor=actor,
                       record_type="contact", record_id=contact.id,
                       account_id=contact.account_id, data={
                           "consent_id": row.id, "channel": channel, "status": status,
                           "how": how, "source_ref": source_ref, "note": row.note})
    return row


# The unsubscribe link -------------------------------------------------------------
# token = <tenant id hex><message id hex>.<random>; only a hash of the random part is
# stored on the message, so the link works without any key and a leaked database does
# not give working links.

def new_link_token(tenant_id: uuid.UUID, message_id: uuid.UUID) -> tuple[str, str]:
    """(token for the email, hash to store on the message)."""
    secret = secrets.token_urlsafe(18)
    return (f"{tenant_id.hex}{message_id.hex}.{secret}",
            hashlib.sha256(secret.encode()).hexdigest())


def link_url(token: str) -> str:
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/api/v1/public/unsubscribe/{token}"


_TOKEN = re.compile(r"^([0-9a-f]{32})([0-9a-f]{32})\.([A-Za-z0-9_-]{10,64})$")


def parse_link_token(token: str) -> tuple[uuid.UUID, uuid.UUID, str] | None:
    m = _TOKEN.match(token or "")
    if not m:
        return None
    return uuid.UUID(m.group(1)), uuid.UUID(m.group(2)), m.group(3)


def link_token_ok(msg: Message | None, secret: str) -> bool:
    if msg is None or not msg.link_token_hash:
        return False
    return hmac.compare_digest(msg.link_token_hash,
                               hashlib.sha256(secret.encode()).hexdigest())


async def withdraw_everywhere(session: AsyncSession, tenant_id: uuid.UUID, contact: Contact,
                              *, how: str, actor: str, source_ref: str | None = None
                              ) -> tuple[Consent | None, bool]:
    """STOP or the unsubscribe link: promotional messages stop on every channel.
    Returns (record, newly) where newly is False if they had already stopped."""
    st = await states(session, contact.id)
    if all(s.status == WITHDRAWN for s in st.values()):
        return None, False
    row = await record(session, tenant_id, contact, channel="all", status=WITHDRAWN, how=how,
                       actor=actor, source_ref=source_ref)
    return row, True


async def contacts_for_address(session: AsyncSession, address: str) -> list[Contact]:
    """Active contacts whose email is exactly this address (any case)."""
    return list(await session.scalars(select(Contact).where(
        Contact.status == "active", Contact.email.is_not(None),
        or_(Contact.email == address, Contact.email.ilike(address.replace("%", r"\%")
                                                          .replace("_", r"\_"))))))
