"""Erasing a contact's personal data (decision R6 A; Step 4 safeguard 5).

An admin asks; a second person with the same permission approves on a decision
card (the Privacy agent works at L0 and the asker can never approve their own
request). Then, for the contact and every older record merged into it:

- the contact keeps a stub ("Erased contact", its id, its account) and loses its
  name, email, phone and other personal fields;
- the same values are removed from field history, duplicate pairs, merge snapshots,
  import rows, decision cards, background event payloads, takeovers, messages (their
  text and addresses) and their language reviews, sync history and Forge's copy of
  ERP records; consent records stay without their free text, as proof of what the
  customer asked for; there are no meaning vectors for
  contacts (only account names have them), so there are none to remove;
- the audit trail's details that mention them are removed, except where the law
  requires them to stay: details behind customer-facing and money actions while
  inside their retention period, and anything about an account under legal hold.
  Those are counted as kept and go when their period ends;
- keyed fingerprints of the erased email and phone are kept (never the values), so
  later ERP syncs hide them on the way in (until the ERP itself is cleaned);
- the erasure is recorded with ids and counts only ("contact.erased").

Audit records written before Step 4 (format 1) cannot be changed without breaking
the chain; any that mention the contact are counted, still hidden by default (R4).
"""

import hashlib
import hmac
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Text, cast, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import chain
from app.audit import evidence as ev_crypto
from app.audit.redact import LABELS
from app.audit.retention import held_accounts
from app.core.errors import ValidationFailed
from app.models import (
    AuditEvidence,
    AuditRecord,
    Consent,
    Contact,
    Decision,
    ErasedIdentifier,
    FieldProvenance,
    ImportBatch,
    LanguageReview,
    MatchCandidate,
    MergeEvent,
    Message,
    OutboxEvent,
    RecordTakeover,
    SyncDeadLetter,
    SyncRun,
)
from app.models.integrations import ERP_RECORD_MODELS

ERASED = "[erased]"
STUB_NAME = "Erased contact"


def _digits(phone: str) -> str:
    d = re.sub(r"\D", "", phone)
    return d[-10:] if len(d) >= 10 else d


def normalize(kind: str, value: str) -> str:
    return value.strip().lower() if kind == "email" else _digits(value)


def fingerprint(key: bytes, tenant_id: uuid.UUID, kind: str, value: str) -> str:
    msg = f"erased:{tenant_id}:{kind}:{normalize(kind, value)}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


class Scrubber:
    """Finds and replaces one person's values anywhere in text or JSON."""

    def __init__(self, names: set[str], emails: set[str], phones: set[str]):
        parts = [re.escape(t) for t in sorted(names | emails, key=len, reverse=True)
                 if len(t) >= 3]
        for p in phones:
            d = _digits(p)
            if len(d) >= 8:
                parts.append(r"(?:\+?91[ -]?|0)?" + r"[ -]?".join(d))
        self.re = re.compile(r"(?<![\w@])(?:" + "|".join(parts) + r")(?![\w@])",
                             re.IGNORECASE) if parts else None
        self.like = [t for t in (*names, *emails) if len(t) >= 3] + \
            [_digits(p)[-6:] for p in phones if len(_digits(p)) >= 6]

    def text(self, s: str) -> str:
        return self.re.sub(ERASED, s) if self.re else s

    def json(self, value: Any, key: str | None = None) -> Any:
        if isinstance(value, dict):
            return {k: self.json(v, str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [self.json(v, key) for v in value]
        if isinstance(value, str):
            out = self.text(value)
            if key and key.lower() in LABELS and out != value:
                return ERASED
            return out
        return value

    def mentions(self, column):
        """SQL: the column's text contains one of the values (a cheap first filter)."""
        text_col = cast(column, Text)
        return or_(*[text_col.ilike(f"%{t}%") for t in self.like]) if self.like else None


async def _contacts(session: AsyncSession, contact_id: uuid.UUID) -> list[Contact]:
    """The contact and every record merged into it, at any depth."""
    root = await session.get(Contact, contact_id)
    if root is None:
        raise ValidationFailed("Contact not found")
    found, frontier = {root.id: root}, [root.id]
    while frontier:
        more = list(await session.scalars(select(Contact).where(
            Contact.merged_into_id.in_(frontier))))
        frontier = [c.id for c in more if c.id not in found]
        found.update({c.id: c for c in more})
    return list(found.values())


async def check_allowed(session: AsyncSession, contact_id: uuid.UUID) -> Contact:
    contact = await session.get(Contact, contact_id)
    if contact is None:
        raise ValidationFailed("Contact not found")
    if contact.status == "erased":
        raise ValidationFailed("This contact's personal data was already erased")
    if contact.account_id and contact.account_id in await held_accounts(session):
        raise ValidationFailed("The contact's account is under a legal hold, so nothing about "
                               "it can be erased until the hold is lifted")
    return contact


async def _scrub_rows(session: AsyncSession, model, columns: list[str], where,
                      sc: Scrubber) -> int:
    n = 0
    for row in await session.scalars(select(model).where(where)):
        changed = False
        for col in columns:
            before = getattr(row, col)
            if before is None:
                continue
            after = sc.text(before) if isinstance(before, str) else sc.json(before)
            if after != before:
                setattr(row, col, after)
                changed = True
        n += changed
    return n


async def erase_contact(session: AsyncSession, tenant_id: uuid.UUID, contact_id: uuid.UUID,
                        *, actor: str, reason: str, requested_by: str | None,
                        run_id: uuid.UUID | None = None) -> dict[str, Any]:
    root = await check_allowed(session, contact_id)
    people = await _contacts(session, contact_id)
    ids = [c.id for c in people]
    held = await held_accounts(session)
    if any(c.account_id in held for c in people if c.account_id):
        raise ValidationFailed("A record of this contact belongs to an account under legal hold")
    sc = Scrubber({c.full_name for c in people if c.full_name},
                  {c.email for c in people if c.email}, {c.phone for c in people if c.phone})
    emails = {c.email for c in people if c.email}
    phones = {c.phone for c in people if c.phone}
    done: dict[str, int] = {}
    kept: dict[str, int] = {}

    # The contact records themselves become stubs.
    for c in people:
        c.full_name, c.email, c.phone = STUB_NAME, None, None
        c.designation = c.department = c.preferred_language = None
        c.normalized_name, c.phonetic_keys = "", []
        c.hygiene_score, c.hygiene_details = None, None
        c.status = "erased"
    done["contacts"] = len(people)
    done["field_history"] = (await session.execute(
        update(FieldProvenance).where(FieldProvenance.record_type == "contact",
                                      FieldProvenance.record_id.in_(ids))
        .values(value=None, source_ref=None).execution_options(synchronize_session=False)
    )).rowcount or 0
    pairs = list(await session.scalars(select(MatchCandidate).where(
        MatchCandidate.record_type == "contact",
        or_(MatchCandidate.left_id.in_(ids), MatchCandidate.right_id.in_(ids)))))
    for p in pairs:
        p.reasons = []
        if p.status == "open":
            p.status, p.decided_by, p.decided_at = "superseded", actor, datetime.now(UTC)
    done["duplicate_pairs"] = len(pairs)
    done["merge_snapshots"] = await _scrub_rows(
        session, MergeEvent, ["survivor_before", "merged_before", "survivor_after",
                              "field_choices"],
        (MergeEvent.record_type == "contact")
        & or_(MergeEvent.survivor_id.in_(ids), MergeEvent.merged_id.in_(ids)), sc)

    if sc.like:
        done["import_rows"] = await _scrub_rows(
            session, ImportBatch, ["rows", "errors"],
            or_(sc.mentions(ImportBatch.rows), sc.mentions(ImportBatch.errors)), sc)
        done["decision_cards"] = await _scrub_rows(
            session, Decision, ["title", "recommendation", "reasons", "args", "facts", "checks",
                                "result", "note"],
            or_((Decision.record_type == "contact") & Decision.record_id.in_(ids),
                sc.mentions(Decision.title), sc.mentions(Decision.args),
                sc.mentions(Decision.facts), sc.mentions(Decision.result),
                sc.mentions(Decision.note), sc.mentions(Decision.recommendation),
                sc.mentions(Decision.reasons)), sc)
        done["event_payloads"] = await _scrub_rows(
            session, OutboxEvent, ["payload", "last_error"], sc.mentions(OutboxEvent.payload), sc)
        done["takeovers"] = await _scrub_rows(
            session, RecordTakeover, ["reason"],
            (RecordTakeover.record_type == "contact") & RecordTakeover.record_id.in_(ids), sc)
        done["sync_history"] = await _scrub_rows(
            session, SyncRun, ["details", "error_message"], sc.mentions(SyncRun.details), sc)
        done["sync_dead_letters"] = await _scrub_rows(
            session, SyncDeadLetter, ["raw", "reason"], sc.mentions(SyncDeadLetter.raw), sc)
        erp = 0
        for model in ERP_RECORD_MODELS.values():
            erp += await _scrub_rows(session, model, ["data"], sc.mentions(model.data), sc)
        done["erp_copies"] = erp

    about = Message.contact_id.in_(ids)
    msgs = list(await session.scalars(select(Message).where(
        or_(about, sc.mentions(Message.body)) if sc.like else about)))
    for m in msgs:
        if m.contact_id in ids:
            m.body, m.subject, m.erased_at = ERASED, None, datetime.now(UTC)
            m.to_address = m.from_address = m.link_token_hash = None
        else:
            m.body = sc.text(m.body)
            m.subject = sc.text(m.subject) if m.subject else None
    done["messages"] = len(msgs)
    # Language reviews keep their verdict and score; their text goes (Step 6).
    reviews = list(await session.scalars(select(LanguageReview).where(
        LanguageReview.message_id.in_([m.id for m in msgs if m.contact_id in ids]))))
    for r in reviews:
        r.comment = sc.text(r.comment) if r.comment else None
        r.original_body = ERASED if r.original_body else None
    done["language_reviews"] = len(reviews)
    # Consent records stay (they prove what the customer asked for, and that it was
    # honoured); their free text goes. They hold no other personal data.
    consents = list(await session.scalars(select(Consent).where(Consent.contact_id.in_(ids))))
    for c in consents:
        c.note, c.source_ref = None, None
    kept["consent_records"] = len(consents)
    done["meaning_vectors"] = 0  # none are kept for contacts (S2A embeds account names)

    # Audit details: removed unless the law keeps them.
    removed = kept_law = kept_hold = 0
    rows = await session.scalars(select(AuditEvidence).where(
        AuditEvidence.data_enc.is_not(None)).order_by(AuditEvidence.seq))
    for ev in rows:
        if ev.contact_id not in ids:
            text_ = ev_crypto.decrypt(tenant_id, ev.seq, ev.data_enc)
            if not sc.re or not sc.re.search(text_):
                continue
        if ev.account_id and ev.account_id in held:
            kept_hold += 1
            continue
        if ev.retention_class == "customer_money":
            kept_law += 1  # removed by the nightly job once its period ends
            continue
        ev.data_enc, ev.removed_at = None, datetime.now(UTC)
        ev.removed_reason = "erasure of a contact's personal data"
        removed += 1
    done["audit_details"] = removed
    kept["audit_details_required_by_law"] = kept_law
    kept["audit_details_under_legal_hold"] = kept_hold
    if sc.like:
        kept["older_sealed_records"] = await session.scalar(
            select(func.count()).select_from(AuditRecord)
            .where(AuditRecord.format == 1, sc.mentions(AuditRecord.data))) or 0

    # Fingerprints so later syncs hide these values on the way in.
    ring = ev_crypto.ring()
    for kind, values in (("email", emails), ("phone", phones)):
        for v in values:
            if not normalize(kind, v):
                continue
            fp = fingerprint(ring.keys[ring.current], tenant_id, kind, v)
            if not await session.scalar(select(ErasedIdentifier.id).where(
                    ErasedIdentifier.kind == kind, ErasedIdentifier.fingerprint == fp,
                    ErasedIdentifier.key_id == ring.current)):
                session.add(ErasedIdentifier(tenant_id=tenant_id, kind=kind, fingerprint=fp,
                                             key_id=ring.current))
    await session.flush()

    await chain.append(session, tenant_id, "contact.erased", actor=actor, record_type="contact",
                       record_id=root.id, run_id=run_id, evidence_class="customer_money",
                       account_id=root.account_id, data={
                           "contact_ids": ids, "stores": done, "kept": kept,
                           "requested_by": requested_by, "reason": sc.text(reason)})
    return {"contact_ids": [str(i) for i in ids], "stores": done, "kept": kept}


async def mask_for(session: AsyncSession, tenant_id: uuid.UUID):
    """A function hiding erased emails and phones in incoming ERP rows, or None when
    nothing was ever erased (the common case costs one query)."""
    rows = (await session.execute(select(ErasedIdentifier.kind, ErasedIdentifier.key_id,
                                         ErasedIdentifier.fingerprint))).all()
    if not rows:
        return None
    try:
        ring = ev_crypto.ring()
    except ev_crypto.EvidenceKeyError:
        return None
    wanted = {(k, kid, fp) for k, kid, fp in rows if kid in ring.keys}
    kids = {kid for _, kid, _ in wanted}
    email_re = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

    def erased(kind: str, value: str) -> bool:
        return any((kind, kid, fingerprint(ring.keys[kid], tenant_id, kind, value)) in wanted
                   for kid in kids)

    def mask(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: mask(v) for k, v in value.items()}
        if isinstance(value, list):
            return [mask(v) for v in value]
        if isinstance(value, str) and value:
            if email_re.fullmatch(value.strip()) and erased("email", value):
                return ERASED
            if 10 <= len(re.sub(r"\D", "", value)) <= 13 and not re.search(r"[A-Za-z]", value) \
                    and erased("phone", value):
                return ERASED
        return value

    return mask

