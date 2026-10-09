"""F12.6 adapter layer: the contract-test suite on the fake ERPNext, plus the failure
paths and translations the generic suite cannot set up.

Set FORGE_ERPNEXT_URL / FORGE_ERPNEXT_KEY / FORGE_ERPNEXT_SECRET to also run the
suite against a real ERPNext site.
"""

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.integrations.adapter import Page
from app.integrations.contracts import Entity, Invoice, Order
from app.integrations.erpnext.adapter import ERPNextAdapter, ERPNextSettings
from app.integrations.testing import contract
from app.integrations.testing.fake_erpnext import FakeERPNext
from app.integrations.testing.seed import seed_demo


def make_fake(**seed_kw) -> FakeERPNext:
    fake = FakeERPNext()
    seed_demo(fake, **seed_kw)
    return fake


def adapter_for(fake: FakeERPNext, secret: str | None = None) -> ERPNextAdapter:
    return ERPNextAdapter(
        ERPNextSettings("http://erp.test", fake.api_key, secret or fake.api_secret),
        transport=fake,
    )


def fake_target(fake: FakeERPNext) -> contract.Target:
    offline = FakeERPNext()
    offline.fault = "offline"
    return contract.Target(
        name="fake ERPNext",
        make=lambda: adapter_for(fake),
        make_bad_login=lambda: adapter_for(fake, secret="wrong"),
        make_unreachable=lambda: adapter_for(offline),
    )


async def read_all(a: ERPNextAdapter, entity: Entity, limit: int = 5, since=None):
    records, problems, cursor = [], [], None
    while True:
        page = await a.changes(entity, since=since, cursor=cursor, limit=limit)
        records += page.records
        problems += page.problems
        cursor = page.next_cursor
        if not cursor:
            return records, problems


async def test_contract_suite_passes_on_fake_erpnext():
    results = await contract.run(fake_target(make_fake(burst=7)))
    assert results == {name: None for name in contract.CHECKS}


@pytest.mark.skipif(not os.environ.get("FORGE_ERPNEXT_URL"), reason="no live ERPNext configured")
async def test_contract_suite_passes_on_live_erpnext():
    target = contract._erpnext_target(
        os.environ["FORGE_ERPNEXT_URL"], os.environ["FORGE_ERPNEXT_KEY"],
        os.environ["FORGE_ERPNEXT_SECRET"], os.environ.get("FORGE_ERPNEXT_TZ", "Asia/Kolkata"),
    )
    results = await contract.run(target)
    assert results == {name: None for name in contract.CHECKS}


async def test_bulk_import_sharing_one_time_is_paged_without_gaps_or_repeats():
    fake = make_fake(burst=25)
    a = adapter_for(fake)
    records, _ = await read_all(a, Entity.customer, limit=10)
    names = [r.external_id for r in records]
    assert len(names) == len(set(names)) == len(fake.docs["Customer"])
    assert sum(n.startswith("Imported Customer") for n in names) == 25
    await a.aclose()


async def test_contract_suite_catches_naive_paging():
    """The suite must fail an adapter that pages on modified time alone."""

    class NaivePaging(ERPNextAdapter):
        """Asks for "records modified after the last one I saw", ignoring ties."""

        async def changes(self, entity, *, since, cursor, limit=200):
            source = self.SOURCES[entity]
            filters = [list(f) for f in source.filters]
            if cursor:
                filters.append(["modified", ">", cursor])
            elif since:
                filters.append(["modified", ">=", self.to_local(since)])
            rows = await self.client.get_list(
                source.doctype, fields=["*"], filters=filters, order_by="modified asc", limit=limit
            )
            records, problems = await self._translate(entity, source, rows)
            last = str(rows[-1]["modified"]) if len(rows) >= limit else None
            return Page(records, last, problems)

    fake = make_fake(burst=10)
    target = fake_target(fake)
    target.make = lambda: NaivePaging(
        ERPNextSettings("http://erp.test", fake.api_key, fake.api_secret), transport=fake
    )
    results = await contract.run(target)
    assert results["listing"] is not None


async def test_record_that_cannot_be_read_is_reported_and_the_page_still_arrives():
    fake = make_fake(broken=True)
    a = adapter_for(fake)
    records, problems = await read_all(a, Entity.order, limit=50)
    assert [p.external_id for p in problems] == ["SAL-ORD-2026-BROKEN"]
    assert "qty" in problems[0].reason
    assert len(records) == len(fake.docs["Sales Order"]) - 1
    await a.aclose()


@pytest.mark.parametrize(("fault", "cause", "retryable"), [
    ("offline", "unreachable", True),
    ("timeout", "timeout", True),
    ("server_error", "server_error", True),
    ("rate_limited", "rate_limited", True),
    ("not_json", "bad_response", False),
])
async def test_failures_carry_a_specific_cause(fault, cause, retryable):
    fake = make_fake()
    a = adapter_for(fake)
    fake.fault = fault
    check = await a.check()
    assert (check.ok, check.cause) == (False, cause)
    with pytest.raises(contract.ConnectorError) as e:
        await a.changes(Entity.item, since=None, cursor=None, limit=5)
    assert (e.value.cause, e.value.retryable) == (cause, retryable)
    await a.aclose()


async def test_revoked_key_is_named_as_the_cause():
    fake = make_fake()
    a = adapter_for(fake)
    assert (await a.check()).ok
    fake.revoke()
    check = await a.check()
    assert (check.ok, check.cause) == (False, "auth_rejected")
    assert "rejected the API key" in check.message
    await a.aclose()


async def test_full_check_lists_record_types_the_user_cannot_read():
    fake = make_fake()
    fake.denied = {"Item Price", "Payment Entry Reference"}
    a = adapter_for(fake)
    check = await a.check(full=True)
    assert (check.ok, check.cause) == (False, "permission_denied")
    assert check.about["cannot_read"] == ["Item Price", "Payment Entry Reference"]
    assert check.about["time_zone_source"] == "setting"
    assert check.about["versions"] == {"frappe": "16.50.0", "erpnext": "16.50.0"}
    await a.aclose()


async def test_times_are_converted_between_site_time_and_utc():
    fake = FakeERPNext()
    fake.add("Item", {"name": "A", "item_code": "A", "item_name": "A",
                      "modified": "2026-10-08 17:58:10.358324"})
    fake.add("Item", {"name": "B", "item_code": "B", "item_name": "B",
                      "modified": "2026-10-08 18:30:00.000000"})
    a = adapter_for(fake)
    item = await a.fetch(Entity.item, "A")
    assert item.modified_at == datetime(2026, 10, 8, 12, 28, 10, 358324, tzinfo=UTC)
    later, _ = await read_all(a, Entity.item, since=datetime(2026, 10, 8, 12, 30, tzinfo=UTC))
    assert [r.external_id for r in later] == ["B"]
    await a.aclose()


async def test_customer_translation():
    fake = make_fake()
    a = adapter_for(fake)
    c = await a.fetch(Entity.customer, "Bharat Forge Components Pvt Ltd")
    assert c.gstin == "27AABCB1234F1Z8"  # from tax_id
    other = await a.fetch(Entity.customer, "Sundaram Precision Castings Ltd")
    assert other.gstin == "33AADCS5678K1ZX"  # from India Compliance's gstin field
    assert c.billing_address.city == "Pune" and c.billing_address.pincode == "411018"
    assert c.credit_limit == Decimal("500000")
    assert c.payment_terms == "Net 30" and c.customer_type == "company"
    await a.aclose()


async def test_only_selling_prices_and_customer_receipts_are_read():
    fake = make_fake()
    a = adapter_for(fake)
    prices, _ = await read_all(a, Entity.price, limit=50)
    assert prices and all(p.price_list == "Standard Selling" for p in prices)
    receipts, _ = await read_all(a, Entity.receipt, limit=50)
    assert receipts and "ACC-PAY-2026-09999" not in {r.external_id for r in receipts}
    r = receipts[0]
    assert r.allocations and r.allocations[0].invoice_number.startswith("ACC-SINV-")
    await a.aclose()


async def test_stock_is_keyed_by_item_and_warehouse():
    fake = make_fake()
    a = adapter_for(fake)
    s = await a.fetch(Entity.stock, "GBX-110@Stores - FI")
    assert (s.item_code, s.warehouse) == ("GBX-110", "Stores - FI")
    assert s.projected_qty == s.actual_qty - s.reserved_qty
    assert await a.fetch(Entity.stock, "no-at-sign") is None
    await a.aclose()


def _order_row(**kw):
    return {"name": "SO", "customer": "C", "transaction_date": "2026-05-01", "currency": "INR",
            "net_total": 100, "total_taxes_and_charges": 18, "grand_total": 118,
            "modified": "2026-05-01 10:00:00", **kw}


@pytest.mark.parametrize(("row", "status"), [
    ({"docstatus": 0, "status": "Draft"}, "draft"),
    ({"docstatus": 2, "status": "Cancelled"}, "cancelled"),
    ({"docstatus": 1, "status": "On Hold"}, "on_hold"),
    ({"docstatus": 1, "status": "Closed", "per_delivered": 50}, "closed"),
    ({"docstatus": 1, "status": "To Deliver and Bill", "per_delivered": 0}, "open"),
    ({"docstatus": 1, "status": "To Deliver and Bill", "per_delivered": 40}, "partly_delivered"),
    ({"docstatus": 1, "status": "To Bill", "per_delivered": 100}, "delivered"),
    ({"docstatus": 1, "status": "Completed", "per_delivered": 0}, "delivered"),
])
def test_order_status(row, status):
    a = ERPNextAdapter(ERPNextSettings("http://erp.test", "k", "s"))
    order: Order = a._order(_order_row(**row))
    assert order.status == status and order.erp_status == row["status"]


@pytest.mark.parametrize(("row", "status"), [
    ({"docstatus": 0}, "draft"),
    ({"docstatus": 2}, "cancelled"),
    ({"docstatus": 1, "is_return": 1, "outstanding_amount": -118}, "credit_note"),
    ({"docstatus": 1, "outstanding_amount": 0, "status": "Paid"}, "paid"),
    ({"docstatus": 1, "outstanding_amount": 118, "status": "Overdue"}, "overdue"),
    ({"docstatus": 1, "outstanding_amount": 50, "status": "Partly Paid"}, "partly_paid"),
    ({"docstatus": 1, "outstanding_amount": 118, "status": "Unpaid"}, "unpaid"),
])
def test_invoice_status(row, status):
    a = ERPNextAdapter(ERPNextSettings("http://erp.test", "k", "s"))
    base = {"name": "SI", "customer": "C", "posting_date": "2026-06-01", "currency": "INR",
            "net_total": 100, "total_taxes_and_charges": 18, "grand_total": 118,
            "modified": "2026-06-01 10:00:00"}
    invoice: Invoice = a._invoice({**base, **row})
    assert invoice.status == status


def test_missing_settings_are_named():
    with pytest.raises(contract.ConnectorError) as e:
        ERPNextAdapter(ERPNextSettings("", "k", "s"))
    assert e.value.cause == "not_configured"
