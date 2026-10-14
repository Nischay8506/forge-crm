"""F3.1 the product configurator (decision C4 A): options and rules checked before they
are saved, a guided configurator that only offers what still leads to a valid product,
refusal of anything invalid through the API, variant codes and fingerprints, and the
"reprice recommended" flag when an ERP item or the rules change."""

import copy
import uuid

import pytest
from sqlalchemy import select

from app.cpq.configurator import Model, ModelProblems, fingerprint
from app.cpq.samples import FLANGES
from app.db import tenant_session
from app.integrations.connections import use_transport
from app.integrations.testing.fake_erpnext import FakeERPNext
from app.integrations.testing.seed import seed_demo
from app.models import Configuration, SyncFlow
from tests.test_agents import API, _user_with_role, ctx  # noqa: F401
from tests.test_enquiries import E, _enquiry, _seller

P = f"{API}/products"


def flanges(**changes):
    m = copy.deepcopy(FLANGES)
    m.update(changes)
    return m


def problems_of(data) -> list[str]:
    try:
        m = Model.parse(data)
    except ModelProblems as exc:
        return exc.problems
    return m.check()


# The checker on its own -----------------------------------------------------------------

def test_the_sample_is_sound_and_only_valid_choices_are_offered():
    m = Model.parse(FLANGES)
    assert m.check() == []
    st = m.state({"class": "2500"})
    size = next(a for a in st.attributes if a["key"] == "size")
    blocked = {o["value"]: o["why"] for o in size["options"] if not o["available"]}
    assert set(blocked) == {"350", "400", "450", "500", "600"}
    assert blocked["400"] == "Class 2500 flanges are made only up to 12 inch (rule R-3)"
    facing = next(a for a in st.attributes if a["key"] == "facing")
    assert [o["value"] for o in facing["options"] if o["available"]] == ["RTJ"]
    assert "Flat face is made only for Class 150 (rule R-1)" in \
        next(o["why"] for o in facing["options"] if o["value"] == "FF")
    assert not st.complete and st.valid is False and st.problems == []
    # Availability of a picked attribute is shown against the other choices, so a person
    # can see what else would fit.
    st = m.state({"class": "150", "facing": "FF"})
    cls = next(a for a in st.attributes if a["key"] == "class")
    assert [o["value"] for o in cls["options"] if o["available"]] == ["150"]


def test_walking_the_guided_questions_never_reaches_a_dead_end():
    m = Model.parse(FLANGES)
    order = ["material", "size", "class", "facing"]
    starts = [(a["key"], o["value"]) for a in m.state({}).attributes for o in a["options"]]
    for key, value in starts:
        picked = {key: value}
        for k in order:
            if k in picked:
                continue
            st = m.state(picked)
            offered = [o["value"] for o in next(a for a in st.attributes if a["key"] == k)
                       ["options"] if o["available"]]
            assert offered, (picked, k)
            picked[k] = offered[-1]
        st = m.state(picked)
        assert st.valid and st.result is not None, picked


def test_a_valid_product_gets_a_variant_code_and_the_same_fingerprint_every_time():
    m = Model.parse(FLANGES)
    a = m.configure("fam-1", {"size": "150", "class": "300", "material": "F316", "facing": "RF"})
    b = Model.parse(flanges()).configure(
        "fam-1", {"facing": "RF", "material": "F316", "class": "300", "size": "150"})
    assert a.variant_code == "FL-150-300-F316-RF"
    assert a.fingerprint == b.fingerprint == fingerprint("fam-1", a.values)
    assert [x["text"] for x in a.labels] == ['6" (150 NB)', "Class 300",
                                             "Stainless ASTM A182 F316", "Raised face"]
    assert m.configure("fam-2", a.values).fingerprint != a.fingerprint  # another family


def test_the_api_level_refuses_anything_the_rules_do_not_allow():
    m = Model.parse(FLANGES)
    with pytest.raises(ModelProblems) as exc:
        m.configure("f", {"size": "400", "class": "2500", "material": "A105", "facing": "RF"})
    assert exc.value.problems == [
        "Class 900 and above need a ring-type joint face (rule R-2); Class 2500 flanges are "
        "made only up to 12 inch (rule R-3)"]
    with pytest.raises(ModelProblems) as exc:
        m.configure("f", {"size": "150", "class": "300", "material": "Brass"})
    assert exc.value.problems == ["Material has no option 'Brass'", "Pick the facing"]


def test_conflicting_rules_and_dead_ends_are_caught_before_saving():
    clash = flanges(rules=[*FLANGES["rules"],
                           {"kind": "requires", "when": [{"attr": "material", "in": ["LF2"]}],
                            "then": {"attr": "class", "in": ["150"]},
                            "message": "LF2 only in Class 150"},
                           {"kind": "excludes", "when": [{"attr": "material", "in": ["LF2"]}],
                            "then": {"attr": "class", "in": ["150"]},
                            "message": "No LF2 in Class 150"}])
    got = problems_of(clash)
    assert len(got) == 1 and got[0].startswith("Material 'Low-temperature ASTM A350 LF2' can "
                                               "never be chosen: It leaves no pressure class")
    assert "LF2 only in Class 150 (rule R-5)" in got[0] and "(rule R-6)" in got[0]
    # Every flange ruled out.
    nothing = flanges(rules=[{"kind": "requires", "when": [{"attr": "size", "in": [v]}],
                              "then": {"attr": "class", "in": ["9999"]}, "message": "x"}
                             for v in ("15",)])
    assert problems_of(nothing) == ["Rule R-1: Pressure class has no option '9999'"]
    everything = flanges(rules=[
        {"kind": "only_when", "then": {"attr": "facing", "in": [f]},
         "when": [{"attr": "class", "in": ["9"]}], "message": "x"} for f in ("RF",)])
    assert "Pressure class has no option '9'" in problems_of(everything)[0]
    none_left = flanges(rules=[
        {"id": f"R-{i}", "kind": "excludes", "when": [{"attr": "size", "in": [s]}],
         "then": {"attr": "class", "in": ["150", "300", "600", "900", "1500", "2500"]},
         "message": f"No {s}"} for i, s in enumerate(
             [v["value"] for v in FLANGES["attributes"][0]["options"]], 1)])
    assert problems_of(none_left) == [
        "No product can be made: together the rules rule out every combination"]
    # A repeated rule, a code that leaves an attribute out, a rule without a reason.
    dup = flanges(rules=[*FLANGES["rules"], {**FLANGES["rules"][2], "id": "R-9"}],
                  code_pattern="FL-{size}-{class}-{material}")
    got = problems_of(dup)
    assert got == ["The variant code pattern leaves out Facing, so different products would "
                   "get the same code"]
    got = problems_of(flanges(rules=[*FLANGES["rules"], {**FLANGES["rules"][2], "id": "R-9"}]))
    assert got == ["Rule R-9 repeats rule R-3"]
    got = problems_of(flanges(rules=[{**FLANGES["rules"][0], "message": ""}]))
    assert got == ["Rule R-1 needs the reason a person sees when it blocks an option"]


def test_numbers_are_checked_by_range_and_optional_parts_leave_no_gap_in_the_code():
    data = {
        "code_pattern": "SP-{grade}-{length}-{coat}",
        "attributes": [
            {"key": "grade", "label": "Grade", "kind": "choice", "options": [
                {"value": "light", "label": "Light", "code": "L"},
                {"value": "heavy", "label": "Heavy", "code": "H"}]},
            {"key": "length", "label": "Length", "kind": "number", "unit": "mm",
             "min": 100, "max": 6000, "step": 10},
            {"key": "coat", "label": "Coating", "kind": "choice", "required": False,
             "options": [{"value": "galv", "label": "Galvanised", "code": "G"}]},
        ],
        "rules": [
            {"kind": "excludes", "when": [{"attr": "grade", "in": ["light"]}],
             "then": {"attr": "length", "min": 4000},
             "message": "Light spools are made up to 3990 mm"},
            {"kind": "only_when", "then": {"attr": "coat", "in": ["galv"]},
             "when": [{"attr": "length", "max": 3000}],
             "message": "The galvanising bath takes up to 3 m"},
        ],
    }
    m = Model.parse(data)
    assert m.check() == []
    st = m.state({"grade": "light"})
    length = next(a for a in st.attributes if a["key"] == "length")
    assert [(r["min"], r["max"], r["available"]) for r in length["ranges"]] == [
        ("100", "3000", True), ("3010", "3990", True), ("4000", "6000", False)]
    assert length["ranges"][2]["why"] == "Light spools are made up to 3990 mm (rule R-1)"
    assert m.state({"grade": "heavy", "length": "4500", "coat": "galv"}).problems == [
        "The galvanising bath takes up to 3 m (rule R-2)"]
    assert m.state({"length": "4505"}).problems == ["Length goes in steps of 10 mm"]
    assert m.state({"length": 7000}).problems == ["Length must be from 100 to 6000 mm"]
    got = m.configure("f", {"grade": "heavy", "length": "4500.0"})
    assert got.variant_code == "SP-H-4500" and got.values == {"grade": "heavy",
                                                              "length": "4500"}
    assert m.configure("f", {"grade": "light", "length": 2500, "coat": "galv"}
                       ).variant_code == "SP-L-2500-G"


# Through the API -------------------------------------------------------------------------

async def _manager(client, ctx):  # noqa: F811
    _, h = await _user_with_role(client, ctx, "Product Manager", [
        "products.manage", "products.configure", "enquiries.read", "enquiries.manage"])
    return h


async def _flanges(client, h) -> dict:
    r = await client.post(f"{E}/setup/families", headers=h, json={"template": "flanges"})
    assert r.status_code == 201, r.text
    return r.json()


async def test_options_are_set_up_checked_and_versioned(client, ctx):  # noqa: F811
    h = ctx["h"]
    _, reader = await _user_with_role(client, ctx, "Just Reading", ["enquiries.read"])
    assert (await client.get(f"{P}/families", headers=reader)).status_code == 403
    fam = await _flanges(client, h)
    got = (await client.get(f"{P}/families/{fam['id']}", headers=h)).json()
    assert got["options"]["version"] == 1 and len(got["options"]["rules"]) == 4
    assert got["versions"][0]["note"] == "Sample from the template"
    # A clash is refused with the reason, and "check" says the same without saving.
    model = got["options"]
    bad = {"attributes": model["attributes"], "code_pattern": model["code_pattern"],
           "rules": [*model["rules"], {"kind": "requires", "message": "Class 150 only",
                                       "when": [{"attr": "facing", "in": ["RTJ"]}],
                                       "then": {"attr": "class", "in": ["150"]}}]}
    r = await client.put(f"{P}/families/{fam['id']}/options", headers=h, json=bad)
    assert r.status_code == 422
    problems = r.json()["error"]["problems"]
    assert any(p.startswith("Pressure class 'Class 900' can never be chosen") for p in problems)
    assert (await client.post(f"{P}/families/{fam['id']}/check", headers=h, json=bad)
            ).json()["problems"] == problems
    # A sound change is a new version; the new rule gets the next id.
    ok = {**bad, "rules": [*model["rules"], {
        "kind": "excludes", "message": "No F304 in Class 1500 and up",
        "when": [{"attr": "material", "in": ["F304"]}],
        "then": {"attr": "class", "in": ["1500", "2500"]}}], "note": "F304 limits"}
    r = await client.put(f"{P}/families/{fam['id']}/options", headers=h, json=ok)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2 and r.json()["rules"][-1]["id"] == "R-5"
    listed = (await client.get(f"{P}/families", headers=h)).json()["families"]
    assert next(f for f in listed if f["id"] == fam["id"])["options"]["rules"] == 5
    audit = (await client.get(f"{API}/audit?kind=product.options.saved", headers=h)).json()
    assert len(audit["records"] if isinstance(audit, dict) else audit) == 2


async def test_a_line_is_configured_only_with_a_valid_product(client, ctx):  # noqa: F811
    h = ctx["h"]
    await _seller(client, ctx)
    fam = await _flanges(client, h)
    r = await client.post(E, headers=h, json={
        "customer": {"company": "Godavari Pipelines"}, "summary": "Flanges",
        "lines": [{"description": "Weld neck flange 6 inch class 300 RF", "qty": 40,
                   "unit": "nos"}]})
    e = r.json()
    line = e["lines"][0]
    assert line["family"] == {"id": fam["id"], "name": "Flanges", "has_options": True}
    # The guided step says what fits.
    st = (await client.post(f"{P}/families/{fam['id']}/configure", headers=h,
                            json={"values": {"class": "2500"}})).json()
    size = next(a for a in st["attributes"] if a["key"] == "size")
    assert not next(o for o in size["options"] if o["value"] == "400")["available"]
    # Anything the rules do not allow is refused, whoever sends it.
    bad = await client.post(f"{P}/configurations", headers=h, json={
        "family_id": fam["id"], "enquiry_line_id": line["id"],
        "values": {"size": "400", "class": "2500", "material": "A105", "facing": "RTJ"}})
    assert bad.status_code == 422
    assert bad.json()["error"]["problems"] == [
        "Class 2500 flanges are made only up to 12 inch (rule R-3)"]
    ok = await client.post(f"{P}/configurations", headers=h, json={
        "family_id": fam["id"], "enquiry_line_id": line["id"],
        "values": {"size": "150", "class": "300", "material": "F316", "facing": "RF"}})
    assert ok.status_code == 201, ok.text
    assert ok.json()["variant_code"] == "FL-150-300-F316-RF"
    e = await _enquiry(client, h, e["id"])
    cfg = e["lines"][0]["configuration"]
    assert cfg["variant_code"] == "FL-150-300-F316-RF" and cfg["reprice_reason"] is None
    ev = next(x for x in e["events"] if x["kind"] == "line_configured")
    assert ev["detail"] == {"line": 1, "family": "Flanges",
                            "variant_code": "FL-150-300-F316-RF"}
    # New rules that no longer allow it mark the line "configure again".
    model = (await client.get(f"{P}/families/{fam['id']}", headers=h)).json()["options"]
    stricter = {"attributes": model["attributes"], "code_pattern": model["code_pattern"],
                "rules": [*model["rules"], {
                    "kind": "excludes", "message": "F316 is made only from Class 600",
                    "when": [{"attr": "material", "in": ["F316"]}],
                    "then": {"attr": "class", "in": ["150", "300"]}}]}
    r = await client.put(f"{P}/families/{fam['id']}/options", headers=h, json=stricter)
    assert r.status_code == 200, r.text
    e = await _enquiry(client, h, e["id"])
    why = e["lines"][0]["configuration"]["reprice_reason"]
    assert why.startswith("The options of Flanges changed (version 2) and no longer allow "
                          "it: F316 is made only from Class 600 (rule R-5)")
    assert any(x["kind"] == "line_reprice" for x in e["events"])
    # A seller without the product permissions cannot configure.
    _, plain = await _user_with_role(client, ctx, "Plain Seller", [
        "enquiries.read", "enquiries.work"])
    r = await client.post(f"{P}/configurations", headers=plain, json={
        "family_id": fam["id"], "values": {}})
    assert r.status_code == 403


async def test_a_changed_erp_item_marks_its_configurations_for_repricing(client, ctx):  # noqa: F811
    from app.integrations.runner import run_sync
    from tests.test_integrations_api import no_sleep

    h, tid = ctx["h"], ctx["tid"]
    fake = FakeERPNext()
    seed_demo(fake)
    fake.add("Item", {"name": "RM-F316", "item_code": "RM-F316",
                      "item_name": "Forging F316", "item_group": "Raw Material",
                      "description": "Forging F316", "stock_uom": "Kg",
                      "gst_hsn_code": "7218", "is_stock_item": 1, "is_sales_item": 0})
    url = f"http://erp-{uuid.uuid4().hex[:8]}.test"
    use_transport(url, fake)
    try:
        r = await client.post(f"{API}/integrations/connections", headers=h, json={
            "name": "ERPNext", "base_url": url, "api_key": fake.api_key,
            "api_secret": fake.api_secret, "settings": {"time_zone": "Asia/Kolkata"}})
        assert r.status_code == 201, r.text
        async with tenant_session(tid) as s:
            flow = await s.scalar(select(SyncFlow).where(
                SyncFlow.connection_id == uuid.UUID(r.json()["id"]),
                SyncFlow.entity == "item"))
        await run_sync(tid, flow.id, sleep=no_sleep)  # the first sync flags nothing
        fam = await _flanges(client, h)
        model = (await client.get(f"{P}/families/{fam['id']}", headers=h)).json()["options"]
        for o in model["attributes"][2]["options"]:
            if o["value"] == "F316":
                o["erp_item"] = "RM-F316"
        r = await client.put(f"{P}/families/{fam['id']}/options", headers=h, json={
            "attributes": model["attributes"], "rules": model["rules"],
            "code_pattern": model["code_pattern"]})
        assert r.status_code == 200, r.text
        e = (await client.post(E, headers=h, json={
            "customer": {"company": "X"}, "lines": [{"description": "Flange"}]})).json()
        for values in ({"size": "150", "class": "300", "material": "F316", "facing": "RF"},
                       {"size": "150", "class": "300", "material": "A105", "facing": "RF"}):
            r = await client.post(f"{P}/configurations", headers=h, json={
                "family_id": fam["id"], "values": values,
                **({"enquiry_line_id": e["lines"][0]["id"]} if values["material"] == "F316"
                   else {})})
            assert r.status_code == 201, r.text
        await run_sync(tid, flow.id, sleep=no_sleep)
        async with tenant_session(tid) as s:
            assert all(c.reprice_reason is None for c in await s.scalars(select(Configuration)))
        fake.catch_up()  # the change happens after the configurations were made
        fake.touch("Item", "RM-F316", item_name="Forging F316 (new supplier)")
        await run_sync(tid, flow.id, sleep=no_sleep)
        async with tenant_session(tid) as s:
            got = {c.values["material"]: c.reprice_reason
                   for c in await s.scalars(select(Configuration))}
        assert got["A105"] is None
        assert got["F316"].startswith("ERP item RM-F316 (Forging F316 (new supplier)) changed "
                                      "on ") and got["F316"].endswith("reprice recommended")
        e = await _enquiry(client, h, e["id"])
        assert e["lines"][0]["configuration"]["reprice_reason"] == got["F316"]
        assert any(x["kind"] == "line_reprice" for x in e["events"])
    finally:
        use_transport(url, None)
