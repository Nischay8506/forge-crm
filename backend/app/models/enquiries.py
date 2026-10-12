"""Phase 2, M1: enquiries, the emails they come from, routing, timers and files
(decisions N1-N15, migration 0010)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantMixin

UUIDCol = PGUUID(as_uuid=True)

# The fixed label set for incoming email (F1.1).
LABELS = ("enquiry", "complaint", "payment", "order_status", "vendor_mail", "spam")
STATUSES = ("new", "open", "waiting_customer", "parked", "closed", "merged")
OPEN_STATUSES = ("new", "open", "waiting_customer", "parked")
# "Can we make this?" (F1.6): standard, configurable, needs engineering, no fit.
MAKE_ABILITY = ("standard", "cto", "eto", "no_fit")


def _now_col() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class StoredFile(IdMixin, TenantMixin, Base):
    """A raw email or an attachment (N9 A). The bytes live in the file store
    (app/files/store.py), encrypted with a key of their own, which is itself kept
    here encrypted under EVIDENCE_KEYS. Removing a file blanks it in the store and
    records why; the row stays."""

    __tablename__ = "stored_files"

    kind: Mapped[str] = mapped_column(String(15), nullable=False)  # raw_email|attachment|upload
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(150), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    store: Mapped[str] = mapped_column(String(10), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    key_id: Mapped[str] = mapped_column(String(16), nullable=False)
    wrapped_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = _now_col()
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_reason: Mapped[str | None] = mapped_column(String(200))


class Mailbox(IdMixin, TenantMixin, Base):
    """The one sales/RFQ mailbox a company connects (N3 A). The IMAP password or the
    Google/Microsoft sign-in token is encrypted (app/core/secrets.py) and never
    returned, logged or audited."""

    __tablename__ = "mailboxes"

    provider: Mapped[str] = mapped_column(String(12), nullable=False)  # imap|gmail|microsoft
    address: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active")
    imap_host: Mapped[str | None] = mapped_column(String(255))
    imap_port: Mapped[int | None] = mapped_column(Integer)
    imap_security: Mapped[str | None] = mapped_column(String(10))
    username: Mapped[str | None] = mapped_column(String(320))
    folder: Mapped[str] = mapped_column(String(100), nullable=False, default="INBOX")
    secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    secret_hint: Mapped[str] = mapped_column(String(12), nullable=False)
    # Where reading got to: IMAP {uidvalidity, last_uid}, Gmail {history_id},
    # Microsoft {delta_link}.
    cursor: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failures: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    poll_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    push_ref: Mapped[str | None] = mapped_column(String(300))
    push_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    push_secret_hash: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col()

    def __repr__(self) -> str:  # never show the stored secret
        return f"<Mailbox {self.provider} {self.address}>"


class ProductFamily(IdMixin, TenantMixin, Base):
    """What the company makes (N8 A): the words that point an enquiry to this family,
    and the details needed to quote it, each with its question per language."""

    __tablename__ = "product_families"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    template: Mapped[str | None] = mapped_column(String(30))
    keywords: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # [{"key": "grade", "label": "Material grade", "required": true,
    #   "questions": {"en": "...", "hi": "...", "te": "..."}}]
    checklist: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    # How the family is usually supplied (F1.6); "no_fit" means we do not make it.
    make_ability: Mapped[str] = mapped_column(String(10), nullable=False, default="standard")
    moq: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # {"qty": 5, "unit": "MT"}
    # The order size the family suits best (F1.7): {"min": 10, "max": 200, "unit": "MT"}
    sweet_spot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    refer_to: Mapped[str | None] = mapped_column(String(300))  # named in a decline
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col()


class Enquiry(IdMixin, TenantMixin, Base):
    """One enquiry (F1.1): what the customer asked for, who owns it and where it is."""

    __tablename__ = "enquiries"

    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    ref: Mapped[str] = mapped_column(String(20), nullable=False)  # ENQ-2026-00042
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    source: Mapped[str] = mapped_column(String(10), nullable=False)  # email | manual
    summary: Mapped[str | None] = mapped_column(String(300))
    language: Mapped[str | None] = mapped_column(String(10))
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("accounts.id"))
    contact_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("contacts.id"))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("users.id"))
    assigned_rule: Mapped[str | None] = mapped_column(String(20))
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The customer as written in their email: company, contact_name, designation,
    # phone, email, city, state.
    customer: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # target_date, target_date_text, delivery_location, special_requirements, tender_ref
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_tender: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    product_family_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDCol, ForeignKey("product_families.id"))
    waiting_on: Mapped[str] = mapped_column(String(10), nullable=False, default="us")
    # The questions asked in the acknowledgement: [{"key", "label", "asked_at",
    # "answered_at"}]
    questions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_response_by: Mapped[str | None] = mapped_column(String(100))
    owner_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quote_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quote_sent_by: Mapped[str | None] = mapped_column(String(100))
    nudges: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    last_nudge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    waiting_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    possible_duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDCol, ForeignKey("enquiries.id"))
    duplicate_score: Mapped[int | None] = mapped_column(SmallInteger)
    duplicate_reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("enquiries.id"))
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merged_by: Mapped[str | None] = mapped_column(String(100))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(String(100))
    close_reason: Mapped[str | None] = mapped_column(String(300))
    erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # "Can we make this?" (F1.6): the stamp, its reasons, and who decided it.
    make_ability: Mapped[str | None] = mapped_column(String(10))
    make_reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    make_source: Mapped[str | None] = mapped_column(String(10))  # rules | model | person
    make_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Lead score (F1.7): 0-100, its parts {"fit": {...}, "intent": {...}, "risk": {...}}
    # and the top three reasons in words.
    score: Mapped[int | None] = mapped_column(SmallInteger)
    score_parts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    score_reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col()
    last_activity_at: Mapped[datetime] = _now_col()


class EnquiryLine(IdMixin, TenantMixin, Base):
    """One requirement line (TechnicalRequirement): what, how many, in what. `original`
    keeps the line as first read, so a correction becomes a training pair (F1.4)."""

    __tablename__ = "enquiry_lines"

    enquiry_id: Mapped[uuid.UUID] = mapped_column(
        UUIDCol, ForeignKey("enquiries.id", ondelete="CASCADE"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    item_ref: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(1000), nullable=False)
    material: Mapped[str | None] = mapped_column(String(200))
    dimensions: Mapped[str | None] = mapped_column(String(300))
    qty: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    unit: Mapped[str | None] = mapped_column(String(20))
    standard: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(String(1000))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    # Fields the reader was not sure of or could not read (left empty, never guessed)
    unsure: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    make_ability: Mapped[str | None] = mapped_column(String(10))
    make_reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    source: Mapped[str] = mapped_column(String(12), nullable=False)  # email|reply|person|attachment
    source_ref: Mapped[str | None] = mapped_column(String(100))
    original: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    corrected_by: Mapped[str | None] = mapped_column(String(100))
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col()


class InboundEmail(IdMixin, TenantMixin, Base):
    """One email read from the company's mailbox, or sent to the Test mailbox, and
    what became of it. Nothing read is ever dropped: an email is an enquiry, a reply
    on one, Other mail, or automatic mail (a bounce, an out-of-office)."""

    __tablename__ = "inbound_emails"

    mailbox_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("mailboxes.id"))
    provider: Mapped[str] = mapped_column(String(12), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(300), nullable=False)
    internet_message_id: Mapped[str | None] = mapped_column(String(500))
    thread_key: Mapped[str | None] = mapped_column(String(500))
    in_reply_to: Mapped[str | None] = mapped_column(String(500))
    references: Mapped[str | None] = mapped_column(Text)
    from_address: Mapped[str | None] = mapped_column(String(320))
    from_name: Mapped[str | None] = mapped_column(String(200))
    to_addresses: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    cc_addresses: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    subject: Mapped[str | None] = mapped_column(String(500))
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    raw_file_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("stored_files.id"))
    # [{"file_id", "filename", "content_type", "size", "skipped"}]
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False,
                                                             default=list)
    auto_kind: Mapped[str | None] = mapped_column(String(12))  # bounce|auto_reply|bulk|own
    label: Mapped[str | None] = mapped_column(String(15))
    label_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    label_source: Mapped[str | None] = mapped_column(String(8))  # model|rules|person|none
    label_reason: Mapped[str | None] = mapped_column(String(300))
    could_be_enquiry: Mapped[bool | None] = mapped_column(Boolean)
    original_label: Mapped[str | None] = mapped_column(String(15))
    relabelled_by: Mapped[str | None] = mapped_column(String(100))
    relabelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stage: Mapped[str] = mapped_column(String(10), nullable=False, default="new")
    outcome: Mapped[str | None] = mapped_column(String(10))
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    enquiry_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("enquiries.id"))
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("messages.id"))
    contact_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("contacts.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol)
    erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col()


class EnquiryEvent(TenantMixin, Base):
    """One step in an enquiry's history. Appended, never changed."""

    __tablename__ = "enquiry_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    enquiry_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("enquiries.id"),
                                                  nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    at: Mapped[datetime] = _now_col()


class EnquiryAssignment(TenantMixin, Base):
    """Who an enquiry was given to, by which rule (F1.9). Appended, never changed."""

    __tablename__ = "enquiry_assignments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    enquiry_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("enquiries.id"),
                                                  nullable=False, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("users.id"))
    previous_user_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("users.id"))
    rule: Mapped[str] = mapped_column(String(20), nullable=False)
    overrode_rule: Mapped[str | None] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    at: Mapped[datetime] = _now_col()


class EnquiryPair(IdMixin, TenantMixin, Base):
    """A person's word on two enquiries that looked alike (F1.5), remembered so the
    pair is not flagged again. first_id < second_id."""

    __tablename__ = "enquiry_pairs"

    first_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("enquiries.id"),
                                                nullable=False)
    second_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("enquiries.id"),
                                                 nullable=False)
    decision: Mapped[str] = mapped_column(String(15), nullable=False)  # different | same
    decided_by: Mapped[str] = mapped_column(String(100), nullable=False)
    decided_at: Mapped[datetime] = _now_col()


class EnquirySettings(Base):
    """A company's routing, timer and intake settings. Without a row the defaults in
    app/enquiries/settings.py apply."""

    __tablename__ = "enquiry_settings"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("tenants.id"),
                                                 primary_key=True)
    routing: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    timers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    intake: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    scoring: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    updated_by: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime] = _now_col()


class CapabilityProfile(Base):
    """What the company can make (F1.6, N8 A): processes and their limits, materials,
    certificates, industries it does not supply and those it wants (F1.7), and the
    role that reviews enquiries needing engineering (P2 A)."""

    __tablename__ = "capability_profiles"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("tenants.id"),
                                                 primary_key=True)
    # [{"name": "CNC turning", "families": [family ids], "max_diameter_mm": 300,
    #   "max_length_mm": 1500, "max_thickness_mm": null, "max_weight_kg": null,
    #   "tolerance": "IT7"}]
    processes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    materials: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    certifications: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    excluded_industries: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    target_industries: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    engineering_role_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol,
                                                                  ForeignKey("roles.id"))
    updated_by: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime] = _now_col()


class EnquiryReview(IdMixin, TenantMixin, Base):
    """An engineering review of an enquiry that needs engineering (P2 A): the lines,
    files and reasons for the engineering role, and what they decided."""

    __tablename__ = "enquiry_reviews"

    enquiry_id: Mapped[uuid.UUID] = mapped_column(
        UUIDCol, ForeignKey("enquiries.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="engineering")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open")
    role_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("roles.id"))
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("users.id"))
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    package: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    outcome: Mapped[str | None] = mapped_column(String(20))  # can_make | cannot_make
    notes: Mapped[str | None] = mapped_column(String(2000))
    opened_by: Mapped[str] = mapped_column(String(100), nullable=False)
    opened_at: Mapped[datetime] = _now_col()
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done_by: Mapped[str | None] = mapped_column(String(100))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SlaTimer(IdMixin, TenantMixin, Base):
    """One running (or finished) target on any record (F1.10's generic engine,
    app/sla/engine.py). `used_seconds` is the clock time already used before the
    current run; while paused, the remind/due/double times are empty."""

    __tablename__ = "sla_timers"

    subject_type: Mapped[str] = mapped_column(String(20), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, nullable=False)
    stage: Mapped[str] = mapped_column(String(30), nullable=False)
    clock: Mapped[str] = mapped_column(String(8), nullable=False)  # always | working
    target_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    running_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remind_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    double_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    breached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated2_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(10))  # met | missed | cancelled
    created_at: Mapped[datetime] = _now_col()
