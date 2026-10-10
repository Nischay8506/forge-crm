"""Messages, the Test inbox, consent, channel and language settings (Step 6, F12.5).

Every message is sent through app/channels/send.py; these endpoints only ask for it.
The email password goes in and never comes out (its last four characters do).
"""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.audit import _message, _names
from app.audit import chain
from app.channels import accounts, rules, send, simulate
from app.channels import consent as consent_mod
from app.channels import statuses as st
from app.channels.adapters.simulator import BOUNCE, LOST, OUTAGE
from app.config import get_settings
from app.core import secrets
from app.core.deps import CurrentUser, require_permission
from app.core.errors import AppError, NotFound, ValidationFailed
from app.db import get_db
from app.language import LANGUAGES, WRITING, glossary, review, speech, tone, writer
from app.language.script import detect
from app.models import Contact, LanguageReview, Message, MessageEvent, User

router = APIRouter(tags=["channels"])

READ = require_permission("accounts.read")
SEND = require_permission("messages.send")
MANAGE = require_permission("channels.manage")


def _actor(u: CurrentUser) -> str:
    return f"user:{u.user.id}"


async def _view(db: AsyncSession, m: Message) -> dict[str, Any]:
    out = _message(m, *(await _names(db, m)))
    out["status_label"] = st.LABELS.get(m.status, m.status)
    if m.user_id:
        u = await db.get(User, m.user_id)
        out["user_name"] = u.full_name if u else None
    return out


async def _msg(db: AsyncSession, message_id: uuid.UUID) -> Message:
    m = await db.get(Message, message_id)
    if m is None:
        raise NotFound("Message not found")
    return m


# Messages ---------------------------------------------------------------------------

@router.get("/messages")
async def list_messages(
    account_id: uuid.UUID | None = None, contact_id: uuid.UUID | None = None,
    status: str | None = None, direction: Literal["in", "out"] | None = None,
    audience: Literal["customers", "staff", "all"] = "customers",
    test_inbox: bool | None = None, before: datetime | None = None,
    limit: int = Query(50, le=200),
    _: CurrentUser = Depends(READ), db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Messages to or from customers (or staff notifications), newest first."""
    stmt = select(Message)
    if account_id:
        stmt = stmt.where(Message.account_id == account_id)
    if contact_id:
        stmt = stmt.where(Message.contact_id == contact_id)
    if status:
        stmt = stmt.where(Message.status.in_(status.split(",")))
    if direction:
        stmt = stmt.where(Message.direction == direction)
    if audience == "customers":
        stmt = stmt.where(or_(Message.purpose.is_(None), Message.purpose != "staff"))
    elif audience == "staff":
        stmt = stmt.where(Message.purpose == "staff")
    if test_inbox is not None:
        sim = Message.provider == "simulator"
        stmt = stmt.where(sim if test_inbox else or_(Message.provider.is_(None), ~sim))
    if before:
        stmt = stmt.where(Message.created_at < before)
    rows = list(await db.scalars(stmt.order_by(Message.created_at.desc()).limit(limit)))
    return [await _view(db, m) for m in rows]


@router.get("/messages/{message_id}")
async def get_message(message_id: uuid.UUID, _: CurrentUser = Depends(READ),
                      db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    m = await _msg(db, message_id)
    out = await _view(db, m)
    out["events"] = [{"status": e.status, "label": st.LABELS.get(e.status, e.status),
                      "source": e.source, "applied": e.applied, "actor": e.actor,
                      "detail": e.detail, "at": e.at}
                     for e in await db.scalars(select(MessageEvent).where(
                         MessageEvent.message_id == m.id).order_by(MessageEvent.id))]
    out["reviews"] = [_review(r) for r in await db.scalars(select(LanguageReview).where(
        LanguageReview.message_id == m.id).order_by(LanguageReview.created_at))]
    thread = []
    if m.in_reply_to_id:
        orig = await db.get(Message, m.in_reply_to_id)
        if orig is not None:
            thread.append(orig)
    thread += list(await db.scalars(select(Message).where(Message.in_reply_to_id == m.id)
                                    .order_by(Message.created_at)))
    out["thread"] = [{"id": str(t.id), "direction": t.direction, "status": t.status,
                      "subject": t.subject, "created_at": t.created_at} for t in thread]
    out["language_name"] = LANGUAGES[m.language].name if m.language in LANGUAGES else None
    return out


class ComposeIn(BaseModel):
    contact_id: uuid.UUID | None = None
    in_reply_to_id: uuid.UUID | None = None
    purpose: Literal["promotional", "transactional", "reply"] = "transactional"
    subject: str = Field(default="", max_length=300)
    body: str = Field(default="", max_length=20000)
    language: str | None = Field(default=None, max_length=10)
    send: bool = False


async def _reply_target(db: AsyncSession, body: ComposeIn) -> Message | None:
    if not body.in_reply_to_id:
        return None
    orig = await db.get(Message, body.in_reply_to_id)
    if orig is None or orig.direction != "in":
        raise AppError("You can only reply to a message a customer sent")
    return orig


@router.post("/messages", status_code=201)
async def compose(body: ComposeIn, current: CurrentUser = Depends(SEND),
                  db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """A message written by a person: kept as a draft, or sent at once (`send`)."""
    orig = await _reply_target(db, body)
    contact_id = body.contact_id or (orig.contact_id if orig else None)
    if contact_id is None and orig is None:
        raise AppError("Choose who the message is for")
    msg = await send.draft(db, current.tenant_id, purpose=body.purpose, subject=body.subject,
                           body=body.body, created_by=_actor(current), contact_id=contact_id,
                           language=body.language, in_reply_to_id=orig.id if orig else None,
                           feature="compose")
    if body.send:
        await send.queue(db, msg, actor=_actor(current))
    return await _view(db, msg)


class WriteIn(BaseModel):
    contact_id: uuid.UUID | None = None
    in_reply_to_id: uuid.UUID | None = None
    purpose: Literal["promotional", "transactional", "reply"] = "transactional"
    language: str = Field(max_length=10)
    brief: str = Field(min_length=3, max_length=2000)


@router.post("/messages/write", status_code=201)
async def write_for_me(body: WriteIn, current: CurrentUser = Depends(SEND),
                       db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """"Draft it for me": the Writing Assistant writes it natively in the language
    (L5 A). It comes back as a draft for the person to read, change and send, with any
    problem its checks found."""
    orig = await _reply_target(db, ComposeIn(in_reply_to_id=body.in_reply_to_id))
    contact = await db.get(Contact, body.contact_id or (orig.contact_id if orig else None)) \
        if (body.contact_id or (orig and orig.contact_id)) else None
    if contact is None and orig is None:
        raise AppError("Choose who the message is for")
    d = await writer.write(db, current.tenant_id, language=body.language,
                           purpose=body.purpose, brief=body.brief, contact=contact,
                           in_reply_to=orig, sender_name=current.user.full_name,
                           trigger="compose", trigger_ref=_actor(current))
    msg = await send.draft(db, current.tenant_id, purpose=body.purpose, subject=d.subject,
                           body=d.body, created_by=_actor(current),
                           contact_id=contact.id if contact else None, language=d.language,
                           ai_generated=True,
                           disclosure=send.disclosure_for(d.language, agent=False),
                           run_id=d.run_id, in_reply_to_id=orig.id if orig else None,
                           feature="compose.ai")
    out = await _view(db, msg)
    out["problems"], out["rewritten"] = d.problems, d.rewritten
    return out


class EditIn(BaseModel):
    subject: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=20000)
    purpose: Literal["promotional", "transactional", "reply"] | None = None
    language: str | None = Field(default=None, max_length=10)


@router.patch("/messages/{message_id}")
async def edit_draft(message_id: uuid.UUID, body: EditIn, current: CurrentUser = Depends(SEND),
                     db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    m = await _msg(db, message_id)
    if m.status != st.DRAFT or m.direction != "out" or m.purpose == "staff":
        raise AppError("Only a draft can be changed")
    if body.subject is not None:
        m.subject = body.subject
    if body.body is not None:
        m.body = body.body
    if body.purpose is not None:
        m.purpose = body.purpose
    if body.language is not None:
        if body.language not in LANGUAGES:
            raise AppError("Unknown language")
        m.language = body.language
        if m.ai_generated:
            m.disclosure = send.disclosure_for(body.language, agent=False)
    m.status_at = datetime.now(UTC)
    return await _view(db, m)


@router.post("/messages/{message_id}/send")
async def send_draft(message_id: uuid.UUID, current: CurrentUser = Depends(SEND),
                     db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    m = await _msg(db, message_id)
    if m.purpose == "staff":
        raise AppError("Notifications to staff are sent by Forge")
    await send.queue(db, m, actor=_actor(current))
    return await _view(db, m)


class WhyIn(BaseModel):
    reason: str = Field(min_length=5, max_length=500)


@router.post("/messages/{message_id}/send-anyway")
async def send_anyway(message_id: uuid.UUID, body: WhyIn, current: CurrentUser = Depends(SEND),
                      db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """A held message goes now, past quiet hours and the limits (recorded). Consent,
    a stop or an erased contact still block it."""
    m = await send.send_anyway(db, await _msg(db, message_id), _actor(current), body.reason)
    return await _view(db, m)


@router.post("/messages/{message_id}/cancel")
async def cancel(message_id: uuid.UUID, current: CurrentUser = Depends(SEND),
                 db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await _view(db, await send.cancel(db, await _msg(db, message_id), _actor(current)))


@router.post("/messages/{message_id}/resend")
async def resend(message_id: uuid.UUID, current: CurrentUser = Depends(SEND),
                 db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Send a failed message again, after checking it did not arrive."""
    return await _view(db, await send.resend(db, await _msg(db, message_id), _actor(current)))


class TextIn(BaseModel):
    subject: str | None = Field(default=None, max_length=300)
    body: str = Field(default="", max_length=20000)
    language: str | None = Field(default=None, max_length=10)


@router.post("/messages/check-text")
async def check_text(body: TextIn, _: CurrentUser = Depends(SEND),
                     db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """The language and glossary checks on text being written, for the compose screen."""
    found = detect(body.body)
    lang = body.language or found.language or "en"
    problems = glossary.problems(body.subject, body.body, lang, await glossary.terms(db)) \
        if body.body.strip() else []
    return {"detected": found.language, "detected_label": found.label if found.language
            else None, "sure": found.sure, "problems": problems}


# Consent ----------------------------------------------------------------------------

def _consent_row(c: Any) -> dict[str, Any]:
    return {"id": str(c.id), "channel": c.channel, "status": c.status, "how": c.how,
            "how_label": consent_mod.HOW.get(c.how, c.how), "note": c.note,
            "source_ref": c.source_ref, "recorded_by": c.recorded_by,
            "recorded_at": c.recorded_at}


async def _contact(db: AsyncSession, contact_id: uuid.UUID) -> Contact:
    c = await db.get(Contact, contact_id)
    if c is None:
        raise NotFound("Contact not found")
    return c


@router.get("/contacts/{contact_id}/consent")
async def get_consent(contact_id: uuid.UUID, _: CurrentUser = Depends(READ),
                      db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    c = await _contact(db, contact_id)
    states = await consent_mod.states(db, c.id)
    return {"contact_id": str(c.id), "channels": [
        {"channel": s.channel, "status": s.status, "may_promote": s.may_promote,
         "how": s.how, "how_label": consent_mod.HOW.get(s.how or "", None), "at": s.at}
        for s in states.values()],
        "history": [_consent_row(r) for r in await consent_mod.history(db, c.id)],
        "hows": consent_mod.HOW}


class ConsentIn(BaseModel):
    channel: Literal["email", "whatsapp", "sms", "all"]
    status: Literal["granted", "withdrawn"]
    how: Literal["form", "in_person", "phone", "email", "import", "person"]
    note: str | None = Field(default=None, max_length=300)


@router.post("/contacts/{contact_id}/consent", status_code=201)
async def record_consent(contact_id: uuid.UUID, body: ConsentIn,
                         current: CurrentUser = Depends(require_permission("accounts.write")),
                         db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """A person records what the customer said. Stopping promotional messages on every
    channel ("all") is the same as the customer replying STOP."""
    c = await _contact(db, contact_id)
    if c.status != "active":
        raise AppError("This contact is not active")
    row = await consent_mod.record(db, current.tenant_id, c, channel=body.channel,
                                   status=body.status, how=body.how, actor=_actor(current),
                                   note=body.note)
    return _consent_row(row)


# Channel settings, email account, live switch -------------------------------------------

@router.get("/channels/settings")
async def get_settings_(current: CurrentUser = Depends(READ),
                        db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    cfg = await rules.load(db, current.tenant_id)
    r, _ = await rules.rules(db, current.tenant_id)
    return {"settings": cfg, "time_zone": str(r.tz), "holidays": sorted(
        d.isoformat() for d in r.holidays),
        "email_account": accounts.view(await accounts.get(db)),
        "presets": accounts.PRESETS, "credential_keys_configured": secrets.configured(),
        "writer_available": writer.available(), "speech_available": speech.available(),
        "simulator_addresses": {"bounce": BOUNCE, "outage": OUTAGE, "lost": LOST},
        # Unsubscribe links point at PUBLIC_BASE_URL: a customer cannot open localhost.
        "links_public": _public(get_settings().public_base_url)}


def _public(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return bool(host) and host not in ("localhost", "127.0.0.1", "::1") \
        and not host.endswith(".local")


class SettingsIn(BaseModel):
    promo_per_day: int
    promo_per_week: int
    quiet_start: str
    quiet_end: str
    promo_rest_days: list[int]
    promo_skip_holidays: bool
    reply_window_hours: int
    review_first: int
    review_every: int
    staff_email: bool


@router.put("/channels/settings")
async def put_settings(body: SettingsIn, current: CurrentUser = Depends(MANAGE),
                       db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    data = body.model_dump()
    problems = rules.problems(data)
    if problems:
        raise ValidationFailed(problems)
    before, after = await rules.save(db, current.tenant_id, data, _actor(current))
    changed = {k: {"from": before[k], "to": after[k]} for k in rules.FIELDS
               if before[k] != after[k]}
    await chain.append(db, current.tenant_id, "channels.settings_changed",
                       actor=_actor(current), data={"changes": changed})
    return after


class AccountIn(BaseModel):
    host: str = Field(max_length=255)
    port: int
    security: Literal["starttls", "ssl", "none"]
    username: str = Field(default="", max_length=320)
    password: str | None = Field(default=None, max_length=500)
    from_address: str = Field(max_length=320)
    from_name: str | None = Field(default=None, max_length=100)
    reply_to: str | None = Field(default=None, max_length=320)

    def __repr__(self) -> str:  # never print the password
        return f"<AccountIn {self.host}:{self.port}>"


@router.put("/channels/email-account")
async def put_account(body: AccountIn, current: CurrentUser = Depends(MANAGE),
                      db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    a = await accounts.save(db, current.tenant_id, body.model_dump(), _actor(current))
    return accounts.view(a) or {}


@router.post("/channels/email-account/check")
async def check_account(current: CurrentUser = Depends(MANAGE),
                        db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Connect and log in, without sending anything."""
    a = await accounts.get(db)
    if a is None:
        raise NotFound("No email account is set up")
    ok, msg = await accounts.check(db, a, _actor(current))
    return {"ok": ok, "message": msg, "account": accounts.view(a)}


class PauseIn(BaseModel):
    paused: bool


@router.post("/channels/email-account/pause")
async def pause_account(body: PauseIn, current: CurrentUser = Depends(MANAGE),
                        db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    a = await accounts.get(db)
    if a is None:
        raise NotFound("No email account is set up")
    await accounts.set_paused(db, a, body.paused, _actor(current))
    return accounts.view(a) or {}


class ModeIn(BaseModel):
    mode: Literal["simulator", "live"]


@router.post("/channels/mode")
async def set_mode(body: ModeIn, current: CurrentUser = Depends(MANAGE),
                   db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Live sends real email from the company's mailbox; it needs an account that
    passed its check. Back to the simulator at any time."""
    cfg = await rules.load(db, current.tenant_id)
    if body.mode == "live":
        a = await accounts.get(db)
        if a is None or a.status != "active" or a.last_check_ok is not True:
            raise AppError("Set up the email account and run its check first")
    if cfg["email_mode"] != body.mode:
        await rules.set_mode(db, current.tenant_id, body.mode, _actor(current))
        await chain.append(db, current.tenant_id, "channels.mode_changed",
                           actor=_actor(current),
                           data={"from": cfg["email_mode"], "to": body.mode})
    return await rules.load(db, current.tenant_id)


# The Test inbox ---------------------------------------------------------------------------

class ReplyIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


@router.post("/channels/test-inbox/{message_id}/reply", status_code=201)
async def sim_reply(message_id: uuid.UUID, body: ReplyIn, current: CurrentUser = Depends(SEND),
                    db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Reply as the customer would. "STOP" stops their promotional messages."""
    return await _view(db, await simulate.reply(db, message_id, body.body, _actor(current)))


@router.post("/channels/test-inbox/{message_id}/bounce")
async def sim_bounce(message_id: uuid.UUID, current: CurrentUser = Depends(SEND),
                     db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await _view(db, await simulate.bounce(db, message_id, _actor(current)))


@router.post("/channels/test-inbox/{message_id}/unsubscribe")
async def sim_unsubscribe(message_id: uuid.UUID, current: CurrentUser = Depends(SEND),
                          db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Press the email's unsubscribe link, as the customer would."""
    newly = await simulate.unsubscribe(db, message_id, _actor(current))
    return {"unsubscribed": True, "already": not newly}


# Languages ---------------------------------------------------------------------------------

def _tone(t: tone.Tone) -> dict[str, Any]:
    return {"language": t.language, "name": LANGUAGES[t.language].name,
            "native": LANGUAGES[t.language].native, "formality": t.formality,
            "greeting": t.greeting, "sign_off": t.sign_off, "avoid": t.avoid, "notes": t.notes,
            "is_default": t.is_default, "updated_by": t.updated_by, "updated_at": t.updated_at}


def _review(r: LanguageReview) -> dict[str, Any]:
    return {"id": str(r.id), "message_id": str(r.message_id), "language": r.language,
            "why": r.why, "blocking": r.blocking, "problems": r.problems, "status": r.status,
            "score": r.score, "comment": r.comment, "original_body": r.original_body,
            "reviewed_by": r.reviewed_by, "reviewed_at": r.reviewed_at,
            "created_at": r.created_at}


@router.get("/languages")
async def languages(_: CurrentUser = Depends(READ),
                    db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return {"recognised": [{"code": lang.code, "name": lang.name, "native": lang.native,
                            "writes": lang.code in WRITING} for lang in LANGUAGES.values()],
            "writing": list(WRITING), "tones": [_tone(t) for t in await tone.all_tones(db)],
            "formality": tone.FORMALITY, "glossary_kinds": glossary.KINDS,
            "glossary": [glossary.view(t) for t in await glossary.terms(db)],
            "report": await review.report(db), "writer_available": writer.available(),
            "speech_available": speech.available()}


class ToneIn(BaseModel):
    formality: Literal["formal", "neutral", "friendly"]
    greeting: str | None = Field(default=None, max_length=200)
    sign_off: str | None = Field(default=None, max_length=200)
    avoid: list[str] = Field(default_factory=list, max_length=30)
    notes: str | None = Field(default=None, max_length=4000)


@router.put("/languages/tone/{code}")
async def put_tone(code: str, body: ToneIn,
                   current: CurrentUser = Depends(require_permission("languages.manage")),
                   db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    if code not in WRITING:
        raise AppError("Tone guides are for the languages Forge writes in")
    return _tone(await tone.save(db, current.tenant_id, code, body.model_dump(),
                                 _actor(current)))


class TermIn(BaseModel):
    term: str = Field(max_length=100)
    kind: Literal["keep", "translate", "forbid"]
    language: str | None = Field(default=None, max_length=10)
    translation: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=300)


@router.post("/languages/glossary", status_code=201)
async def add_term(body: TermIn,
                   current: CurrentUser = Depends(require_permission("languages.manage")),
                   db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return glossary.view(await glossary.add(db, current.tenant_id, body.model_dump(),
                                            _actor(current)))


@router.delete("/languages/glossary/{term_id}", status_code=204)
async def remove_term(term_id: uuid.UUID,
                      current: CurrentUser = Depends(require_permission("languages.manage")),
                      db: AsyncSession = Depends(get_db)) -> None:
    await glossary.remove(db, current.tenant_id, term_id, _actor(current))


@router.get("/languages/reviews")
async def list_reviews(status: str = "waiting", language: str | None = None,
                       limit: int = Query(50, le=200), _: CurrentUser = Depends(READ),
                       db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    stmt = select(LanguageReview).where(LanguageReview.status.in_(status.split(",")))
    if language:
        stmt = stmt.where(LanguageReview.language == language)
    out = []
    for r in await db.scalars(stmt.order_by(LanguageReview.blocking.desc(),
                                            LanguageReview.created_at).limit(limit)):
        row = _review(r)
        m = await db.get(Message, r.message_id)
        row["message"] = await _view(db, m) if m else None
        out.append(row)
    return out


class VerdictIn(BaseModel):
    verdict: Literal["approve", "change", "reject"]
    score: int | None = Field(default=None, ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)
    subject: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=20000)


@router.post("/languages/reviews/{review_id}")
async def decide_review(review_id: uuid.UUID, body: VerdictIn,
                        current: CurrentUser = Depends(require_permission("languages.review")),
                        db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    r = await review.decide(db, review_id, verdict=body.verdict, actor=_actor(current),
                            score=body.score, comment=body.comment, subject=body.subject,
                            body=body.body)
    return _review(r)


@router.post("/languages/detect")
async def detect_language(body: TextIn, _: CurrentUser = Depends(READ)) -> dict[str, Any]:
    found = detect(body.body)
    return {"language": found.language, "script": found.script, "sure": found.sure,
            "label": found.label if found.language else None, "share": round(found.share, 2)}


@router.post("/languages/transcribe")
async def transcribe(file: UploadFile = File(...), language: str | None = Form(default=None),
                     current: CurrentUser = Depends(SEND),
                     db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Speech to text for dictating a message (L10 A). The audio is not kept."""
    audio = await file.read(speech.MAX_BYTES + 1)
    return await speech.transcribe(db, current.tenant_id, audio=audio,
                                   filename=file.filename or "recording.webm",
                                   content_type=file.content_type or "", language=language)
