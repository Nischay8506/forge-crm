"""Phase 2, M3 CPQ records. F3.1: each product family's options model (attributes,
options and rules; every save is a new version, never changed after) and the
configurations made with it (decision C4 A). F3.2: material and machine-hour rates with
the date they apply from, and each family's cost model (decision C3 A), versioned like
the options model."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantMixin

UUIDCol = PGUUID(as_uuid=True)


def _now_col() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ProductModel(IdMixin, TenantMixin, Base):
    """One version of a family's options and rules (app/cpq/configurator.py). Saving
    makes a new version; only the latest is `current`."""

    __tablename__ = "product_models"

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUIDCol, ForeignKey("product_families.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    attributes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    code_pattern: Mapped[str] = mapped_column(String(100), nullable=False)
    note: Mapped[str | None] = mapped_column(String(300))  # what changed, in words
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = _now_col()


class Configuration(IdMixin, TenantMixin, Base):
    """A configured product: its values, variant code and fingerprint (the same product
    always has the same fingerprint), made with one model version. `reprice_reason`
    is set when something it depends on changed (an ERP item, the family's rules)."""

    __tablename__ = "configurations"

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUIDCol, ForeignKey("product_families.id"), nullable=False)
    model_id: Mapped[uuid.UUID] = mapped_column(
        UUIDCol, ForeignKey("product_models.id"), nullable=False)
    values: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    labels: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False)
    variant_code: Mapped[str] = mapped_column(String(100), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reprice_reason: Mapped[str | None] = mapped_column(String(500))
    reprice_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = _now_col()


RATE = Numeric(18, 4)


class MaterialRate(IdMixin, TenantMixin, Base):
    """What a material costs from a date (F3.2). A change is a new row, never an edit,
    so a quote's cost always shows the rate it used; on the same date the later row
    wins (a correction)."""

    __tablename__ = "material_rates"

    code: Mapped[str] = mapped_column(String(40), nullable=False)  # F316, IS2062-E250
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # The canonical unit (kg, m, m2, m3, nos) and the rate per it (K12 A); the unit and
    # rate as typed (₹62,000 per tonne) are kept in source_unit and source_rate.
    unit: Mapped[str] = mapped_column(String(10), nullable=False, default="kg")
    rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)  # rupees per unit
    source_unit: Mapped[str | None] = mapped_column(String(20))
    source_rate: Mapped[Decimal | None] = mapped_column(RATE)
    # K4 A: the share of the bought material that ends up in the part (92 = 92%), and
    # what the rest sells for as scrap, per canonical unit. None = 100% / no credit.
    yield_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    scrap_rate: Mapped[Decimal | None] = mapped_column(RATE)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = _now_col()


class MachineRate(IdMixin, TenantMixin, Base):
    """A machine or work centre's hour rate (labour and machine) from a date, for
    companies that don't keep workstations in ERPNext (F3.2)."""

    __tablename__ = "machine_rates"

    code: Mapped[str] = mapped_column(String(140), nullable=False)
    name: Mapped[str] = mapped_column(String(140), nullable=False)
    hour_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = _now_col()


class CostModel(IdMixin, TenantMixin, Base):
    """One version of a family's cost model (app/cpq/costing.py): its rate card and its
    formulas, tables, materials and operations. Saving makes a new version."""

    __tablename__ = "cost_models"

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUIDCol, ForeignKey("product_families.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    model: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    note: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = _now_col()
