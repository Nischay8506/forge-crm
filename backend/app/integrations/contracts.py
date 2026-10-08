"""Shared contracts (F12.6): the shapes every adapter translates its system's records into.

The rest of Forge only ever sees these, never an ERP's own field names, so a second
ERP needs a new adapter and nothing else. NC-CAPA and Telemetry join with the
service modules.

Conventions every adapter follows (checked by the contract-test suite):
- external_id is the source system's own stable id for the record;
- modified_at is when the source last changed it, timezone-aware UTC;
- money and quantities are Decimal, never float; dates without a time are dates;
- codes (customer group, unit, warehouse, payment terms...) are the source's own codes,
  translated to Forge's by the mapping studio, not by the adapter;
- status is one of the listed canonical values; erp_status keeps the source's word.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class Entity(StrEnum):
    customer = "customer"
    item = "item"
    price = "price"
    order = "order"
    invoice = "invoice"
    receipt = "receipt"
    stock = "stock"


ENTITY_LABELS: dict[Entity, str] = {
    Entity.customer: "Customers",
    Entity.item: "Items",
    Entity.price: "Prices",
    Entity.order: "Sales orders",
    Entity.invoice: "Invoices",
    Entity.receipt: "Payments received",
    Entity.stock: "Stock",
}


class _Shape(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Record(_Shape):
    external_id: str = Field(min_length=1)
    modified_at: AwareDatetime
    disabled: bool = False


class Address(_Shape):
    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state: str | None = None
    pincode: str | None = None
    country: str | None = None  # the source's country name or code, as given
    gstin: str | None = None


class Customer(Record):
    name: str
    legal_name: str | None = None
    customer_type: Literal["company", "individual"] = "company"
    gstin: str | None = None
    pan: str | None = None
    customer_group: str | None = None
    territory: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    currency: str | None = None
    payment_terms: str | None = None
    credit_limit: Decimal | None = None
    billing_address: Address | None = None


class Item(Record):
    code: str
    name: str
    description: str | None = None
    item_group: str | None = None
    uom: str | None = None
    hsn_code: str | None = None
    is_stock_item: bool = True
    is_sales_item: bool = True


class Price(Record):
    item_code: str
    price_list: str
    currency: str
    rate: Decimal
    uom: str | None = None
    customer_external_id: str | None = None
    valid_from: date | None = None
    valid_upto: date | None = None


OrderStatus = Literal[
    "draft", "open", "on_hold", "partly_delivered", "delivered", "closed", "cancelled"
]


class OrderLine(_Shape):
    item_code: str
    description: str | None = None
    qty: Decimal
    uom: str | None = None
    rate: Decimal
    amount: Decimal
    delivered_qty: Decimal = Decimal(0)
    delivery_date: date | None = None
    warehouse: str | None = None


class Order(Record):
    number: str
    customer_external_id: str
    order_date: date
    delivery_date: date | None = None
    customer_po: str | None = None
    status: OrderStatus
    erp_status: str | None = None
    currency: str
    net_total: Decimal
    tax_total: Decimal
    grand_total: Decimal
    lines: tuple[OrderLine, ...] = ()


InvoiceStatus = Literal[
    "draft", "unpaid", "partly_paid", "overdue", "paid", "credit_note", "cancelled"
]


class InvoiceLine(_Shape):
    item_code: str
    description: str | None = None
    qty: Decimal
    uom: str | None = None
    rate: Decimal
    amount: Decimal
    order_number: str | None = None


class Invoice(Record):
    number: str
    customer_external_id: str
    invoice_date: date
    due_date: date | None = None
    status: InvoiceStatus
    erp_status: str | None = None
    is_return: bool = False
    currency: str
    net_total: Decimal
    tax_total: Decimal
    grand_total: Decimal
    outstanding: Decimal
    lines: tuple[InvoiceLine, ...] = ()


class Allocation(_Shape):
    invoice_number: str
    amount: Decimal


class Receipt(Record):
    number: str
    customer_external_id: str
    received_on: date
    amount: Decimal
    currency: str
    mode: str | None = None
    reference_no: str | None = None
    status: Literal["draft", "posted", "cancelled"]
    allocations: tuple[Allocation, ...] = ()


class Stock(Record):
    """Stock of one item in one warehouse. external_id is "<item_code>@<warehouse>"."""

    item_code: str
    warehouse: str
    actual_qty: Decimal
    reserved_qty: Decimal = Decimal(0)
    ordered_qty: Decimal = Decimal(0)
    projected_qty: Decimal
    uom: str | None = None


CONTRACTS: dict[Entity, type[Record]] = {
    Entity.customer: Customer,
    Entity.item: Item,
    Entity.price: Price,
    Entity.order: Order,
    Entity.invoice: Invoice,
    Entity.receipt: Receipt,
    Entity.stock: Stock,
}

