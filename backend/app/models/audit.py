"""Step 4 (F12.4): the audit trail's evidence, seals, agent versions, retention,
legal holds, and messages with their provenance (decisions R1-R8).

The audit records themselves are in app/models/agents.py (AuditRecord).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
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


class AuditEvidence(TenantMixin, Base):
    """The details behind one audit record (R1), encrypted (Step 4 safeguard 1; see
    app/audit/evidence.py). The record's hash covers their fingerprint, so they can
    be removed (retention, erasure) but not changed; a database trigger allows only
    "data_enc -> NULL" with a reason, or re-encryption under a new key."""

    __tablename__ = "audit_evidence"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    retention_class: Mapped[str] = mapped_column(String(20), nullable=False)
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, index=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, index=True)
    data_enc: Mapped[str | None] = mapped_column(Text)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_reason: Mapped[str | None] = mapped_column(String(200))


class AuditSeal(TenantMixin, Base):
    """A signed copy of the chain's head at one moment (R3). The key lives outside
    the database, so rewriting history also needs that key."""

    __tablename__ = "audit_seals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sealed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    key_id: Mapped[str] = mapped_column(String(40), nullable=False)
    signature: Mapped[str] = mapped_column(String(128), nullable=False)
    prev_signature: Mapped[str | None] = mapped_column(String(128))


class AgentVersion(Base):
    """One distinct agent definition (R8). Definitions live in code (T4), so this is
    shared by every tenant; the number goes up when the fingerprint changes."""

    __tablename__ = "agent_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_key: Mapped[str] = mapped_column(String(60), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)


class RetentionSettings(Base):
    """A tenant's retention periods in months per record category, the law each one
    follows, and who confirmed them legally (R5, Step 4 safeguard 4)."""

    __tablename__ = "retention_settings"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("tenants.id"),
                                                 primary_key=True)
    profile: Mapped[str] = mapped_column(String(20), nullable=False)
    periods: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    basis: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    legal_review: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    updated_by: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)


class LegalHold(IdMixin, TenantMixin, Base):
    """While open, nothing about this account is deleted by retention or erasure (R5)."""

    __tablename__ = "legal_holds"

    account_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("accounts.id"),
                                                  nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    placed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    placed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    lifted_by: Mapped[str | None] = mapped_column(String(100))
    lifted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(IdMixin, TenantMixin, Base):
    """A message to or from a customer (R7, F12.5). Outgoing messages leave only
    through app/channels/send.py, which checks every rule before a provider sees them;
    the statuses are the common set every channel's receipts map onto
    (app/channels/statuses.py)."""

    __tablename__ = "messages"

    account_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("accounts.id"),
                                                         index=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("contacts.id"),
                                                         index=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False, default="out")
    status: Mapped[str] = mapped_column(String(15), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    disclosure: Mapped[str | None] = mapped_column(String(200))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, index=True)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_by: Mapped[str | None] = mapped_column(String(100))
    erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Step 6 (migration 0009)
    to_address: Mapped[str | None] = mapped_column(String(320))
    from_address: Mapped[str | None] = mapped_column(String(320))
    language: Mapped[str | None] = mapped_column(String(10))
    script: Mapped[str | None] = mapped_column(String(10))
    # promotional | transactional | reply | staff (a notification to a member of staff)
    purpose: Mapped[str | None] = mapped_column(String(15))
    provider: Mapped[str | None] = mapped_column(String(30))
    provider_ref: Mapped[str | None] = mapped_column(String(200))
    status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Why it is held, blocked, failed or bounced, in a sentence for people.
    reason: Mapped[str | None] = mapped_column(String(500))
    send_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(100))
    feature: Mapped[str | None] = mapped_column(String(60))
    # The unsubscribe link's secret, hashed (app/channels/consent.py).
    link_token_hash: Mapped[str | None] = mapped_column(String(64))
    # Who sent it anyway, past quiet hours and the limits (never past consent or a stop).
    override_by: Mapped[str | None] = mapped_column(String(100))
    in_reply_to_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("messages.id"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("users.id"))
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)


class ErasedIdentifier(TenantMixin, Base):
    """A keyed fingerprint of an erased contact's email or phone (R6), so later ERP
    syncs hide it on the way in. The value itself is not kept."""

    __tablename__ = "erased_identifiers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # email | phone
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    key_id: Mapped[str] = mapped_column(String(16), nullable=False)
    erased_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
