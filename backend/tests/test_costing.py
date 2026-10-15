"""F3.2, the cost engine (decisions C3 A, K2 to K13 A): units and conversions, rates
typed in any unit with yield and scrap, cost models checked before they are saved, the
source order (cost model, ERPNext BOM, rate card, unknown), review cards for costs that
are incomplete or unknown, and costs that move when a rate, model or BOM changes."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest
from sqlalchemy import func, select

from app.cpq import costing, costmodel, units
from app.cpq.configurator import Model as ProductModel
from app.cpq.rates import Rate, RateBook
from app.cpq.samples import FLANGES, FLANGES_COST
from app.db import tenant_session
from app.integrations.connections import use_transport
from app.integrations.testing.fake_erpnext import FakeERPNext
from app.integrations.testing.seed import seed_demo
from app.models import (
    AuditRecord,
    CostAlert,
    ErpBom,
    ErpBomVersion,
    Message,
    Quote,
    QuoteLine,
    QuoteRevision,
    User,
)
from tests.test_agents import API, _user_with_role, ctx, decide  # noqa: F401
from tests.test_enquiries import E
from tests.test_erp_sync import _connect, sync_all

Q, C = f"{API}/quotes", f"{API}/costs"
CENT = Decimal("0.01")


def r2(v: Decimal) -> Decimal:
    return v.quantize(CENT, ROUND_HALF_UP)


# Units (K12 A) -----------------------------------------------------------------------------

def test_units_convert_only_what_is_listed():
    assert units.factor("tonne", "kg") == 1000 and units.factor("MT", "kg") == 1000
    assert units.factor("g", "kg") == Decimal("0.001") and units.factor("Kg", "kg") == 1
    assert units.convert(Decimal(2), "ft", "m") == Decimal("0.6096")
    assert units.factor("sq ft", "m²") == Decimal("0.09290304")
    assert units.factor("Box", "box") == 1  # a unit Forge doesn't know is still itself
    assert units.rate_to_canonical(Decimal(62000), "tonne") == (Decimal(62), "kg")
    with pytest.raises(units.UnitError, match="doesn't know the unit 'ton'"):
        units.factor("ton", "kg")  # 907 or 1016 kg: Forge doesn't guess
    with pytest.raises(units.UnitError, match="kg is a mass and m is a length"):
        units.factor("kg", "m")
    with pytest.raises(units.UnitError, match="'box'"):
        units.factor("box", "nos")  # never guesses how many pieces are in a box


# Rates in any unit, with yield and scrap (K4, K5, K12 A) ---------------------------------

async def test_a_rate_typed_per_tonne_is_kept_and_used_per_kg(client, ctx):  # noqa: F811
    h, today = ctx["h"], date.today().isoformat()
    r = await client.post(f"{C}/materials", headers=h, json={
        "code": "IS2062", "name": "Plate IS 2062 E250", "unit": "tonne", "rate": "62000",
        "yield_pct": "92", "scrap_rate": "28000", "effective_from": today})
    assert r.status_code == 201, r.text
    rate = r.json()["rate"]
    assert (rate["unit"], rate["rate"], rate["source_unit"], rate["source_rate"]) == (
        "kg", "62", "tonne", "62000")
    assert rate["yield_pct"] == "92" and rate["scrap_rate"] == "28"
    for bad, why in (({"yield_pct": "0"}, "yield"), ({"yield_pct": "120"}, "yield"),
                     ({"unit": "ton"}, "unit must be one Forge can convert"),
                     ({"unit": "hour"}, "unit must be one Forge can convert"),
                     ({"scrap_rate": "70000"}, "scrap rate can't be above")):
        r = await client.post(f"{C}/materials", headers=h, json={
            "code": "IS2062", "name": "Plate", "unit": "tonne", "rate": "62000",
            "effective_from": today, **bad})
        assert r.status_code == 422 and why in r.text, (bad, r.text)
    got = (await client.get(f"{C}/materials", headers=h)).json()["materials"]
    plate = next(m for m in got if m["code"] == "IS2062")
    assert plate["current"]["source_rate"] == "62000" and plate["erpnext"] is None
    async with tenant_session(ctx["tid"]) as s:
        rec = await s.scalar(select(AuditRecord).where(
            AuditRecord.kind == "cost.material_rate.added"))
        assert rec is not None


# Cost models are checked before they are saved (K8, K13 A) -------------------------------

def test_the_sample_cost_model_works_for_every_flange():
    pm = ProductModel.parse(FLANGES)
    ok = costmodel.check(FLANGES_COST, pm, {"A105", "F304", "F316", "LF2"},
                         {"CNC LATHE", "VMC"})
    assert ok.problems == [] and ok.warnings == [] and ok.tried > 500
    short = {**FLANGES_COST, "tables": {**FLANGES_COST["tables"], "weight": {
        "keys": ["size", "class"],
        "rows": [r for r in FLANGES_COST["tables"]["weight"]["rows"]
                 if r["key"] != ["600", "900"]]}}}
    got = costmodel.check(short, pm, {"F316"}, set())
    assert any(p.startswith("Value finished_kg: Table weight has no row for size 600, class "
                            "900 (for size 600, class 900") for p in got.problems), got.problems
    bad = {**FLANGES_COST, "values": [{"name": "finished_kg", "formula": "lookup(weight, size)"}],
           "operations": [{"label": "Drill", "machine": "VMC", "minutes": "holes_per * 2"}]}
    got = costmodel.check(bad, pm, set(), set())
    assert "Value finished_kg: lookup(weight, ...) needs 2 keys (size, class)" in got.problems
    assert "Operation Drill: holes_per is not an attribute or a named value" in got.problems
    warn = costmodel.check(FLANGES_COST, pm, {"F316"}, set())
    assert warn.problems == [] and "Material A105 has no rate yet" in warn.warnings
    assert "Machine CNC LATHE has no hour rate yet" in warn.warnings


def _rate(kind, code, rate, unit="kg", y=None, scrap=None):
    return Rate(kind, code, code, Decimal(rate), unit, date(2026, 10, 1), "forge",
                str(uuid.uuid4()), Decimal(y) if y else None, Decimal(scrap) if scrap else None)


def _bom(code, item, qty, items, ops=(), uom="Nos"):
    data = {"code": code, "item_code": item, "quantity": str(qty), "uom": uom, "is_default": True,
            "items": [{"item_code": i, "qty": str(q), "uom": u} for i, q, u in items],
            "operations": [{"operation": o, "workstation": w, "minutes": str(m)}
                           for o, w, m in ops]}
    return ErpBom(id=uuid.uuid4(), tenant_id=uuid.uuid4(), connection_id=uuid.uuid4(),
                  external_id=code, code=code, item_code=item, quantity=Decimal(qty), uom=uom,
                  is_default=True, data=data, modified_at=datetime.now(UTC))


def test_boms_explode_convert_units_and_stop_at_loops():
    book = costing.Book(date(2026, 10, 14), RateBook(date(2026, 10, 14), {
        "EN8": _rate("material", "EN8", 78, y=50, scrap=20),
        "BRG": _rate("material", "BRG", 180, unit="nos")}, {
        "LATHE": _rate("machine", "Lathe", 600, unit="hour")}))
    book.boms = {"PUMP": _bom("BOM-PUMP", "PUMP", 1, [("SHAFT", 2, "Nos"), ("BRG", 2, "Nos")],
                              [("Assembly", "", 30)]),
                 "SHAFT": _bom("BOM-SHAFT", "SHAFT", 10, [("EN8", 25000, "g")],
                               [("Turning", "Lathe", 120)])}
    out = costing.work_out(book, family_id=None, configuration_id=None, item_code="pump",
                           unit="nos", qty=Decimal(3))
    assert out["source"] == "bom" and [b["code"] for b in out["boms"]] == ["BOM-PUMP",
                                                                            "BOM-SHAFT"]
    parts = {p["label"]: p["amount"] for p in out["parts"]}
    # 2 shafts need 5 kg of EN8 (25,000 g per 10, converted): the BOM's quantity is what
    # is used, so no yield on top; 24 minutes of turning; 2 bearings.
    assert parts == {"EN8 (SHAFT)": "390.00", "Turning (SHAFT)": "240.00", "BRG": "360.00"}
    assert out["conversions"][0]["from"] == "g" and out["conversions"][0]["result"] == "5"
    assert out["status"] == "incomplete" and out["per_unit"] == "990.00"
    # The missing hour rate, and the overhead and packing a family's cost model would have
    # added to a BOM cost (J3 C).
    assert out["problems"] == [
        "BOM BOM-PUMP, Assembly: it has no workstation, so no hour rate",
        "BOM BOM-PUMP: the line has no product family, so its factory overhead and packing "
        "are missing"]

    book.boms["SHAFT"] = _bom("BOM-SHAFT", "SHAFT", 1, [("PUMP", 1, "Nos")])
    loop = costing.work_out(book, family_id=None, configuration_id=None, item_code="PUMP",
                            unit=None, qty=Decimal(1))
    assert any("go round in a circle (PUMP → SHAFT → PUMP)" in p for p in loop["problems"])
    book.boms["PUMP"] = _bom("BOM-PUMP", "PUMP", 1, [("BRG", 1, "Box")])
    boxed = costing.work_out(book, family_id=None, configuration_id=None, item_code="PUMP",
                             unit="kg", qty=Decimal(1))
    assert "BOM BOM-PUMP, BRG: Forge doesn't know the unit 'Box'" in boxed["problems"][1]
    assert boxed["problems"][0].startswith("The line's unit: kg is a mass")


# The whole flow ----------------------------------------------------------------------------

async def _flanges_quote(client, h, lines):
    fam = (await client.post(f"{E}/setup/families", headers=h,
                             json={"template": "flanges"})).json()
    r = await client.post(f"{C}/models/{fam['id']}/sample", headers=h)
    assert r.status_code == 200, r.text
    e = (await client.post(E, headers=h, json={
        "customer": {"company": "Konark Pipes", "state": "Andhra Pradesh"},
        "lines": [{"description": d, "qty": q, "unit": "nos"} for d, q, _ in lines]})).json()
    for ln, (_, _, values) in zip(e["lines"], lines, strict=True):
        if values:
            r = await client.post(f"{API}/products/configurations", headers=h, json={
                "family_id": fam["id"], "enquiry_line_id": ln["id"], "values": values})
            assert r.status_code == 201, r.text
    r = await client.post(Q, headers=h, json={"enquiry_id": e["id"]})
    assert r.status_code == 201, r.text
    return fam, r.json()


def _flange_cost(kg, holes, drill, rtj=False):
    """The sample model by hand: F316 at ₹410/kg, 85% yield, scrap ₹140; lathe ₹650/h,
    VMC ₹900/h; 12% overhead, 2% packing."""
    kg = Decimal(kg)
    bought = kg * 100 / 85
    material = r2(bought * 410 - (bought - kg) * 140)
    turning = r2(max(Decimal(10), 6 + kg * Decimal("0.8")) * 650 / 60)
    drilling = r2(holes * Decimal(drill) * 900 / 60)
    groove = r2(Decimal(12) * 650 / 60) if rtj else Decimal(0)
    base = material + turning + drilling + groove
    overhead = r2(base * 12 / 100)
    packing = r2((base + overhead) * 2 / 100)
    return material, base + overhead + packing


async def test_a_configured_line_is_costed_from_its_model(client, ctx):  # noqa: F811
    h = ctx["h"]
    fam, q = await _flanges_quote(client, h, [
        ("Weld neck flange", 40, {"size": "150", "class": "300", "material": "F316",
                                  "facing": "RF"}),
        ("Weld neck flange RTJ", 5, {"size": "150", "class": "900", "material": "F316",
                                     "facing": "RTJ"}),
        ("Gasket", 40, None)])
    one, two, gasket = q["active"]["lines"]
    material, total = _flange_cost("19.1", 12, "1.5")
    assert one["cost_status"] == "complete" and one["cost_source"] == "model"
    assert one["unit_cost"] == str(total), one["cost"]
    cost = one["cost"]
    assert [p["group"] for p in cost["parts"]] == ["material", "conversion", "conversion",
                                                    "overhead", "packing"]
    assert cost["parts"][0]["amount"] == str(material)
    assert "19.1 kg in the part, 22.471 kg of F316 bought at 85% yield, at ₹410 per kg, less " \
           "3.371 kg scrap at ₹140" == cost["parts"][0]["detail"]
    assert {(r["kind"], r["code"], r["rate"]) for r in cost["rates"]} == {
        ("material", "F316", "410"), ("machine", "CNC Lathe", "650"), ("machine", "VMC", "900")}
    assert cost["why"] == "The cost model of Flanges, version 1" and cost["packed_kg"] == "19.673"
    assert cost["values"] == {"finished_kg": "19.1"} and cost["problems"] == []

    _, total2 = _flange_cost("43.2", 12, "2.5", rtj=True)
    assert two["unit_cost"] == str(total2)
    assert two["cost"]["one_time"] == [{"label": "RTJ ring gauge", "amount": "6500.00",
                                        "detail": "One-time, charged once on the quote"}]

    # Nothing covers the gasket: unknown, and a card for a person who manages costs (K10).
    assert gasket["cost_status"] == "unknown" and gasket["unit_cost"] is None
    assert "the line has no product family" in gasket["cost"]["why"]
    card_id = gasket["cost_review"]["id"]
    card = (await client.get(f"{API}/decisions/{card_id}", headers=h)).json()
    assert card["status"] == "pending" and card["title"].startswith(
        f"Review the unknown cost of {q['number']} R1, line 3: Gasket")
    assert card["reasons"][0].startswith("No cost model, ERPNext BOM or rate card covers")
    assert card["record_name"] == q["number"] and card["counter_fields"][0]["key"] == "unit_cost"

    # Only people who may see costs see the card; only costs.manage may decide it.
    _, seller = await _user_with_role(client, ctx, "Seller", ["quotes.read",
                                                              "decisions.decide"])
    assert (await client.get(f"{API}/decisions/{card_id}", headers=seller)).status_code == 404
    _, viewer = await _user_with_role(client, ctx, "Cost Viewer", [
        "quotes.read", "costs.view", "decisions.decide"])
    seen = (await client.get(f"{API}/decisions/{card_id}", headers=viewer)).json()
    assert not seen["can_decide"] and "costs.manage" in seen["why_not"]

    # Countered with the unit cost to use: the line is reviewed, and stays reviewed when
    # its quantity changes or the costs are worked out again.
    r = await decide(client, h, card_id, "counter", values={"unit_cost": "₹45.5"},
                     note="Bought out from Champion at 45.50")
    assert r.status_code == 200, r.text
    got = (await client.get(f"{Q}/{q['id']}", headers=h)).json()["active"]["lines"][2]
    assert got["cost_status"] == "reviewed" and got["unit_cost"] == "45.50"
    assert got["cost"]["review"]["unit_cost"] == "45.50"
    r = await client.patch(f"{Q}/{q['id']}/lines/{gasket['id']}", headers=h, json={"qty": "60"})
    assert r.json()["active"]["lines"][2]["cost_status"] == "reviewed"
    r = await client.post(f"{Q}/{q['id']}/cost", headers=h)
    assert r.json()["active"]["lines"][2]["unit_cost"] == "45.50"
    # A new problem asks again.
    r = await client.patch(f"{Q}/{q['id']}/lines/{gasket['id']}", headers=h,
                           json={"item_code": "GSK-150"})
    again = r.json()["active"]["lines"][2]
    assert again["cost_status"] == "unknown" and again["cost_review"]["status"] == "pending"
    assert again["cost_review"]["id"] != card_id

    async with tenant_session(ctx["tid"]) as s:
        kinds = [a.kind for a in await s.scalars(select(AuditRecord).where(
            AuditRecord.kind.in_(("quote.costed", "cost.model.saved"))))]
    assert kinds.count("cost.model.saved") == 1 and kinds.count("quote.costed") >= 4


async def test_configuring_a_line_after_the_quote_started_updates_the_draft(client, ctx):  # noqa: F811
    # Found on Nischay's PC (15 Oct): the quote was started, then the line configured.
    h, tid = ctx["h"], ctx["tid"]
    fam, q = await _flanges_quote(client, h, [("pipe", 40, None), ("Gasket", 40, None)])
    one = q["active"]["lines"][0]
    assert one["cost_status"] == "unknown" and one["family_id"] is None
    card_id = one["cost_review"]["id"]

    flange = {"size": "150", "class": "300", "material": "F316", "facing": "RF"}
    r = await client.post(f"{API}/products/configurations", headers=h, json={
        "family_id": fam["id"], "enquiry_line_id": one["enquiry_line_id"], "values": flange})
    assert r.status_code == 201, r.text
    got = (await client.get(f"{Q}/{q['id']}", headers=h)).json()["active"]["lines"][0]
    assert got["family_id"] == fam["id"] and got["variant_code"] == "FL-150-300-F316-RF"
    assert got["description"].startswith("pipe (") and got["cost_status"] == "complete"
    assert got["unit_cost"] == str(_flange_cost("19.1", 12, "1.5")[1])
    # The card for the unknown cost closes by itself; the gasket's stays.
    assert (await client.get(f"{API}/decisions/{card_id}", headers=h)).json()["status"] \
        == "superseded"
    async with tenant_session(tid) as s:
        kinds = list(await s.scalars(select(AuditRecord.kind).where(
            AuditRecord.record_id == uuid.UUID(q["id"]))))
        assert "quote.line.configured" in kinds

    # A sent revision never changes, whatever happens to the enquiry.
    async with tenant_session(tid) as s:
        quote = await s.get(Quote, uuid.UUID(q["id"]))
        rev = await s.scalar(select(QuoteRevision).where(QuoteRevision.quote_id == quote.id))
        rev.status, quote.status = "sent", "sent"
    r = await client.post(f"{API}/products/configurations", headers=h, json={
        "family_id": fam["id"], "enquiry_line_id": one["enquiry_line_id"],
        "values": {**flange, "material": "A105"}})
    assert r.status_code == 201, r.text
    after = (await client.get(f"{Q}/{q['id']}", headers=h)).json()["active"]["lines"][0]
    assert after["variant_code"] == "FL-150-300-F316-RF"
    assert after["unit_cost"] == got["unit_cost"]


async def test_missing_rates_make_costs_incomplete_until_they_are_added(client, ctx):  # noqa: F811
    h = ctx["h"]
    fam, q = await _flanges_quote(client, h, [
        ("Weld neck flange", 10, {"size": "100", "class": "150", "material": "LF2",
                                  "facing": "RF"})])
    # A second model version adds a coating whose material has no rate yet.
    model = (await client.get(f"{C}/models/{fam['id']}", headers=h)).json()
    m = model["current"]["model"]
    m["materials"].append({"label": "Coating", "code": "PAINT-EP", "qty": "0.05", "unit": "l"})
    chk = (await client.post(f"{C}/models/{fam['id']}/check", headers=h,
                             json={"model": m, "values": {"size": "100", "class": "150",
                                                          "material": "LF2", "facing": "RF"}})
           ).json()
    assert chk["problems"] == [] and chk["warnings"] == ["Material PAINT-EP has no rate yet"]
    assert chk["preview"]["status"] == "incomplete"
    _, viewer = await _user_with_role(client, ctx, "Cost Viewer", ["costs.view"])
    assert (await client.put(f"{C}/models/{fam['id']}", headers=viewer,
                             json={"model": m})).status_code == 403
    bad = await client.put(f"{C}/models/{fam['id']}", headers=h, json={
        "model": {**m, "overhead_pct": "overhead + 1"}})
    assert bad.status_code == 422 and "overhead is not an attribute" in bad.text
    r = await client.put(f"{C}/models/{fam['id']}", headers=h,
                         json={"model": m, "note": "Epoxy coating on every flange"})
    assert r.status_code == 200 and r.json()["version"] == 2

    # Saving the model worked the draft out again (it used version 1).
    line = (await client.get(f"{Q}/{q['id']}", headers=h)).json()["active"]["lines"][0]
    assert line["cost_status"] == "incomplete" and line["cost"]["model"]["version"] == 2
    assert line["cost"]["problems"] == [
        f"Material Coating: material PAINT-EP has no rate on {date.today():%d %b %Y}"]
    assert line["cost"]["missing"] == [{"kind": "material", "code": "PAINT-EP"}]
    assert Decimal(line["unit_cost"]) > 0  # worked out from what is known
    card = line["cost_review"]
    assert card["status"] == "pending"

    # The rate arrives (per litre, converted to m³): the draft is worked out again at
    # once, the card closes by itself, and the person who added it sees the quote (K9).
    r = await client.post(f"{C}/materials", headers=h, json={
        "code": "PAINT-EP", "name": "Epoxy paint", "unit": "litre", "rate": "640",
        "effective_from": date.today().isoformat()})
    assert r.status_code == 201, r.text
    assert r.json()["rate"]["unit"] == "m3" and r.json()["rate"]["rate"] == "640000"
    assert [x["number"] for x in r.json()["recosted"]] == [q["number"]]
    line = (await client.get(f"{Q}/{q['id']}", headers=h)).json()["active"]["lines"][0]
    assert line["cost_status"] == "complete" and line["cost_review"] is None
    coat = next(p for p in line["cost"]["parts"] if p["label"] == "Coating")
    assert coat["amount"] == "32.00" and coat["detail"].startswith("0.00005 m³ of PAINT-EP")
    assert line["cost"]["conversions"][0]["from"] == "l"
    old = (await client.get(f"{API}/decisions/{card['id']}", headers=h)).json()
    assert old["status"] == "superseded"


async def test_sent_quotes_are_flagged_when_costs_move(client, ctx):  # noqa: F811
    h, tid = ctx["h"], ctx["tid"]
    fam, q = await _flanges_quote(client, h, [
        ("Weld neck flange", 40, {"size": "150", "class": "300", "material": "F316",
                                  "facing": "RF"})])
    async with tenant_session(tid) as s:
        quote = await s.get(Quote, uuid.UUID(q["id"]))
        rev = await s.scalar(select(QuoteRevision).where(QuoteRevision.quote_id == quote.id))
        rev.status, quote.status = "sent", "sent"
        quote.owner_id = await s.scalar(select(User.id).where(User.email.like("admin@%")))

    # Dated ahead: nothing moves yet. In force today: the sent quote is flagged.
    later = await client.post(f"{C}/materials", headers=h, json={
        "code": "F316", "name": "Stainless forging F316", "rate": "430", "yield_pct": "85",
        "scrap_rate": "140", "effective_from": (date.today() + timedelta(days=3)).isoformat()})
    assert later.json()["applies_now"] is False and later.json()["flagged"] == []
    now = await client.post(f"{C}/materials", headers=h, json={
        "code": "F316", "name": "Stainless forging F316", "rate": "425", "yield_pct": "85",
        "scrap_rate": "140", "effective_from": date.today().isoformat()})
    flagged = now.json()["flagged"]
    assert [x["number"] for x in flagged] == [q["number"]]
    assert flagged[0]["reasons"] == ["Line 1: Material F316: ₹410 → ₹425 per kg"]
    got = (await client.get(f"{Q}/{q['id']}", headers=h)).json()
    assert got["active"]["reprice_reason"] == "Line 1: Material F316: ₹410 → ₹425 per kg"
    assert got["active"]["lines"][0]["unit_cost"] == q["active"]["lines"][0]["unit_cost"]
    listed = (await client.get(Q, headers=h, params={"reprice": "true"})).json()["quotes"]
    assert [x["number"] for x in listed] == [q["number"]]

    # The hourly check finds nothing new; the owner gets one email for the day.
    async with tenant_session(tid) as s:
        assert await costing.refresh(s, tid, costing.ACTOR) == {"recosted": [], "flagged": []}
        assert await costing.email_alerts(s, tid) == 1
        assert await costing.email_alerts(s, tid) == 0
        alerts = list(await s.scalars(select(CostAlert)))
        assert len(alerts) == 1 and alerts[0].emailed_at is not None
        msg = await s.scalar(select(Message).where(Message.purpose == "staff").order_by(
            Message.created_at.desc()))
        assert msg.subject.startswith("Costs changed on 1 of your sent quote")
        assert f"{q['number']} R1: Line 1: Material F316" in msg.body


# ERPNext BOMs (K7, K11 A) -----------------------------------------------------------------

async def test_an_item_is_costed_from_its_erpnext_bom_and_bom_changes_flag_quotes(
        client, ctx):  # noqa: F811
    h, tid = ctx["h"], ctx["tid"]
    fake = FakeERPNext()
    seed_demo(fake)
    erp = await _connect(tid, fake)
    try:
        await sync_all(erp)
        today = date.today().isoformat()
        for code, unit, rate in (("RM-EN8-BAR-45", "tonne", "78000"),
                                 ("RM-CI-HOUSING-110", "nos", "4200"),
                                 ("BRG-6205", "nos", "180")):
            assert (await client.post(f"{C}/materials", headers=h, json={
                "code": code, "name": code, "unit": unit, "rate": rate,
                "effective_from": today})).status_code == 201
        # ERPNext's valuation and last purchase rates are shown next to a material that is
        # an ERPNext item, for reference only (K5 A).
        assert (await client.post(f"{C}/materials", headers=h, json={
            "code": "MTR-7K5", "name": "Motor", "unit": "nos", "rate": "21000",
            "effective_from": today})).status_code == 201
        mats = {m["code"]: m for m in (await client.get(f"{C}/materials", headers=h)).json()[
            "materials"]}
        ref = mats["MTR-7K5"]["erpnext"]
        assert ref["uom"] == "Nos" and Decimal(ref["valuation_rate"]) > 0 \
            and Decimal(ref["last_purchase_rate"]) > 0
        assert mats["RM-EN8-BAR-45"]["erpnext"] is None

        e = (await client.post(E, headers=h, json={
            "customer": {"company": "Deccan Gears"},
            "lines": [{"description": "Gearbox", "qty": 2, "unit": "nos"}]})).json()
        q = (await client.post(Q, headers=h, json={"enquiry_id": e["id"]})).json()
        line = q["active"]["lines"][0]
        r = await client.patch(f"{Q}/{q['id']}/lines/{line['id']}", headers=h,
                               json={"item_code": "GBX-110"})
        line = r.json()["active"]["lines"][0]
        cost = line["cost"]
        # The BOM covers the materials and the operations, but the line belongs to no
        # family, so the factory overhead and packing are missing: the cost is incomplete
        # and a person reviews it on a card (J3 C).
        assert line["cost_source"] == "bom" and line["cost_status"] == "incomplete", cost
        assert cost["problems"] == ["BOM BOM-GBX-110-001: the line has no product family, so "
                                    "its factory overhead and packing are missing"]
        card = (await client.get(f"{API}/decisions/{line['cost_review']['id']}",
                                 headers=h)).json()
        assert card["status"] == "pending" and card["title"].startswith(
            f"Review the incomplete cost of {q['number']} R1, line 1")
        # Housing 4,200; two shafts from their own BOM: 6.4 kg of bar at ₹78/kg, 36 min
        # turning at ₹650/h and 12 min milling at ₹900/h (ERPNext's workstations); four
        # bearings at ₹180; 90 min assembly at ₹300/h.
        parts = {p["label"]: p["amount"] for p in cost["parts"]}
        assert parts == {"RM-CI-HOUSING-110": "4200.00", "RM-EN8-BAR-45 (SFT-40)": "499.20",
                         "Turning (SFT-40)": "390.00", "Keyway milling (SFT-40)": "180.00",
                         "BRG-6205": "720.00", "Assembly": "450.00"}
        assert line["unit_cost"] == "6439.20"
        assert [(b["code"], b["level"]) for b in cost["boms"]] == [
            ("BOM-GBX-110-001", 0), ("BOM-SFT-40-001", 1)]
        assert {r["source"] for r in cost["rates"] if r["kind"] == "machine"} == {"erpnext"}
        async with tenant_session(tid) as s:
            assert await s.scalar(select(func.count()).select_from(ErpBomVersion)) == 2

        # Sent, then the BOM changes in ERPNext: a new version is kept, the old one stays,
        # and the quote is marked "reprice recommended" (K11 A).
        async with tenant_session(tid) as s:
            rev = await s.scalar(select(QuoteRevision).where(
                QuoteRevision.quote_id == uuid.UUID(q["id"])))
            rev.status = "sent"
            (await s.get(Quote, uuid.UUID(q["id"]))).status = "sent"
        fake.tick(600)
        fake.touch("BOM", "BOM-GBX-110-001", quantity=2.0)
        await sync_all(erp)
        async with tenant_session(tid) as s:
            versions = list(await s.scalars(select(ErpBomVersion).where(
                ErpBomVersion.bom_code == "BOM-GBX-110-001")))
            assert len(versions) == 2
            rev = await s.scalar(select(QuoteRevision).where(
                QuoteRevision.quote_id == uuid.UUID(q["id"])))
            assert rev.reprice_reason == "Line 1: The ERPNext BOM of GBX-110 changed " \
                                         "(BOM-GBX-110-001)"
            line_row = await s.scalar(select(QuoteLine).where(QuoteLine.revision_id == rev.id))
            assert line_row.unit_cost == Decimal("6439.2")  # a sent revision never changes
    finally:
        use_transport(erp.url, None)


async def test_a_rate_card_covers_a_family_without_materials(client, ctx):  # noqa: F811
    h = ctx["h"]
    fam, q = await _flanges_quote(client, h, [
        ("Blind flange", 4, {"size": "50", "class": "150", "material": "A105",
                             "facing": "FF"})])
    m = (await client.get(f"{C}/models/{fam['id']}", headers=h)).json()["current"]["model"]
    card_only = {k: v for k, v in m.items() if k not in ("materials", "operations")}
    r = await client.put(f"{C}/models/{fam['id']}", headers=h, json={"model": card_only})
    assert r.status_code == 200, r.text
    line = (await client.get(f"{Q}/{q['id']}", headers=h)).json()["active"]["lines"][0]
    assert line["cost_source"] == "rate_card" and line["cost_status"] == "complete"
    assert line["cost"]["parts"] == [{"group": "rate_card", "label": "Rate card",
                                      "detail": "2.7 kg at ₹165 per kg", "amount": "445.50"}]
    assert line["unit_cost"] == "445.50"
    versions = (await client.get(f"{C}/models/{fam['id']}", headers=h)).json()["versions"]
    assert [(v["version"], v["full"], v["rate_card"]) for v in versions] == [
        (2, False, True), (1, True, True)]


def test_money_in_words():
    assert costing.rs(Decimal("123456.5")) == "₹1,23,456.50"
    assert costing.rs(Decimal("410")) == "₹410" and costing.rs(Decimal("-12.345")) == "-₹12.35"
    assert costing.rs(Decimal("9999999")) == "₹99,99,999"

