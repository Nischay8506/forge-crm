"""Integration framework (F12.6): ERP connections, sync flows and their runs, the
dead-letter list, the synced ERP records, ERP writes, the mapping studio's code maps,
and MCP client tokens. Migration 0005.

Synced records get one table per shared contract (decision E2). Each row is the
latest copy of one ERP record: the columns Forge filters and joins on, plus the whole
contract record in `data`. Stock and credit are read-only copies shown with "as of"
(synced_at). A record the ERP no longer has is marked gone_at, never deleted.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.models.base import Base, IdMixin, TenantMixin, TimestampMixin

UUIDCol = PGUUID(as_uuid=True)
MONEY = Numeric(18, 2)
QTY = Numeric(18, 4)


def _now_col() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _ts() -> Mapped[datetime | None]:
    return mapped_column(DateTime(timezone=True))


class ConnectionMixin:
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUIDCol, ForeignKey("erp_connections.id"), nullable=False, index=True
    )


class ErpConnection(IdMixin, TenantMixin, TimestampMixin, Base):
    """One ERP site a tenant connected (decision V2: several per company). The API
    key and secret are stored encrypted (app/core/secrets.py, decision E8) and are
    never returned, logged or audited; secret_hint is the last characters of the key
    so people can tell which one is in use."""

    __tablename__ = "erp_connections"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # erpnext
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    # time_zone, company, host_header, poll_minutes, entities, ...
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    api_key_enc: Mapped[str] = mapped_column(Text, nullable=False)
    api_secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    secret_hint: Mapped[str] = mapped_column(String(12), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active")
    last_check_at: Mapped[datetime | None] = _ts()
    last_check_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_check_cause: Mapped[str | None] = mapped_column(String(30))
    last_check_message: Mapped[str | None] = mapped_column(String(500))
    last_ok_at: Mapped[datetime | None] = _ts()
    about: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    webhook_token_hash: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(100), nullable=False)

    def __repr__(self) -> str:  # never show the stored credentials
        return f"<ErpConnection {self.name!r} {self.kind} {self.base_url}>"


class SyncFlow(IdMixin, TenantMixin, TimestampMixin, ConnectionMixin, Base):
    """One record type read from one connection, with what the sync loop remembers
    between runs (app.integrations.sync.FlowState) and its health."""

    __tablename__ = "sync_flows"
    __table_args__ = (UniqueConstraint("connection_id", "entity"),)

    entity: Mapped[str] = mapped_column(String(20), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    running_since: Mapped[datetime | None] = _ts()
    last_started_at: Mapped[datetime | None] = _ts()
    last_finished_at: Mapped[datetime | None] = _ts()
    last_success_at: Mapped[datetime | None] = _ts()
    last_error_cause: Mapped[str | None] = mapped_column(String(30))
    last_error_message: Mapped[str | None] = mapped_column(String(500))
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_read: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reconciled_at: Mapped[datetime | None] = _ts()
    last_reconcile: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    run_requested_at: Mapped[datetime | None] = _ts()  # "sync now" or a webhook nudge


class SyncRun(IdMixin, TenantMixin, Base):
    __tablename__ = "sync_runs"

    flow_id: Mapped[uuid.UUID] = mapped_column(
        UUIDCol, ForeignKey("sync_flows.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(12), nullable=False)  # sync | reconcile
    # schedule | manual | webhook | setup
    trigger: Mapped[str] = mapped_column(String(12), nullable=False)
    # running | succeeded | partial | failed
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    started_at: Mapped[datetime] = _now_col()
    finished_at: Mapped[datetime | None] = _ts()
    read: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    problems: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_cause: Mapped[str | None] = mapped_column(String(30))
    error_message: Mapped[str | None] = mapped_column(String(500))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class SyncDeadLetter(IdMixin, TenantMixin, ConnectionMixin, Base):
    """A record that could not be read into Forge (decision E7: a task in "Waiting
    for you"). Seeing the same record fail again bumps attempts; a later good copy
    resolves it."""

    __tablename__ = "sync_dead_letters"

    entity: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = _now_col()
    last_seen_at: Mapped[datetime] = _now_col()
    resolved_by: Mapped[str | None] = mapped_column(String(100))
    resolved_at: Mapped[datetime | None] = _ts()
    note: Mapped[str | None] = mapped_column(String(500))


class ErpRecordMixin(IdMixin, TenantMixin, ConnectionMixin):
    """What every synced ERP record row carries."""

    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    synced_at: Mapped[datetime] = _now_col()
    gone_at: Mapped[datetime | None] = _ts()
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    @declared_attr.directive
    def __table_args__(cls):  # noqa: N805
        return (UniqueConstraint("connection_id", "external_id"),)


class ErpCustomer(ErpRecordMixin, Base):
    """A customer in an ERP, linked to the golden account (decision E4). link_status:
    pending (not looked at yet), linked, review (a person decides), ignored."""

    __tablename__ = "erp_customers"

    name: Mapped[str] = mapped_column(String(300), nullable=False)
    gstin: Mapped[str | None] = mapped_column(String(15), index=True)
    pan: Mapped[str | None] = mapped_column(String(10), index=True)
    customer_group: Mapped[str | None] = mapped_column(String(200))
    territory: Mapped[str | None] = mapped_column(String(200))
    # Money terms stay per connection (decision E5): two ERPs may set them differently.
    payment_terms: Mapped[str | None] = mapped_column(String(200))
    credit_limit: Mapped[Decimal | None] = mapped_column(MONEY)
    currency: Mapped[str | None] = mapped_column(String(3))
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDCol, ForeignKey("accounts.id"), index=True
    )
    link_status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    link_method: Mapped[str | None] = mapped_column(String(20))
    link_reasons: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    candidates: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    linked_by: Mapped[str | None] = mapped_column(String(100))
    linked_at: Mapped[datetime | None] = _ts()


class ErpItem(ErpRecordMixin, Base):
    __tablename__ = "erp_items"

    code: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    item_group: Mapped[str | None] = mapped_column(String(200))
    uom: Mapped[str | None] = mapped_column(String(50))
    hsn_code: Mapped[str | None] = mapped_column(String(20))


class ErpPrice(ErpRecordMixin, Base):
    __tablename__ = "erp_prices"

    item_code: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    price_list: Mapped[str] = mapped_column(String(200), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    uom: Mapped[str | None] = mapped_column(String(50))
    customer_external_id: Mapped[str | None] = mapped_column(String(200))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_upto: Mapped[date | None] = mapped_column(Date)


class ErpOrder(ErpRecordMixin, Base):
    __tablename__ = "erp_orders"

    number: Mapped[str] = mapped_column(String(200), nullable=False)
    customer_external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    delivery_date: Mapped[date | None] = mapped_column(Date)
    customer_po: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    erp_status: Mapped[str | None] = mapped_column(String(60))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    net_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    grand_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)


class ErpInvoice(ErpRecordMixin, Base):
    __tablename__ = "erp_invoices"

    number: Mapped[str] = mapped_column(String(200), nullable=False)
    customer_external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    erp_status: Mapped[str | None] = mapped_column(String(60))
    is_return: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    grand_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    outstanding: Mapped[Decimal] = mapped_column(MONEY, nullable=False)


class ErpReceipt(ErpRecordMixin, Base):
    __tablename__ = "erp_receipts"

    number: Mapped[str] = mapped_column(String(200), nullable=False)
    customer_external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)


class ErpStock(ErpRecordMixin, Base):
    __tablename__ = "erp_stock"

    item_code: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    warehouse: Mapped[str] = mapped_column(String(200), nullable=False)
    actual_qty: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    reserved_qty: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    ordered_qty: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    projected_qty: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    uom: Mapped[str | None] = mapped_column(String(50))


class ErpWorkstation(ErpRecordMixin, Base):
    """A workstation and its hour rate (F3.2)."""

    __tablename__ = "erp_workstations"

    code: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    hour_rate: Mapped[Decimal] = mapped_column(QTY, nullable=False)


class ErpBom(ErpRecordMixin, Base):
    """A submitted BOM (F3.2); its items and operations are in `data`."""

    __tablename__ = "erp_boms"

    code: Mapped[str] = mapped_column(String(200), nullable=False)
    item_code: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    uom: Mapped[str | None] = mapped_column(String(50))
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ErpWrite(IdMixin, TenantMixin, TimestampMixin, ConnectionMixin, Base):
    """A write Forge made (or tried to make) in an ERP (decision E3: only "create
    customer in ERPNext" for now). The idempotency key stops the same request being
    sent twice; an unknown outcome is looked up in the ERP before any retry."""

    __tablename__ = "erp_writes"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)

    kind: Mapped[str] = mapped_column(String(30), nullable=False)  # create_customer
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDCol, ForeignKey("accounts.id"), index=True
    )
    # pending -> sending -> succeeded | failed | unknown; refused = stopped before sending
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(200))
    error: Mapped[str | None] = mapped_column(String(1000))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requested_by: Mapped[str] = mapped_column(String(100), nullable=False)


class ForgeCode(IdMixin, TenantMixin, TimestampMixin, Base):
    """An entry in one of Forge's own code lists, which ERP codes map onto in the
    mapping studio (decision V3)."""

    __tablename__ = "forge_codes"
    __table_args__ = (UniqueConstraint("tenant_id", "list", "code"),)

    list: Mapped[str] = mapped_column(String(20), nullable=False)
    code: Mapped[str] = mapped_column(String(200), nullable=False)
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    # standard | erp:<connection name> | manual
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class CodeMap(IdMixin, TenantMixin, TimestampMixin, ConnectionMixin, Base):
    """How one ERP code maps to a Forge code. Every change adds a CodeMapHistory row
    and an audit record."""

    __tablename__ = "code_maps"
    __table_args__ = (UniqueConstraint("connection_id", "list", "erp_code"),)

    list: Mapped[str] = mapped_column(String(20), nullable=False)
    erp_code: Mapped[str] = mapped_column(String(200), nullable=False)
    forge_code: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # suggested|confirmed|unmapped
    suggestion_source: Mapped[str | None] = mapped_column(String(20))  # exact | similar | ai
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str] = mapped_column(String(100), nullable=False)


class CodeMapHistory(IdMixin, TenantMixin, Base):
    """Append-only: the database refuses UPDATE and DELETE for the app role."""

    __tablename__ = "code_map_history"
    __table_args__ = (UniqueConstraint("code_map_id", "version"),)

    code_map_id: Mapped[uuid.UUID] = mapped_column(
        UUIDCol, ForeignKey("code_maps.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    forge_code: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    suggestion_source: Mapped[str | None] = mapped_column(String(20))
    changed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    changed_at: Mapped[datetime] = _now_col()


class McpClient(IdMixin, TenantMixin, Base):
    """A tool (an AI assistant, a script) allowed to use Forge's MCP server (decision
    E9). Only a hash of its token is stored; the token is shown once."""

    __tablename__ = "mcp_clients"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_hint: Mapped[str] = mapped_column(String(12), nullable=False)
    allow_write: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = _now_col()
    last_used_at: Mapped[datetime | None] = _ts()
    revoked_at: Mapped[datetime | None] = _ts()
    revoked_by: Mapped[str | None] = mapped_column(String(100))


ERP_RECORD_MODELS: dict[str, type[ErpRecordMixin]] = {
    "customer": ErpCustomer,
    "item": ErpItem,
    "price": ErpPrice,
    "order": ErpOrder,
    "invoice": ErpInvoice,
    "receipt": ErpReceipt,
    "stock": ErpStock,
    "workstation": ErpWorkstation,
    "bom": ErpBom,
}
