"""Phase 2, M3 quotes (decisions C12 A, C14 A). A quote belongs to an enquiry and its
account and is numbered per company and financial year (Q/2026-27/0001). Its revisions
are R1, R2 ... on the same number; exactly one is active, and a sent revision is never
changed (F3.7). Each line keeps the cost breakdown it was worked out with (F3.2), so it
always shows why it cost what it did."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantMixin

UUIDCol = PGUUID(as_uuid=True)
QTY = Numeric(18, 4)
OPEN_QUOTE_STATUSES = ("draft", "pending_approval", "sent")


def _now_col() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Quote(IdMixin, TenantMixin, Base):
    """A quote: draft, pending_approval (F3.4), sent, accepted, lost or expired."""

    __tablename__ = "quotes"

    seq: Mapped[int] = mapped_column(Integer, nullable=False)  # within the financial year
    fy: Mapped[str] = mapped_column(String(7), nullable=False)  # 2026-27
    number: Mapped[str] = mapped_column(String(30), nullable=False)  # Q/2026-27/0001
    enquiry_id: Mapped[uuid.UUID] = mapped_column(
        UUIDCol, ForeignKey("enquiries.id"), nullable=False)
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("accounts.id"))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    active_revision: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col()


class QuoteRevision(IdMixin, TenantMixin, Base):
    """R1, R2 ... of a quote. A draft changes freely; once sent it is frozen. A revision
    after R1 says why it was made: customer_ask, cost_change or correction."""

    __tablename__ = "quote_revisions"

    quote_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("quotes.id"), nullable=False)
    number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    reason: Mapped[str | None] = mapped_column(String(20))
    reason_note: Mapped[str | None] = mapped_column(String(300))
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)
    # Delivery terms and the place the goods go, for freight (F3.2): {"state", "city"}
    delivery_terms: Mapped[str | None] = mapped_column(String(20))
    destination: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    costed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    priced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # F3.3: ok, or below_floor when any line or the quote as a whole is under its floor.
    price_status: Mapped[str | None] = mapped_column(String(12))
    # A sent revision whose costs changed since (a rate, K9 A; a BOM, K11 A).
    reprice_reason: Mapped[str | None] = mapped_column(String(500))
    reprice_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = _now_col()
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuoteLine(IdMixin, TenantMixin, Base):
    """One line of a revision: what, how many, its cost and its price. `cost` is the
    full breakdown it was worked out with (inputs, rates and their dates, formulas,
    results); `cost_flag` says why it should be worked out again (a rate or item
    changed). `price` is the waterfall that made the price (F3.3): where the list price
    came from, each discount, the margin and the floor it was checked against."""

    __tablename__ = "quote_lines"

    revision_id: Mapped[uuid.UUID] = mapped_column(
        UUIDCol, ForeignKey("quote_revisions.id"), nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    enquiry_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDCol, ForeignKey("enquiry_lines.id"))
    family_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDCol, ForeignKey("product_families.id"))
    configuration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDCol, ForeignKey("configurations.id"))
    item_code: Mapped[str | None] = mapped_column(String(140))  # the ERP item, if any
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    variant_code: Mapped[str | None] = mapped_column(String(100))
    qty: Mapped[Decimal | None] = mapped_column(QTY)
    unit: Mapped[str | None] = mapped_column(String(20))
    cost_source: Mapped[str | None] = mapped_column(String(20))  # model|bom|rate_card|unknown
    unit_cost: Mapped[Decimal | None] = mapped_column(QTY)
    cost: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cost_flag: Mapped[str | None] = mapped_column(String(500))
    # K10 A: complete, incomplete (a part is missing or invalid), unknown (no source)
    # or reviewed (a person with costs.manage accepted or set it on a Decisions card).
    cost_status: Mapped[str | None] = mapped_column(String(12))
    cost_review_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDCol, ForeignKey("decisions.id"))
    # F3.3 (C2 A). Where the list price came from: contract (a customer price in the
    # ERP), price_list (the ERP's selling list), cost_plus (the family's target margin)
    # or none, when nothing could price it.
    price_source: Mapped[str | None] = mapped_column(String(12))
    list_price: Mapped[Decimal | None] = mapped_column(QTY)
    unit_price: Mapped[Decimal | None] = mapped_column(QTY)
    margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    # ok, below_floor, no_cost (a line with no cost can't pass the floor on its own),
    # no_price (nothing could price it) or no_floor (no policy says what the floor is).
    # Anything but ok goes for approval.
    price_status: Mapped[str | None] = mapped_column(String(12))
    price: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # What the rep asked for (C2 A): {"discount_pct": 7} or {"unit_price": "1200.00"}.
    ask: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    price_review_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDCol, ForeignKey("decisions.id"))
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col()


class CostAlert(IdMixin, TenantMixin, Base):
    """A sent quote whose cost changed (K9 A): its owner hears about it in one email a
    day, listing all of theirs."""

    __tablename__ = "cost_alerts"

    quote_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("quotes.id"), nullable=False)
    revision: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = _now_col()
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
