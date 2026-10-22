"""F3.3, the price and the floor (decision C2 A): where a line's list price comes from
(a customer's own price in the ERP, the selling price list, or the cost at the family's
target margin), the tier discount, the quantity break and the rep's ask on top, the
floor checked on every line and on the whole quote, and the approval card for a line
that cannot stand on its own."""

import uuid
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from sqlalchemy import select

from app.cpq import pricing
from app.db import tenant_session
from app.integrations.connections import use_transport
from app.integrations.testing.fake_erpnext import FakeERPNext
from app.integrations.testing.seed import seed_demo
from app.models import AuditRecord, ErpCustomer, QuoteLine, QuoteRevision
from tests.test_agents import API, _user_with_role, ctx, decide  # noqa: F401
from tests.test_costing import C, Q, _flange_cost, _flanges_quote
from tests.test_enquiries import E
from tests.test_erp_sync import _connect, sync_all

CENT = Decimal("0.01")
TODAY = date.today().isoformat()


def policy(target="25", floor="15", tiers=None, breaks=None, rounding="0.01",
           at=TODAY) -> dict:
    return {"target_margin_pct": target, "floor_margin_pct": floor,
            "tier_discounts": tiers or {}, "qty_breaks": breaks or [],
            "rounding": rounding, "effective_from": at}


async def set_policy(client, h, body: dict, target: str = "company"):
    r = await client.put(f"{C}/price-policies/{target}", headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def cost_plus(cost: Decimal, margin: str) -> Decimal:
    """Cost at a target margin, to the paisa, rounded up as Forge rounds it."""
    return (cost / (1 - Decimal(margin) / 100)).quantize(CENT, ROUND_CEILING)


async def _account(tid, quote_id: str, name: str = "Konark Pipes",
                   tier: str | None = None):
    """The quote's customer as an account, made if the enquiry had none, with its tier."""
    from app.models import Account, Quote

    async with tenant_session(tid) as s:
        quote = await s.get(Quote, uuid.UUID(quote_id))
        acc = await s.get(Account, quote.account_id) if quote.account_id else None
        if acc is None:
            acc = Account(tenant_id=quote.tenant_id, name=name,
                          normalized_name=name.lower(), source="test")
            s.add(acc)
            await s.flush()
            quote.account_id = acc.id
        if tier:
            acc.tier = tier
        return acc.id


async def test_a_cost_is_priced_at_the_target_margin_and_cleared_against_the_floor(
        client, ctx):  # noqa: F811
    h = ctx["h"]
    await set_policy(client, h, policy(target="25", floor="15"))
    _, q = await _flanges_quote(client, h, [
        ("Weld neck flange", 40, {"size": "150", "class": "300", "material": "F316",
                                  "facing": "RF"})])
    line = q["active"]["lines"][0]
    _, cost = _flange_cost("19.1", 12, "1.5")
    want = cost_plus(cost, "25")
    assert line["price_source"] == "cost_plus" and line["price_status"] == "ok"
    assert line["unit_price"] == str(want)
    assert line["margin_pct"].startswith("25")
    assert line["amount"] == str((want * 40).quantize(CENT, ROUND_HALF_UP))
    waterfall = line["price"]
    assert [s["kind"] for s in waterfall["steps"]] == ["list"]
    assert waterfall["steps"][0]["label"].endswith("25% target margin for Flanges")
    assert waterfall["floor_margin_pct"] == "15" and waterfall["policy"]["scope"] == "company"
    assert waterfall["problems"] == [] and line["price_review"] is None
    # The quote as a whole clears its floor too.
    totals = q["active"]["totals"]
    assert totals["status"] == "ok" and totals["margin_pct"] == "25"
    assert q["active"]["price_status"] == "ok" and q["active"]["priced_at"] is not None


async def test_the_tier_discount_the_quantity_break_and_the_rounding_come_off_the_list(
        client, ctx):  # noqa: F811
    h, tid = ctx["h"], ctx["tid"]
    await set_policy(client, h, policy(
        target="30", floor="10", tiers={"strategic": "5", "standard": "0"},
        breaks=[{"from_qty": "25", "discount_pct": "4"},
                {"from_qty": "500", "discount_pct": "8"}], rounding="5"))
    _, q = await _flanges_quote(client, h, [
        ("Weld neck flange", 40, {"size": "150", "class": "300", "material": "F316",
                                  "facing": "RF"})])
    # A standard customer: only the quantity break, which 40 reaches. Exact to the paisa
    # until someone switches rounding on for the quote (Y5).
    plain = q["active"]["lines"][0]
    assert q["active"]["round_off"] is False
    assert [s["kind"] for s in plain["price"]["steps"]] == ["list", "discount"]
    q = (await client.patch(f"{Q}/{q['id']}", headers=h, json={"round_off": True})).json()
    assert q["active"]["round_off"] is True
    line = q["active"]["lines"][0]
    assert line["price"]["round_off"] is True
    _, cost = _flange_cost("19.1", 12, "1.5")
    after_break = cost_plus(cost, "30") * Decimal("0.96")
    assert Decimal(line["unit_price"]) >= Decimal(plain["unit_price"])
    assert line["price"]["steps"][-1]["label"] == "Rounded up to the next ₹5"
    assert Decimal(line["unit_price"]) % 5 == 0
    assert Decimal(line["unit_price"]) >= after_break
    assert [s["kind"] for s in line["price"]["steps"]] == ["list", "discount", "rounding"]
    assert "Quantity break from 25" in line["price"]["steps"][1]["label"]

    # The same quote for a strategic account: the tier discount comes off as well.
    await _account(tid, q["id"], tier="strategic")
    again = (await client.post(f"{Q}/{q['id']}/price", headers=h)).json()["active"]["lines"][0]
    steps = again["price"]["steps"]
    assert [s["kind"] for s in steps] == ["list", "discount", "discount", "rounding"]
    assert steps[1]["label"] == "strategic customer discount 5%"
    assert Decimal(again["unit_price"]) < Decimal(line["unit_price"])
    assert again["price_status"] == "ok"  # 30% target less 5% and 4% still clears a 10% floor


async def test_a_price_under_the_floor_goes_for_approval_and_a_countered_price_is_used(
        client, ctx):  # noqa: F811
    h = ctx["h"]
    await set_policy(client, h, policy(target="20", floor="18"))
    _, q = await _flanges_quote(client, h, [
        ("Weld neck flange", 40, {"size": "150", "class": "300", "material": "F316",
                                  "facing": "RF"})])
    line = q["active"]["lines"][0]
    assert line["price_status"] == "ok" and line["price_review"] is None

    # The rep asks for 10% off: the margin falls under the floor, so a card is raised.
    r = await client.put(f"{Q}/{q['id']}/lines/{line['id']}/ask", headers=h,
                         json={"discount_pct": "10"})
    assert r.status_code == 200, r.text
    asked = r.json()["active"]["lines"][0]
    assert asked["price_status"] == "below_floor"
    assert asked["price"]["steps"][-1]["label"] == "The rep asked for 10% off"
    assert "below the 18% floor" in asked["price"]["problems"][0]
    assert r.json()["active"]["price_status"] == "below_floor"
    card_id = asked["price_review"]["id"]
    card = (await client.get(f"{API}/decisions/{card_id}", headers=h)).json()
    assert card["status"] == "pending"
    assert card["title"].startswith(f"Approve the price of {q['number']} R1, line 1")
    assert card["reasons"][0].startswith("This line is priced below its floor")
    assert card["counter_fields"][0]["key"] == "unit_price"

    # Only someone with prices.approve may decide it.
    _, viewer = await _user_with_role(client, ctx, "Price Viewer", [
        "quotes.read", "costs.view", "decisions.decide"])
    seen = (await client.get(f"{API}/decisions/{card_id}", headers=viewer)).json()
    assert not seen["can_decide"] and "prices.approve" in seen["why_not"]

    # Countered with a price: the line takes it, with the margin that price leaves.
    r = await decide(client, h, card_id, "counter", values={"unit_price": "₹1,000"},
                     note="Matched the Konark price")
    assert r.status_code == 200, r.text
    got = (await client.get(f"{Q}/{q['id']}", headers=h)).json()["active"]["lines"][0]
    assert got["unit_price"] == "1000.00"
    _, cost = _flange_cost("19.1", 12, "1.5")
    assert got["margin_pct"] == str(((Decimal(1000) - cost) / 1000 * 100).quantize(
        Decimal("0.001")).normalize())
    # Pricing again keeps the approved price and does not ask twice.
    again = (await client.post(f"{Q}/{q['id']}/price", headers=h)).json()["active"]["lines"][0]
    assert again["unit_price"] == "1000.00"
    assert again["price"]["approval"]["unit_price"] == "1000.00"
    assert again["price_review"]["id"] == card_id


async def test_a_countered_price_that_clears_the_floor_clears_the_quote(client, ctx):  # noqa: F811
    # Found walking the test guide (21 Oct): the card was decided, the total cleared its
    # floor, and the quote itself still said below_floor.
    h = ctx["h"]
    await set_policy(client, h, policy(target="20", floor="18"))
    _, q = await _flanges_quote(client, h, [
        ("Weld neck flange", 40, {"size": "150", "class": "300", "material": "F316",
                                  "facing": "RF"})])
    line = q["active"]["lines"][0]
    asked = (await client.put(f"{Q}/{q['id']}/lines/{line['id']}/ask", headers=h,
                              json={"discount_pct": "10"})).json()
    assert asked["active"]["price_status"] == "below_floor"
    card_id = asked["active"]["lines"][0]["price_review"]["id"]
    r = await decide(client, h, card_id, "counter", values={"unit_price": line["unit_price"]})
    assert r.status_code == 200, r.text
    got = (await client.get(f"{Q}/{q['id']}", headers=h)).json()["active"]
    assert got["lines"][0]["unit_price"] == line["unit_price"]
    assert got["totals"]["status"] == "ok" and got["price_status"] == "ok"


async def test_every_step_of_the_waterfall_adds_up_to_the_paisa(client, ctx):  # noqa: F811
    h, tid = ctx["h"], ctx["tid"]
    await set_policy(client, h, policy(
        target="23", floor="5", tiers={"key": "3.3"},
        breaks=[{"from_qty": "10", "discount_pct": "2.7"}]))
    _, q = await _flanges_quote(client, h, [
        ("Weld neck flange", 40, {"size": "150", "class": "300", "material": "F316",
                                  "facing": "RF"})])
    await _account(tid, q["id"], tier="key")
    line = (await client.put(f"{Q}/{q['id']}/lines/{q['active']['lines'][0]['id']}/ask",
                             headers=h, json={"discount_pct": "1.9"})).json()
    line = line["active"]["lines"][0]
    steps = line["price"]["steps"]
    assert [s["kind"] for s in steps] == ["list", "discount", "discount", "ask"]
    assert steps[0]["price"] == line["list_price"] == line["price"]["list_price"]
    for before, step in zip(steps, steps[1:], strict=False):
        assert Decimal(before["price"]) + Decimal(step["amount"]) == Decimal(step["price"])
    assert steps[-1]["price"] == line["unit_price"]


async def test_a_typed_price_is_cleared_when_the_product_changes(client, ctx):  # noqa: F811
    h = ctx["h"]
    await set_policy(client, h, policy(target="25", floor="5"))
    flange = {"size": "150", "class": "300", "material": "F316", "facing": "RF"}
    fam, q = await _flanges_quote(client, h, [("Weld neck flange", 40, flange)])
    line = q["active"]["lines"][0]
    path = f"{Q}/{q['id']}/lines/{line['id']}/ask"

    async def configure(values):
        r = await client.post(f"{API}/products/configurations", headers=h, json={
            "family_id": fam["id"], "enquiry_line_id": line["enquiry_line_id"],
            "values": values})
        assert r.status_code == 201, r.text
        return (await client.get(f"{Q}/{q['id']}", headers=h)).json()["active"]["lines"][0]

    # A price the rep typed was for the F316 flange: it goes when the line becomes F304.
    await client.put(path, headers=h, json={"unit_price": "15000"})
    got = await configure(dict(flange, material="F304"))
    assert got["ask"] == {"cleared": {"unit_price": "15000",
                                      "why": "the line was configured as FL-150-300-F304-RF"}}
    assert [s["kind"] for s in got["price"]["steps"]] == ["list"]
    assert any("₹15,000" in n and "cleared" in n for n in got["price"]["notes"])
    # A % off still means the same off the new price, so it stays.
    await client.put(path, headers=h, json={"discount_pct": "5"})
    got = await configure(flange)
    assert got["ask"] == {"discount_pct": "5"}
    assert got["price"]["steps"][-1]["label"] == "The rep asked for 5% off"
    # The same for an item code or unit changed by hand on the quote line.
    await client.put(path, headers=h, json={"unit_price": "15000"})
    got = (await client.patch(f"{Q}/{q['id']}/lines/{line['id']}", headers=h,
                              json={"unit": "set"})).json()["active"]["lines"][0]
    assert got["ask"]["cleared"]["why"] == "the unit changed"


async def test_a_line_with_no_cost_and_no_erp_price_cannot_be_priced(client, ctx):  # noqa: F811
    h = ctx["h"]
    await set_policy(client, h, policy())
    _, q = await _flanges_quote(client, h, [("Gasket", 40, None)])
    line = q["active"]["lines"][0]
    assert line["cost_status"] == "unknown"
    assert line["price_status"] == "no_price" and line["unit_price"] is None
    assert line["price"]["problems"][0].startswith("The cost of this line is not known")
    card = (await client.get(f"{API}/decisions/{line['price_review']['id']}",
                             headers=h)).json()
    assert card["reasons"][0].startswith("Nothing could price this line")
    # The quote's totals say so instead of pretending.
    assert q["active"]["totals"]["lines_left_out"] == 1
    assert q["active"]["price_status"] == "below_floor"


async def test_with_no_policy_a_line_has_no_floor_to_clear(client, ctx):  # noqa: F811
    h = ctx["h"]
    _, q = await _flanges_quote(client, h, [
        ("Weld neck flange", 10, {"size": "150", "class": "300", "material": "F316",
                                  "facing": "RF"})])
    line = q["active"]["lines"][0]
    assert line["price_status"] == "no_price" and line["unit_price"] is None
    assert "No pricing policy is set for Flanges" in line["price"]["problems"][0]
    card_id = line["price_review"]["id"]

    # Setting the policy prices every open draft again, and the card closes by itself.
    out = await set_policy(client, h, policy(target="25", floor="15"))
    assert q["number"] in out["repriced"]
    got = (await client.get(f"{Q}/{q['id']}", headers=h)).json()["active"]["lines"][0]
    assert got["price_status"] == "ok" and got["price_source"] == "cost_plus"
    assert (await client.get(f"{API}/decisions/{card_id}", headers=h)).json()["status"] \
        == "superseded"


async def test_the_whole_quote_is_checked_against_the_floor_its_lines_weigh_out_to(
        client, ctx):  # noqa: F811
    h = ctx["h"]
    await set_policy(client, h, policy(target="25", floor="20"))
    _, q = await _flanges_quote(client, h, [
        ("Weld neck flange", 40, {"size": "150", "class": "300", "material": "F316",
                                  "facing": "RF"}),
        ("Weld neck flange RTJ", 40, {"size": "150", "class": "900", "material": "F316",
                                      "facing": "RTJ"})])
    assert q["active"]["totals"]["status"] == "ok"
    one = q["active"]["lines"][0]
    # A big discount on the larger line drags the whole quote under the weighted floor,
    # even though the other line is still fine.
    r = await client.put(f"{Q}/{q['id']}/lines/{q['active']['lines'][1]['id']}/ask",
                         headers=h, json={"discount_pct": "15"})
    totals = r.json()["active"]["totals"]
    assert totals["status"] == "below_floor"
    assert Decimal(totals["margin_pct"]) < Decimal(totals["floor_margin_pct"])
    assert r.json()["active"]["lines"][0]["price_status"] == "ok"  # the first line stands
    assert one["unit_price"] == r.json()["active"]["lines"][0]["unit_price"]


async def test_a_customer_price_in_the_erp_beats_the_price_list_and_the_cost(
        client, ctx):  # noqa: F811
    h, tid = ctx["h"], ctx["tid"]
    await set_policy(client, h, policy(target="25", floor="5", tiers={"strategic": "5"},
                                       breaks=[{"from_qty": "1", "discount_pct": "3"}]))
    fake = FakeERPNext()
    seed_demo(fake)
    fake.add("Customer", {
        "name": "Konark Pipes", "customer_name": "Konark Pipes", "customer_type": "Company",
        "customer_group": "OEM", "territory": "India", "default_currency": "INR"})
    fake.add("Item Price", {
        "name": "IP-GBX-110-KONARK", "item_code": "GBX-110", "price_list": "Konark Special",
        "currency": "INR", "price_list_rate": 9000.0, "uom": "Nos", "selling": 1,
        "buying": 0, "customer": "Konark Pipes", "valid_from": "2026-04-01",
        "valid_upto": "2027-03-31"})
    erp = await _connect(tid, fake)
    try:
        await sync_all(erp)
        async with tenant_session(tid) as s:
            from app.models import ErpConnection

            conn = await s.get(ErpConnection, erp.conn_id)
            conn.settings = {**(conn.settings or {}),
                             "selling_price_list": "Standard Selling"}
        e = (await client.post(E, headers=h, json={
            "customer": {"company": "Konark Pipes", "state": "Andhra Pradesh"},
            "lines": [{"description": "Gearbox", "qty": 3, "unit": "nos"}]})).json()
        q = (await client.post(Q, headers=h, json={"enquiry_id": e["id"]})).json()
        line_id = q["active"]["lines"][0]["id"]
        got = (await client.patch(f"{Q}/{q['id']}/lines/{line_id}", headers=h,
                                  json={"item_code": "GBX-110"})).json()["active"]["lines"][0]
        # Nobody is linked to the ERP customer yet, so the selling price list is used.
        assert got["price_source"] == "price_list", got["price"]
        assert "the price list Standard Selling" in got["price"]["steps"][0]["label"]
        assert got["price"]["notes"][0].startswith("The price came from ERPNext Pune")
        # A price-list price still takes the quantity break.
        assert [s["kind"] for s in got["price"]["steps"]] == ["list", "discount"]

        account_id = await _account(tid, q["id"], tier="strategic")
        async with tenant_session(tid) as s:
            cust = await s.scalar(select(ErpCustomer).where(
                ErpCustomer.external_id == "Konark Pipes"))
            cust.account_id, cust.link_status = account_id, "linked"
        mine = (await client.post(f"{Q}/{q['id']}/price",
                                  headers=h)).json()["active"]["lines"][0]
        assert mine["price_source"] == "contract" and mine["unit_price"] == "9000.00"
        assert "a price for this customer" in mine["price"]["steps"][0]["label"]
        assert "valid until 2027-03-31" in mine["price"]["steps"][0]["label"]
        # The customer's own price is already negotiated: a strategic customer reaching a
        # quantity break gets neither discount on top of it (Y6).
        assert [s["kind"] for s in mine["price"]["steps"]] == ["list"]
        assert any("used as it is" in n for n in mine["price"]["notes"])
    finally:
        use_transport(erp.url, None)


async def test_a_price_changed_outside_forge_no_longer_matches_its_seal(client, ctx):  # noqa: F811
    h, tid = ctx["h"], ctx["tid"]
    await set_policy(client, h, policy())
    _, q = await _flanges_quote(client, h, [
        ("Weld neck flange", 40, {"size": "150", "class": "300", "material": "F316",
                                  "facing": "RF"})])
    seal = (await client.get(f"{Q}/{q['id']}/price-seal", headers=h)).json()
    assert seal["ok"] and seal["lines"] == [{"line": 1, "state": "sealed"}]
    assert seal["checked"]["seq"] > 0

    async with tenant_session(tid) as s:
        ln = await s.scalar(select(QuoteLine).join(QuoteRevision).where(
            QuoteRevision.quote_id == uuid.UUID(q["id"])))
        ln.price = {**ln.price, "unit_price": "1.00"}  # changed straight in the database
    after = (await client.get(f"{Q}/{q['id']}/price-seal", headers=h)).json()
    assert not after["ok"] and after["lines"] == [{"line": 1, "state": "changed"}]

    async with tenant_session(tid) as s:
        kinds = [a.kind for a in await s.scalars(select(AuditRecord).where(
            AuditRecord.kind == "quote.priced"))]
    assert len(kinds) >= 1


async def test_a_policy_is_never_edited_and_what_is_wrong_is_said_field_by_field(
        client, ctx):  # noqa: F811
    h = ctx["h"]
    await set_policy(client, h, policy(target="25", floor="15"))
    later = (date.today() + timedelta(days=30)).isoformat()
    await set_policy(client, h, policy(target="28", floor="18", at=later))
    out = (await client.get(f"{C}/price-policies/company", headers=h)).json()
    assert out["in_force"]["target_margin_pct"] == "25"  # today's numbers, not next month's
    assert [v["effective_from"] for v in out["versions"]] == [later, TODAY]

    r = await client.put(f"{C}/price-policies/company", headers=h,
                         json=policy(target="10", floor="20"))
    assert r.status_code == 422, r.text
    assert "floor: the floor cannot be above the target margin" in str(r.json())
    r = await client.put(f"{C}/price-policies/company", headers=h, json={
        **policy(), "tier_discounts": {"gold": "5"}, "rounding": "7"})
    body = str(r.json())
    assert r.status_code == 422 and "gold is not one of" in body and "rounding: one of" in body


def test_rounding_only_ever_goes_up():
    assert pricing._round(Decimal("1201.01"), "5") == Decimal(1205)  # noqa: SLF001
    assert pricing._round(Decimal("1200.00"), "5") == Decimal(1200)  # noqa: SLF001
    assert pricing._round(Decimal("1200.001"), "0.01") == Decimal("1200.01")  # noqa: SLF001
    assert pricing._round(Decimal("1200.12345"), "10") == Decimal(1210)  # noqa: SLF001
