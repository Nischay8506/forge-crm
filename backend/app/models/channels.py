"""Step 6 (F12.5): channels, consent and languages (decisions L1-L11).

Messages themselves are in app/models/audit.py (Message), next to their provenance.
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


def _now_col() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MessageEvent(TenantMixin, Base):
    """One step in a message's life, from Forge, the provider, the simulator or a
    person. `applied` is false for a receipt that arrived out of order (a "delivered"
    after "read") and so did not change the message's status. No personal data."""

    __tablename__ = "message_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUIDCol, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(15), nullable=False)
    source: Mapped[str] = mapped_column(String(12), nullable=False)
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    actor: Mapped[str | None] = mapped_column(String(100))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = _now_col()


class Consent(IdMixin, TenantMixin, Base):
    """One change to a contact's consent for promotional messages on a channel
    ("all" = every channel, e.g. after STOP). The latest row per channel wins.
    Appended, never changed (decision L9)."""

    __tablename__ = "consents"

    contact_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("contacts.id"),
                                                  nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    scope: Mapped[str] = mapped_column(String(15), nullable=False, default="promotional")
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # granted | withdrawn
    # form | in_person | phone | email | import | link | stop_word | person
    how: Mapped[str] = mapped_column(String(20), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(100), nullable=False)
    recorded_at: Mapped[datetime] = _now_col()


class ChannelSettings(Base):
    """A company's sending limits, quiet hours and test mode (L8, L11). Without a row
    the defaults in app/channels/rules.py apply."""

    __tablename__ = "channel_settings"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("tenants.id"),
                                                 primary_key=True)
    promo_per_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    promo_per_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    quiet_start: Mapped[str] = mapped_column(String(5), nullable=False)  # "21:00"
    quiet_end: Mapped[str] = mapped_column(String(5), nullable=False)  # "09:00"
    promo_rest_days: Mapped[list[int]] = mapped_column(JSONB, nullable=False)  # 6 = Sunday
    promo_skip_holidays: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reply_window_hours: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    email_mode: Mapped[str] = mapped_column(String(10), nullable=False)  # simulator | live
    review_first: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    review_every: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    staff_email: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime] = _now_col()


class EmailAccount(IdMixin, TenantMixin, Base):
    """The company's own mailbox Forge sends from over SMTP (L2 A). The password is
    encrypted (app/core/secrets.py) and never returned, logged or audited."""

    __tablename__ = "email_accounts"

    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    security: Mapped[str] = mapped_column(String(10), nullable=False)  # starttls | ssl | none
    username: Mapped[str] = mapped_column(String(320), nullable=False)
    password_enc: Mapped[str] = mapped_column(Text, nullable=False)
    secret_hint: Mapped[str] = mapped_column(String(12), nullable=False)
    from_address: Mapped[str] = mapped_column(String(320), nullable=False)
    from_name: Mapped[str | None] = mapped_column(String(100))
    reply_to: Mapped[str | None] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active")
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_check_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_check_message: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col()

    def __repr__(self) -> str:  # never show the stored password
        return f"<EmailAccount {self.from_address} via {self.host}:{self.port}>"


class ToneGuide(IdMixin, TenantMixin, Base):
    """How messages in one language should sound (L7 A). Without a row the starting
    version in app/language/tone.py applies."""

    __tablename__ = "tone_guides"

    language: Mapped[str] = mapped_column(String(10), nullable=False)
    formality: Mapped[str] = mapped_column(String(10), nullable=False)
    greeting: Mapped[str | None] = mapped_column(String(200))
    sign_off: Mapped[str | None] = mapped_column(String(200))
    avoid: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime] = _now_col()


class GlossaryTerm(IdMixin, TenantMixin, Base):
    """keep: written exactly as it is in every language (brand and product names).
    translate: in `language`, always written as `translation`.
    forbid: never used."""

    __tablename__ = "glossary_terms"

    term: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    language: Mapped[str | None] = mapped_column(String(10))
    translation: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = _now_col()


class LanguageReview(IdMixin, TenantMixin, Base):
    """A person checking the writing of an AI-written message (L7, L11).
    why: "checks" (it still broke a rule after one rewrite) or "sample" (native-speaker
    sampling). A blocking review holds the message until it is decided."""

    __tablename__ = "language_reviews"

    message_id: Mapped[uuid.UUID] = mapped_column(
        UUIDCol, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True)
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    why: Mapped[str] = mapped_column(String(10), nullable=False)
    blocking: Mapped[bool] = mapped_column(Boolean, nullable=False)
    problems: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # waiting | approved | changed | rejected | closed (the message went another way)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="waiting")
    score: Mapped[int | None] = mapped_column(SmallInteger)
    comment: Mapped[str | None] = mapped_column(String(1000))
    original_body: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(100))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now_col()
