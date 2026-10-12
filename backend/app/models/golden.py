"""Golden records (F12.1): the account graph and contacts, plus the Data Steward's
bookkeeping (field provenance, match candidates, merge history, imports).

Account graph: group -> legal entity -> sites (plant / ship-to / bill-to / office).
A merged record is never deleted: it stays as status="merged" with merged_into_id,
so every merge can be undone.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantMixin, TimestampMixin

ACCOUNT_KINDS = ("group", "entity", "site")
SITE_TYPES = ("plant", "ship_to", "bill_to", "office", "warehouse", "branch")
# Used by autonomy overrides and policy rules (F12.2). Empty means standard.
ACCOUNT_TIERS = ("strategic", "key", "standard")
RECORD_STATUSES = ("active", "merged", "inactive", "erased")  # erased: contacts (R6)


class Account(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "accounts"

    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="entity")
    site_type: Mapped[str | None] = mapped_column(String(20))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(300))
    gstin: Mapped[str | None] = mapped_column(String(15), index=True)
    pan: Mapped[str | None] = mapped_column(String(10), index=True)
    cin: Mapped[str | None] = mapped_column(String(21), index=True)
    website: Mapped[str | None] = mapped_column(String(300))
    domain: Mapped[str | None] = mapped_column(String(253), index=True)
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(20), index=True)
    address_line1: Mapped[str | None] = mapped_column(String(300))
    address_line2: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(100))
    pincode: Mapped[str | None] = mapped_column(String(6), index=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="IN")
    industry: Mapped[str | None] = mapped_column(String(100))
    tier: Mapped[str | None] = mapped_column(String(20))

    # Matching helpers, maintained by app.steward.records.refresh_match_fields()
    normalized_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    phonetic_keys: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list)

    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active", index=True)
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id")
    )
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="manual")
    hygiene_score: Mapped[int | None] = mapped_column(Integer)
    hygiene_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # The named-account owner: their enquiries go to this person first (F1.9, N12 A).
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), index=True)


class Contact(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "contacts"

    account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id"), index=True
    )
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    phone: Mapped[str | None] = mapped_column(String(20), index=True)
    designation: Mapped[str | None] = mapped_column(String(150))
    department: Mapped[str | None] = mapped_column(String(100))
    preferred_language: Mapped[str | None] = mapped_column(String(10))

    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    phonetic_keys: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list)

    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active", index=True)
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("contacts.id")
    )
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="manual")
    hygiene_score: Mapped[int | None] = mapped_column(Integer)
    hygiene_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class FieldProvenance(IdMixin, TenantMixin, Base):
    """Where a field value came from and when it was last verified.
    status: current (the value on the record) | proposed (a conflicting value
    waiting for a person) | superseded | rejected."""

    __tablename__ = "field_provenance"

    record_type: Mapped[str] = mapped_column(String(10), nullable=False)  # account | contact
    record_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    field: Mapped[str] = mapped_column(String(50), nullable=False)
    value: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(500))
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="current")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_by: Mapped[str | None] = mapped_column(String(100))  # user id or agent name
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MatchCandidate(IdMixin, TenantMixin, Base):
    """A possible duplicate pair. left_id < right_id so each pair is stored once.
    band: auto_merge | review | distinct. status: open | merged | not_duplicate | superseded."""

    __tablename__ = "match_candidates"
    __table_args__ = (UniqueConstraint("tenant_id", "record_type", "left_id", "right_id"),)

    record_type: Mapped[str] = mapped_column(String(10), nullable=False)
    left_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    right_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    band: Mapped[str] = mapped_column(String(12), nullable=False)
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(15), nullable=False, default="open", index=True)
    decided_by: Mapped[str | None] = mapped_column(String(100))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MergeEvent(IdMixin, TenantMixin, Base):
    """Everything needed to undo a merge: both records as they were, the field
    choices, and every reference that was re-pointed."""

    __tablename__ = "merge_events"

    record_type: Mapped[str] = mapped_column(String(10), nullable=False)
    survivor_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    merged_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("match_candidates.id")
    )
    survivor_before: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    merged_before: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    survivor_after: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    field_choices: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    repointed: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    automatic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    decided_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    undone_by: Mapped[str | None] = mapped_column(String(100))
    undo_notes: Mapped[list[str] | None] = mapped_column(JSONB)


class ImportBatch(IdMixin, TenantMixin, Base):
    """Migration mode: a bulk legacy load (CSV) and what the steward did with it."""

    __tablename__ = "import_batches"

    record_type: Mapped[str] = mapped_column(String(10), nullable=False)
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(15), nullable=False, default="processing")
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    auto_merged_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    rows: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccountEmbedding(TenantMixin, TimestampMixin, Base):
    """Meaning vector of an account's name (decision S2A), used only to put pairs the
    rules missed into the review queue. `model` is "provider/model": vectors from
    different models are never compared. The column has no fixed size, so there is no
    ANN index yet; add one (with a fixed dimension) once the model is settled."""

    __tablename__ = "account_embeddings"

    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(String(150), nullable=False)
    dims: Mapped[int] = mapped_column(Integer, nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(), nullable=False)
