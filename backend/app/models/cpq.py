"""Phase 2, M3 CPQ records. F3.1: each product family's options model (attributes,
options and rules; every save is a new version, never changed after) and the
configurations made with it (decision C4 A)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
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
