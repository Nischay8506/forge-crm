"""The ERPNext adapter (F12.6, decision 5A): ERPNext v15/v16 records -> shared contracts.

Reads go through the standard REST API (/api/resource) with an API key and secret.
The ERPNext user the key belongs to needs read access to every record type synced;
with standard roles that is Sales User, Accounts User, Stock User and Sales Master
Manager (price lists). check(full=True) reports which record types it cannot read.

ERPNext stores times in the site's time zone without saying which, so the
connection carries it (Asia/Kolkata unless set); check() reads the real value when
the user is allowed to.
"""

import html
import json
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from app.integrations.adapter import (
    ConnectionCheck,
    ConnectorError,
    CustomerMatch,
    Page,
    RecordProblem,
    Stamp,
)
from app.integrations.contracts import (
    Address,
    Allocation,
    Customer,
    Entity,
    Invoice,
    InvoiceLine,
    Item,
    NewCustomer,
    Order,
    OrderLine,
    Price,
    Receipt,
    Record,
    Stock,
)
from app.integrations.erpnext.client import FrappeClient

GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


@dataclass(frozen=True)
class ERPNextSettings:
    base_url: str
    # Kept out of repr so a logged or printed settings object never shows them.
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    time_zone: str = "Asia/Kolkata"
    company: str | None = None  # whose credit limit to read; None = the only one set
    host_header: str | None = None


# --- small value helpers ---------------------------------------------------------


def _num(row: dict[str, Any], key: str) -> Decimal:
    v = row.get(key)
    if v is None or v == "":
        return Decimal(0)
    try:
        return Decimal(str(v))
    except InvalidOperation as e:
        raise ValueError(f"{key} is not a number: {v!r}") from e


def _date(v: Any) -> date | None:
    return date.fromisoformat(str(v)[:10]) if v else None


def _text(v: Any) -> str | None:
    """Strip HTML (ERPNext descriptions are rich text) and blank -> None."""
    if v is None:
        return None
    s = html.unescape(re.sub(r"<[^>]+>", " ", str(v)))
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _line_text(x: dict[str, Any]) -> str | None:
    return _text(x.get("description")) or _text(x.get("item_name"))


# --- what each contract reads -------------------------------------------------------


@dataclass(frozen=True)
class _Child:
    key: str  # where the rows go on the parent row
    doctype: str
    filters: tuple[tuple[str, str, Any], ...] = ()


@dataclass(frozen=True)
class _Source:
    doctype: str
    translate: Callable[["ERPNextAdapter", dict[str, Any]], Record]
    filters: tuple[tuple[str, str, Any], ...] = ()
    children: tuple[_Child, ...] = ()
    id_fields: tuple[str, ...] = ("name",)

    def external_id(self, row: dict[str, Any]) -> str:
        return "@".join(str(row[f]) for f in self.id_fields)

    def id_filters(self, external_id: str) -> list[list[Any]]:
        if self.id_fields == ("name",):
            return [["name", "=", external_id]]
        parts = external_id.split("@", len(self.id_fields) - 1)
        if len(parts) != len(self.id_fields):
            return [["name", "=", "\x00"]]  # matches nothing
        return [[f, "=", p] for f, p in zip(self.id_fields, parts, strict=True)]


def _order_status(r: dict[str, Any]) -> str:
    if r.get("docstatus") == 0:
        return "draft"
    status = r.get("status") or ""
    if r.get("docstatus") == 2 or status == "Cancelled":
        return "cancelled"
    if status == "On Hold":
        return "on_hold"
    if status == "Closed":
        return "closed"
    delivered = _num(r, "per_delivered")
    if status == "Completed" or delivered >= 100:
        return "delivered"
    return "partly_delivered" if delivered > 0 else "open"


def _invoice_status(r: dict[str, Any]) -> str:
    if r.get("docstatus") == 0:
        return "draft"
    if r.get("docstatus") == 2 or r.get("status") == "Cancelled":
        return "cancelled"
    if r.get("is_return"):
        return "credit_note"
    outstanding, total = _num(r, "outstanding_amount"), _num(r, "grand_total")
    if outstanding <= 0:
        return "paid"
    if "Overdue" in (r.get("status") or ""):
        return "overdue"
    return "partly_paid" if outstanding < total else "unpaid"


class ERPNextAdapter:
    kind: ClassVar[str] = "erpnext"
    label: ClassVar[str] = "ERPNext"

    SOURCES: ClassVar[dict[Entity, _Source]]
    reads: ClassVar[frozenset[Entity]]

    def __init__(
        self, settings: ERPNextSettings, *, transport: httpx.AsyncBaseTransport | None = None
    ):
        self.settings = settings
        self.tz = ZoneInfo(settings.time_zone)
        self.client = FrappeClient(
            settings.base_url, settings.api_key, settings.api_secret,
            transport=transport, host_header=settings.host_header,
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    # --- time ----------------------------------------------------------------------

    def to_utc(self, v: Any) -> datetime:
        if not v:
            raise ValueError("missing modified time")
        dt = datetime.fromisoformat(str(v))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self.tz)
        return dt.astimezone(UTC)

    def to_local(self, v: datetime) -> str:
        if v.tzinfo is None:
            v = v.replace(tzinfo=UTC)
        local = v.astimezone(self.tz).replace(tzinfo=None)
        return local.isoformat(sep=" ", timespec="microseconds")

    # --- translations ----------------------------------------------------------------

    def _customer(self, r: dict[str, Any]) -> Customer:
        tax_id = (r.get("tax_id") or "").strip().upper()
        gstin = (r.get("gstin") or "").strip().upper() or (tax_id if GSTIN_RE.match(tax_id) else "")
        addr = r.get("_address")
        limits = r.get("credit_limits") or []
        if self.settings.company:
            limits = [x for x in limits if x.get("company") == self.settings.company]
        return Customer(
            external_id=r["name"],
            modified_at=self.to_utc(r.get("modified")),
            disabled=bool(r.get("disabled")),
            name=(r.get("customer_name") or r["name"]).strip(),
            customer_type="individual" if r.get("customer_type") == "Individual" else "company",
            gstin=gstin or None,
            pan=(r.get("pan") or "").strip().upper() or None,
            customer_group=r.get("customer_group"),
            territory=r.get("territory"),
            email=(r.get("email_id") or "").strip() or None,
            phone=(r.get("mobile_no") or "").strip() or None,
            website=(r.get("website") or "").strip() or None,
            currency=r.get("default_currency"),
            payment_terms=r.get("payment_terms"),
            credit_limit=_num(limits[0], "credit_limit") if len(limits) == 1 else None,
            billing_address=Address(
                line1=addr.get("address_line1"), line2=addr.get("address_line2"),
                city=addr.get("city"), state=addr.get("state"), pincode=addr.get("pincode"),
                country=addr.get("country"), gstin=addr.get("gstin"),
            ) if addr else None,
        )

    def _item(self, r: dict[str, Any]) -> Item:
        return Item(
            external_id=r["name"],
            modified_at=self.to_utc(r.get("modified")),
            disabled=bool(r.get("disabled")),
            code=r.get("item_code") or r["name"],
            name=(r.get("item_name") or r["name"]).strip(),
            description=_text(r.get("description")),
            item_group=r.get("item_group"),
            uom=r.get("stock_uom"),
            hsn_code=r.get("gst_hsn_code") or None,
            is_stock_item=bool(r.get("is_stock_item")),
            is_sales_item=bool(r.get("is_sales_item", 1)),
        )

    def _price(self, r: dict[str, Any]) -> Price:
        return Price(
            external_id=r["name"],
            modified_at=self.to_utc(r.get("modified")),
            item_code=r["item_code"],
            price_list=r["price_list"],
            currency=r.get("currency") or "INR",
            rate=_num(r, "price_list_rate"),
            uom=r.get("uom"),
            customer_external_id=r.get("customer") or None,
            valid_from=_date(r.get("valid_from")),
            valid_upto=_date(r.get("valid_upto")),
        )

    def _order(self, r: dict[str, Any]) -> Order:
        lines = sorted(r.get("items") or [], key=lambda x: x.get("idx") or 0)
        return Order(
            external_id=r["name"],
            modified_at=self.to_utc(r.get("modified")),
            number=r["name"],
            customer_external_id=r["customer"],
            order_date=_date(r.get("transaction_date")),
            delivery_date=_date(r.get("delivery_date")),
            customer_po=r.get("po_no") or None,
            status=_order_status(r),
            erp_status=r.get("status"),
            currency=r.get("currency") or "INR",
            net_total=_num(r, "net_total"),
            tax_total=_num(r, "total_taxes_and_charges"),
            grand_total=_num(r, "grand_total"),
            tax_template=r.get("taxes_and_charges") or None,
            lines=tuple(OrderLine(
                item_code=x["item_code"], description=_line_text(x),
                qty=_num(x, "qty"), uom=x.get("uom"), rate=_num(x, "rate"),
                amount=_num(x, "amount"), delivered_qty=_num(x, "delivered_qty"),
                delivery_date=_date(x.get("delivery_date")), warehouse=x.get("warehouse"),
            ) for x in lines),
        )

    def _invoice(self, r: dict[str, Any]) -> Invoice:
        lines = sorted(r.get("items") or [], key=lambda x: x.get("idx") or 0)
        return Invoice(
            external_id=r["name"],
            modified_at=self.to_utc(r.get("modified")),
            number=r["name"],
            customer_external_id=r["customer"],
            invoice_date=_date(r.get("posting_date")),
            due_date=_date(r.get("due_date")),
            status=_invoice_status(r),
            erp_status=r.get("status"),
            is_return=bool(r.get("is_return")),
            currency=r.get("currency") or "INR",
            net_total=_num(r, "net_total"),
            tax_total=_num(r, "total_taxes_and_charges"),
            grand_total=_num(r, "grand_total"),
            outstanding=_num(r, "outstanding_amount"),
            tax_template=r.get("taxes_and_charges") or None,
            lines=tuple(InvoiceLine(
                item_code=x["item_code"], description=_line_text(x),
                qty=_num(x, "qty"), uom=x.get("uom"), rate=_num(x, "rate"),
                amount=_num(x, "amount"), order_number=x.get("sales_order") or None,
            ) for x in lines),
        )

    def _receipt(self, r: dict[str, Any]) -> Receipt:
        refs = sorted(r.get("references") or [], key=lambda x: x.get("idx") or 0)
        return Receipt(
            external_id=r["name"],
            modified_at=self.to_utc(r.get("modified")),
            number=r["name"],
            customer_external_id=r["party"],
            received_on=_date(r.get("posting_date")),
            amount=_num(r, "paid_amount"),
            currency=r.get("paid_from_account_currency") or "INR",
            mode=r.get("mode_of_payment"),
            reference_no=r.get("reference_no") or None,
            status={0: "draft", 1: "posted", 2: "cancelled"}[int(r.get("docstatus") or 0)],
            allocations=tuple(
                Allocation(
                    invoice_number=x["reference_name"], amount=_num(x, "allocated_amount")
                )
                for x in refs if x.get("reference_doctype") == "Sales Invoice"
            ),
        )

    def _stock(self, r: dict[str, Any]) -> Stock:
        return Stock(
            external_id=f"{r['item_code']}@{r['warehouse']}",
            modified_at=self.to_utc(r.get("modified")),
            item_code=r["item_code"],
            warehouse=r["warehouse"],
            actual_qty=_num(r, "actual_qty"),
            reserved_qty=_num(r, "reserved_qty"),
            ordered_qty=_num(r, "ordered_qty"),
            projected_qty=_num(r, "projected_qty"),
            uom=r.get("stock_uom"),
        )

    # --- reading ---------------------------------------------------------------------

    async def _children(self, source: _Source, rows: list[dict[str, Any]]) -> None:
        names = [r["name"] for r in rows]
        for child in source.children:
            found: dict[str, list[dict[str, Any]]] = defaultdict(list)
            if names:
                for c in await self.client.get_list(
                    child.doctype, fields=["*"], parent=source.doctype, limit=0,
                    filters=[["parent", "in", names], *map(list, child.filters)],
                ):
                    found[c["parent"]].append(c)
            for r in rows:
                r[child.key] = found.get(r["name"], [])
        if source.doctype == "Customer":
            addresses = sorted(
                {r["customer_primary_address"] for r in rows if r.get("customer_primary_address")}
            )
            by_name: dict[str, dict[str, Any]] = {}
            if addresses:
                for a in await self.client.get_list(
                    "Address", fields=["*"], filters=[["name", "in", addresses]], limit=0
                ):
                    by_name[a["name"]] = a
            for r in rows:
                r["_address"] = by_name.get(r.get("customer_primary_address") or "")

    async def _translate(self, entity: Entity, source: _Source, rows: list[dict[str, Any]]):
        await self._children(source, rows)
        records: list[Record] = []
        problems: list[RecordProblem] = []
        for r in rows:
            try:
                records.append(source.translate(self, r))
            except (ValidationError, KeyError, ValueError, TypeError) as e:
                ext = None
                try:
                    ext = source.external_id(r)
                except KeyError:
                    pass
                problems.append(RecordProblem(entity, ext, _reason(e), r))
        return records, problems

    async def changes(
        self, entity: Entity, *, since: datetime | None, cursor: str | None, limit: int = 200
    ) -> Page:
        source = self.SOURCES[entity]
        base = [list(f) for f in source.filters]
        rows: list[dict[str, Any]]
        if cursor is None:
            filters = base + ([["modified", ">=", self.to_local(since)]] if since else [])
            rows = await self.client.get_list(
                source.doctype, fields=["*"], filters=filters,
                order_by="modified asc, name asc", limit=limit,
            )
        else:
            # Keyset paging on (modified, name), so records sharing one modified time are
            # neither skipped nor repeated: first the rest of the same time, then later ones.
            at = json.loads(cursor)
            rows = await self.client.get_list(
                source.doctype, fields=["*"],
                filters=base + [["modified", "=", at["m"]], ["name", ">", at["n"]]],
                order_by="name asc", limit=limit,
            )
            if len(rows) < limit:
                rows += await self.client.get_list(
                    source.doctype, fields=["*"], filters=base + [["modified", ">", at["m"]]],
                    order_by="modified asc, name asc", limit=limit - len(rows),
                )
        next_cursor = None
        if len(rows) >= limit:
            last = rows[-1]
            next_cursor = json.dumps({"m": str(last["modified"]), "n": last["name"]})
        records, problems = await self._translate(entity, source, rows)
        return Page(records=records, next_cursor=next_cursor, problems=problems)

    async def fetch(self, entity: Entity, external_id: str) -> Record | None:
        source = self.SOURCES[entity]
        rows = await self.client.get_list(
            source.doctype, fields=["*"], limit=2,
            filters=[list(f) for f in source.filters] + source.id_filters(external_id),
        )
        if not rows:
            return None
        records, problems = await self._translate(entity, source, rows[:1])
        if problems:
            raise ConnectorError(
                "bad_response", f"{external_id} could not be read: {problems[0].reason}"
            )
        return records[0]

    async def stamps(
        self, entity: Entity, *, cursor: str | None, limit: int = 1000
    ) -> tuple[list[Stamp], str | None]:
        source = self.SOURCES[entity]
        filters = [list(f) for f in source.filters]
        if cursor:
            filters.append(["name", ">", cursor])
        rows = await self.client.get_list(
            source.doctype, fields=sorted({"name", "modified", *source.id_fields}),
            filters=filters, order_by="name asc", limit=limit,
        )
        stamps = [Stamp(source.external_id(r), self.to_utc(r["modified"])) for r in rows]
        return stamps, (rows[-1]["name"] if len(rows) >= limit else None)

    # --- writing (decision E3: "create customer" is the one write in Phase 1) ----------

    REF_LABEL: ClassVar[str] = "Forge ref:"

    async def find_customers(
        self, *, ref: str | None, gstin: str | None, name: str | None
    ) -> list[CustomerMatch]:
        """ERPNext happily creates a second customer with the same name ("X - 1") or
        GSTIN, so Forge looks first. The GSTIN is in tax_id on a plain ERPNext (India
        Compliance keeps a copy there too)."""
        source = self.SOURCES[Entity.customer]
        found: dict[str, CustomerMatch] = {}
        for why, filters in (
            ("ref", [["customer_details", "like", f"%{self.REF_LABEL} {ref}%"]] if ref else None),
            ("gstin", [["tax_id", "=", gstin]] if gstin else None),
            ("name", [["customer_name", "=", name]] if name else None),
        ):
            if filters is None:
                continue
            rows = await self.client.get_list(source.doctype, fields=["*"], filters=filters,
                                              limit=20)
            records, _ = await self._translate(Entity.customer, source, rows)
            for c in records:
                found.setdefault(c.external_id, CustomerMatch(c, why))
        return list(found.values())

    async def create_customer(self, new: NewCustomer) -> Customer:
        doc: dict[str, Any] = {
            "customer_name": new.name,
            "customer_type": "Individual" if new.customer_type == "individual" else "Company",
            "customer_details": f"Created from Forge CRM. {self.REF_LABEL} {new.ref}",
        }
        if new.gstin:
            doc["tax_id"] = new.gstin
        if new.customer_group:
            doc["customer_group"] = new.customer_group
        if new.territory:
            doc["territory"] = new.territory
        created = await self.client.insert("Customer", doc)
        record = await self.fetch(Entity.customer, created["name"])
        if record is None:
            raise ConnectorError("bad_response",
                                 f"ERPNext created customer {created['name']} but cannot read it")
        return record

    # --- connection --------------------------------------------------------------------

    async def check(self, *, full: bool = False) -> ConnectionCheck:
        about: dict[str, Any] = {"time_zone": self.settings.time_zone}
        try:
            about["user"] = await self.client.method("frappe.auth.get_logged_user")
            versions = await self.client.method("frappe.utils.change_log.get_versions")
            if isinstance(versions, dict):
                about["versions"] = {
                    app: str(v.get("version")) for app, v in versions.items() if isinstance(v, dict)
                }
        except ConnectorError as e:
            return ConnectionCheck(ok=False, cause=e.cause, message=e.message, about=about)
        if not full:
            return ConnectionCheck(ok=True, message="Connected", about=about)

        try:
            tz = await self.client.method(
                "frappe.client.get_single_value", doctype="System Settings", field="time_zone"
            )
            if tz:
                about["time_zone"] = tz
                about["time_zone_source"] = "erpnext"
        except ConnectorError:
            about["time_zone_source"] = "setting"
        denied: list[str] = []
        for source in self.SOURCES.values():
            doctypes = [source.doctype, *(c.doctype for c in source.children)]
            for doctype in doctypes:
                try:
                    await self.client.get_list(
                        doctype, fields=["name"], limit=1,
                        parent=source.doctype if doctype != source.doctype else None,
                    )
                except ConnectorError as e:
                    if e.cause != "permission_denied":
                        return ConnectionCheck(
                            ok=False, cause=e.cause, message=e.message, about=about
                        )
                    denied.append(doctype)
        about["cannot_read"] = denied
        if denied:
            return ConnectionCheck(
                ok=False, cause="permission_denied",
                message="The ERPNext user cannot read: " + ", ".join(denied), about=about,
            )
        return ConnectionCheck(
            ok=True, message="Connected; every record type can be read", about=about
        )


def _reason(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'record'}: {err['msg']}"
            for err in e.errors()
        )
    if isinstance(e, KeyError):
        return f"missing field {e.args[0]}"
    return str(e)


ERPNextAdapter.SOURCES = {
    Entity.customer: _Source(
        "Customer", ERPNextAdapter._customer,
        children=(_Child("credit_limits", "Customer Credit Limit"),),
    ),
    Entity.item: _Source("Item", ERPNextAdapter._item),
    Entity.price: _Source("Item Price", ERPNextAdapter._price, filters=(("selling", "=", 1),)),
    Entity.order: _Source(
        "Sales Order", ERPNextAdapter._order, children=(_Child("items", "Sales Order Item"),)
    ),
    Entity.invoice: _Source(
        "Sales Invoice", ERPNextAdapter._invoice, children=(_Child("items", "Sales Invoice Item"),)
    ),
    Entity.receipt: _Source(
        "Payment Entry", ERPNextAdapter._receipt,
        filters=(("payment_type", "=", "Receive"), ("party_type", "=", "Customer")),
        children=(_Child("references", "Payment Entry Reference"),),
    ),
    Entity.stock: _Source("Bin", ERPNextAdapter._stock, id_fields=("item_code", "warehouse")),
}
ERPNextAdapter.reads = frozenset(ERPNextAdapter.SOURCES)
