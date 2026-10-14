"""F3.2 groundwork: the formula checker used by cost models, material and machine-hour
rates with dates (decision C3 A), and the quote record (decisions C12 A, C14 A): numbered
per financial year, started from an enquiry with its configured lines, one quote per
enquiry, draft lines that can change, and costs shown only with costs.view (C17 B)."""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.cpq.formula import Formula, FormulaError, Table
from app.cpq.quotes import fy_of
from app.db import tenant_session
from app.models import AuditRecord, QuoteRevision
from tests.test_agents import API, _user_with_role, ctx  # noqa: F401
from tests.test_enquiries import E, _enquiry

Q, C = f"{API}/quotes", f"{API}/costs"


# The formula checker ---------------------------------------------------------------------

WEIGHTS = Table.parse("weights", {"keys": ["size", "class"], "rows": [
    {"key": ["150", "300"], "value": "12.5"}, {"key": [150, 2500], "value": "48"}]}, [])
ENV = {"size": "150", "class": "300", "material": "F316", "qty": Decimal(40)}


def _calc(text, env=None):
    return Formula(text).eval(env or ENV, {"weights": WEIGHTS})


def test_formulas_work_out_exactly_and_read_like_excel():
    assert _calc("lookup(weights, size, class) * 1.05") == Decimal("13.125")
    assert _calc('IF(material == "F316", 410, 95)') == 410
    assert _calc("if(size > 100 and qty >= 40, 1, 2)") == 1
    assert _calc("max(2, ceil(qty / 50))") == 2
    assert _calc("round(10 / 3, 2)") == Decimal("3.33")
    assert _calc("ceil(12.3, 0.5)") == Decimal("12.5") and _calc("floor(12.3, 5)") == 10
    assert _calc("0.1 + 0.2") == Decimal("0.3")  # exact, not 0.30000000000000004
    assert _calc("size * 2") == 300  # a choice that looks like a number is one
    assert _calc("class == 300") is True  # Python's own words work as names
    f = Formula("lookup(weights, size, class) * qty")
    assert f.names == {"size", "class", "qty"} and f.tables == {"weights": 2}


@pytest.mark.parametrize(("text", "why"), [
    ("lookup(weights, 200, class)", "Table weights has no row for size 200, class 300"),
    ("qty / (size - 150)", "It divides by zero (size - 150 is 0)"),
    ("material * 2", "material is the text 'F316', not a number"),
    ('material = "F316"', "Use == to compare (a single = is not allowed)"),
    ("Size + 1", "names are written in small letters: size"),
    ("__import__('os')", "__import__() is not a function formulas know"),
    ("(1).real", "only numbers, text in quotes, names"),
    ("[1, 2]", "only numbers, text in quotes, names"),
    ("qty ** 100", "Powers must be whole numbers from -10 to 10"),
    ("lookup(nope, size)", "There is no table called nope"),
    ("max(1,", "The formula can't be read"),
    ("", "The formula is empty"),
    ("material > 3", "Only numbers can be compared"),
])
def test_formulas_that_cannot_work_say_why(text, why):
    with pytest.raises(FormulaError) as e:
        _calc(text)
    assert why in str(e.value)


def test_tables_are_checked():
    problems: list[str] = []
    Table.parse("weights", {"keys": ["size"], "rows": [
        {"key": ["15"], "value": "1.2"}, {"key": ["15.0"], "value": "1.3"},
        {"key": ["20"], "value": "heavy"}, {"key": ["25", "x"], "value": "1"}]}, problems)
    assert sorted(problems) == sorted([
        "Table weights has 15 twice",
        "Table weights, row 3 (20): the value 'heavy' is not a number",
        "Table weights, row 4: needs 1 key value"])


def test_financial_year():
    assert fy_of(date(2026, 10, 14)) == "2026-27"
    assert fy_of(date(2027, 3, 31)) == "2026-27" and fy_of(date(2027, 4, 1)) == "2027-28"
    assert fy_of(date(2099, 6, 1)) == "2099-00"


# Rates -----------------------------------------------------------------------------------

async def test_rates_are_dated_and_never_edited(client, ctx):  # noqa: F811
    h = ctx["h"]
    today = date.today()
    r = await client.post(f"{C}/materials", headers=h, json={
        "code": "f316", "name": "Stainless forging F316", "rate": "410",
        "effective_from": (today - timedelta(days=30)).isoformat()})
    assert r.status_code == 201, r.text
    assert r.json()["rate"]["code"] == "F316" and r.json()["affected_quotes"] == []
    for rate, days in (("425", 0), ("430", 0), ("450", 10)):  # 430 corrects 425 the same day
        assert (await client.post(f"{C}/materials", headers=h, json={
            "code": "F316", "name": "Stainless forging F316", "rate": rate,
            "effective_from": (today + timedelta(days=days)).isoformat()})).status_code == 201
    got = (await client.get(f"{C}/materials", headers=h)).json()["materials"]
    f316 = next(m for m in got if m["code"] == "F316")
    assert f316["current"]["rate"] == "430" and f316["next"]["rate"] == "450"
    assert [x["rate"] for x in f316["history"]] == ["450", "430", "425", "410"]
    past = (await client.get(f"{C}/materials", headers=h, params={
        "at": (today - timedelta(days=1)).isoformat()})).json()["materials"]
    assert next(m for m in past if m["code"] == "F316")["current"]["rate"] == "410"

    bad = await client.post(f"{C}/materials", headers=h, json={
        "code": "", "name": "", "rate": "-1", "unit": "tonne", "effective_from": "soon"})
    assert bad.status_code == 422
    assert len(bad.json()["error"]["problems"]) == 5

    r = await client.post(f"{C}/machines", headers=h, json={
        "code": "CNC Lathe", "name": "CNC lathe", "hour_rate": "650",
        "effective_from": today.isoformat()})
    assert r.status_code == 201, r.text
    machines = (await client.get(f"{C}/machines", headers=h)).json()["machines"]
    assert machines[0]["code"] == "CNC Lathe" and machines[0]["current"]["hour_rate"] == "650"

    async with tenant_session(ctx["tid"]) as s:
        kinds = [a.kind for a in await s.scalars(select(AuditRecord).where(
            AuditRecord.kind.like("cost.%")))]
    assert kinds.count("cost.material_rate.added") == 4 and "cost.machine_rate.added" in kinds

    _, seller = await _user_with_role(client, ctx, "Rate Reader", ["costs.view"])
    assert (await client.get(f"{C}/materials", headers=seller)).status_code == 200
    assert (await client.post(f"{C}/machines", headers=seller, json={
        "code": "VMC", "name": "VMC", "hour_rate": "900",
        "effective_from": today.isoformat()})).status_code == 403


# Quotes ----------------------------------------------------------------------------------

async def test_a_quote_starts_from_an_enquiry_with_its_lines(client, ctx):  # noqa: F811
    h = ctx["h"]
    fam = (await client.post(f"{E}/setup/families", headers=h,
                             json={"template": "flanges"})).json()
    e = (await client.post(E, headers=h, json={
        "customer": {"company": "Konark Pipes", "city": "Vizag", "state": "Andhra Pradesh"},
        "details": {"delivery_location": "Vizag steel plant gate 3"},
        "lines": [{"description": "Weld neck flange", "qty": 40, "unit": "nos"},
                  {"description": "Gasket", "material": "CAF", "qty": 40, "unit": "nos"}]})).json()
    r = await client.post(f"{API}/products/configurations", headers=h, json={
        "family_id": fam["id"], "enquiry_line_id": e["lines"][0]["id"],
        "values": {"size": "150", "class": "300", "material": "F316", "facing": "RF"}})
    assert r.status_code == 201, r.text

    r = await client.post(Q, headers=h, json={"enquiry_id": e["id"]})
    assert r.status_code == 201, r.text
    q = r.json()
    fy = fy_of(date.today())
    assert q["number"] == f"Q/{fy}/0001" and q["status"] == "draft" and q["revision"] == 1
    act = q["active"]
    assert act["status"] == "draft" and act["destination"]["state"] == "Andhra Pradesh"
    assert date.fromisoformat(act["valid_until"]) >= date.today() + timedelta(days=14)
    one, two = act["lines"]
    assert one["variant_code"] == "FL-150-300-F316-RF" and one["qty"] == "40"
    assert one["description"].startswith("Weld neck flange (6\" (150 NB), Class 300")
    assert two["description"] == "Gasket · CAF" and two["variant_code"] is None
    assert "unit_cost" in one and q["may"]["see_costs"]  # the admin sees costs

    again = await client.post(Q, headers=h, json={"enquiry_id": e["id"]})
    assert again.status_code == 409 and "Q/" in again.json()["error"]["message"]
    e2 = (await client.post(E, headers=h, json={
        "customer": {"company": "Other"}, "lines": [{"description": "Blind flange"}]})).json()
    assert (await client.post(Q, headers=h, json={"enquiry_id": e2["id"]})).json()[
        "number"] == f"Q/{fy}/0002"

    enq = await _enquiry(client, h, e["id"])
    assert enq["quote"]["number"] == q["number"]
    assert any(x["kind"] == "quote_started" for x in enq["events"])

    # Draft lines change; a configured line keeps the configuration's description.
    r = await client.patch(f"{Q}/{q['id']}/lines/{two['id']}", headers=h,
                           json={"qty": "45.5", "unit": "nos"})
    assert r.json()["active"]["lines"][1]["qty"] == "45.5"
    r = await client.patch(f"{Q}/{q['id']}/lines/{one['id']}", headers=h,
                           json={"description": "Something else"})
    assert r.status_code == 403
    r = await client.post(f"{Q}/{q['id']}/lines", headers=h, json={
        "description": "Stud bolts", "qty": "320", "unit": "nos"})
    assert [x["position"] for x in r.json()["active"]["lines"]] == [1, 2, 3]
    r = await client.delete(f"{Q}/{q['id']}/lines/{two['id']}", headers=h)
    assert [(x["position"], x["description"]) for x in r.json()["active"]["lines"]] == [
        (1, one["description"]), (2, "Stud bolts")]
    r = await client.patch(f"{Q}/{q['id']}", headers=h, json={
        "valid_until": (date.today() - timedelta(days=1)).isoformat()})
    assert r.status_code == 400
    r = await client.patch(f"{Q}/{q['id']}", headers=h, json={
        "valid_until": (date.today() + timedelta(days=30)).isoformat(),
        "destination": {"state": "Telangana", "city": "Hyderabad", "bogus": "x"}})
    assert r.json()["active"]["destination"] == {"city": "Hyderabad", "state": "Telangana"}

    # A sent revision never changes.
    async with tenant_session(ctx["tid"]) as s:
        rev = await s.scalar(select(QuoteRevision).where(
            QuoteRevision.quote_id == uuid.UUID(q["id"])))
        rev.status = "sent"
    r = await client.post(f"{Q}/{q['id']}/lines", headers=h, json={"description": "More"})
    assert r.status_code == 400 and "make a new revision" in r.json()["error"]["message"]

    # Who sees what: costs only with costs.view (C17 B).
    _, rep = await _user_with_role(client, ctx, "Quote Reader", ["quotes.read"])
    got = (await client.get(f"{Q}/{q['id']}", headers=rep)).json()
    assert "unit_cost" not in got["active"]["lines"][0] and not got["may"]["see_costs"]
    assert (await client.post(Q, headers=rep, json={"enquiry_id": e2["id"]})).status_code == 403
    listed = (await client.get(Q, headers=rep, params={"status": "open"})).json()["quotes"]
    assert {x["number"] for x in listed} == {q["number"], f"Q/{fy}/0002"}
    assert listed[0]["enquiry"]["ref"].startswith("ENQ-")
