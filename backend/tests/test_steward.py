"""Data Steward (F12.1) end to end through the API and the outbox."""

import uuid

import pytest
from sqlalchemy import select

from app.db import get_sessionmaker, tenant_session
from app.events import outbox
from app.models import Account, Base, Contact, ImportBatch, Tenant
from app.steward import handlers, importer
from app.steward.identifiers import gstin_check_digit
from app.steward.merge import OWN_ROWS, REFERENCES
from tests.conftest import login

API = "/api/v1"


def gstin(state: str, pan: str) -> str:
    first = f"{state}{pan}1Z"
    return first + gstin_check_digit(first)


@pytest.fixture(autouse=True)
def steward_handlers():
    outbox.clear_handlers()
    handlers.register()
    yield
    outbox.clear_handlers()


async def drain():
    while await outbox.dispatch_batch():
        pass


@pytest.fixture
async def ctx(client, make_tenant):
    slug, email, pw = await make_tenant()
    async with get_sessionmaker()() as s:
        tid = (await s.scalar(select(Tenant).where(Tenant.slug == slug))).id
    await drain()
    return {"h": await login(client, slug, email, pw), "tid": tid}


async def test_create_validates_and_derives(client, ctx):
    bad = await client.post(f"{API}/accounts", headers=ctx["h"],
                            json={"name": "Acme", "gstin": "27AAPFU0939F1ZX"})
    assert bad.status_code == 422
    assert "check digit" in bad.json()["error"]["problems"][0]

    r = await client.post(f"{API}/accounts", headers=ctx["h"], json={
        "name": "Acme Pumps Pvt Ltd", "gstin": "27aapfu0939f1zv", "phone": "098765 43210",
        "website": "https://www.acmepumps.in"})
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["gstin"] == "27AAPFU0939F1ZV" and a["pan"] == "AAPFU0939F"
    assert a["state"] == "Maharashtra" and a["phone"] == "+919876543210"
    assert a["domain"] == "acmepumps.in"
    assert 0 < a["hygiene_score"] <= 100

    detail = (await client.get(f"{API}/accounts/{a['id']}", headers=ctx["h"])).json()
    prov = {p["field"]: p for p in detail["provenance"]}
    assert prov["gstin"]["source"] == "manual" and prov["gstin"]["verified_at"]


async def test_steward_auto_merges_same_gstin_and_undo_restores(client, ctx):
    h, g = ctx["h"], gstin("27", "AAACS1234C")
    a = (await client.post(f"{API}/accounts", headers=h, json={
        "name": "Shree Ganesh Engineering Works Pvt Ltd", "gstin": g, "city": "Pune"})).json()
    contact = (await client.post(f"{API}/contacts", headers=h, json={
        "full_name": "Rajesh Kumar", "email": "rajesh@sgew.in", "account_id": a["id"]})).json()
    b = (await client.post(f"{API}/accounts", headers=h, json={
        "name": "Shri Ganesh Engg. Works", "gstin": g, "phone": "+91 20 2712 3456"})).json()
    await drain()

    ra = (await client.get(f"{API}/accounts/{a['id']}", headers=h)).json()["account"]
    rb = (await client.get(f"{API}/accounts/{b['id']}", headers=h)).json()["account"]
    survivor, merged = (ra, rb) if ra["status"] == "active" else (rb, ra)
    assert merged["status"] == "merged" and merged["merged_into_id"] == survivor["id"]
    assert survivor["phone"] == "+912027123456" and survivor["city"] == "Pune"  # both kept
    c = (await client.get(f"{API}/contacts/{contact['id']}", headers=h)).json()
    assert c["account_id"] == survivor["id"]

    merges = (await client.get(f"{API}/steward/merges", headers=h)).json()
    assert len(merges) == 1 and merges[0]["automatic"] is True

    undo = await client.post(f"{API}/steward/merges/{merges[0]['id']}/undo", headers=h)
    assert undo.status_code == 200, undo.text
    ra = (await client.get(f"{API}/accounts/{a['id']}", headers=h)).json()["account"]
    rb = (await client.get(f"{API}/accounts/{b['id']}", headers=h)).json()["account"]
    assert ra["status"] == rb["status"] == "active"
    assert ra["phone"] is None and ra["city"] == "Pune"
    assert rb["phone"] == "+912027123456"
    c = (await client.get(f"{API}/contacts/{contact['id']}", headers=h)).json()
    assert c["account_id"] == a["id"]

    # The undone pair is remembered as "not a duplicate" and is not merged again.
    await client.patch(f"{API}/accounts/{a['id']}", headers=h, json={"industry": "Pumps"})
    await drain()
    ra = (await client.get(f"{API}/accounts/{a['id']}", headers=h)).json()["account"]
    assert ra["status"] == "active"
    assert len((await client.get(f"{API}/steward/merges", headers=h)).json()) == 1


async def test_review_queue_manual_merge_with_field_choice(client, ctx):
    h = ctx["h"]
    a = (await client.post(f"{API}/accounts", headers=h, json={
        "name": "Bharat Forge Limited", "city": "Pune", "industry": "Forgings"})).json()
    b = (await client.post(f"{API}/accounts", headers=h, json={
        "name": "Bharath Forge Ltd", "city": "Pune", "industry": "Automotive"})).json()
    await drain()
    cands = (await client.get(f"{API}/steward/candidates", headers=h)).json()
    assert len(cands) == 1 and cands[0]["band"] == "review"
    detail = (await client.get(f"{API}/steward/candidates/{cands[0]['id']}", headers=h)).json()
    assert "industry" in detail["suggested_choices"]

    r = await client.post(f"{API}/steward/candidates/{cands[0]['id']}/merge", headers=h,
                          json={"survivor_id": a["id"], "field_choices": {"industry": "merged"}})
    assert r.status_code == 200, r.text
    ra = (await client.get(f"{API}/accounts/{a['id']}", headers=h)).json()
    assert ra["account"]["industry"] == "Automotive"
    assert ra["merges"][0]["automatic"] is False
    assert (await client.get(f"{API}/accounts/{b['id']}", headers=h)).json()["account"][
        "status"] == "merged"


async def test_not_duplicate_is_never_asked_again(client, ctx):
    h = ctx["h"]
    await client.post(f"{API}/accounts", headers=h, json={"name": "Sai Industries",
                                                          "city": "Nashik"})
    b = (await client.post(f"{API}/accounts", headers=h, json={"name": "Sai Industries",
                                                               "city": "Rajkot"})).json()
    await drain()
    cand = (await client.get(f"{API}/steward/candidates", headers=h)).json()[0]
    r = await client.post(f"{API}/steward/candidates/{cand['id']}/not-duplicate", headers=h)
    assert r.status_code == 200
    await client.patch(f"{API}/accounts/{b['id']}", headers=h, json={"industry": "Castings"})
    await drain()
    assert (await client.get(f"{API}/steward/candidates", headers=h)).json() == []


async def test_different_pan_is_not_a_candidate(client, ctx):
    h = ctx["h"]
    await client.post(f"{API}/accounts", headers=h, json={
        "name": "Precision Engineering Works", "gstin": gstin("27", "AAACP1111P")})
    await client.post(f"{API}/accounts", headers=h, json={
        "name": "Precision Engineering Works", "gstin": gstin("33", "AAACP2222P")})
    await drain()
    assert (await client.get(f"{API}/steward/candidates", headers=h)).json() == []


async def test_check_finds_duplicates_before_saving(client, ctx):
    h, g = ctx["h"], gstin("29", "AAACK1111K")
    await client.post(f"{API}/accounts", headers=h, json={"name": "Kirloskar Pumps Ltd",
                                                          "gstin": g})
    r = (await client.post(f"{API}/accounts/check", headers=h, json={
        "name": "Kirloskar Pumps Limited", "gstin": gstin("27", "AAACK1111K")})).json()
    assert r["matches"] and r["matches"][0]["band"] == "auto_merge"


async def test_enrichment_fill_confirm_propose_accept(client, ctx):
    h = ctx["h"]
    a = (await client.post(f"{API}/accounts", headers=h, json={
        "name": "Zenith Polymers", "email": "info@zenith.in"})).json()

    async def enrich(field, value, source="signature"):
        r = await client.post(f"{API}/steward/enrich", headers=h, json={
            "record_type": "account", "record_id": a["id"], "field": field, "value": value,
            "source": source, "source_ref": "email:123", "confidence": 0.8})
        return r.json()["result"]

    assert await enrich("phone", "9876501234") == "filled"
    assert await enrich("email", "INFO@zenith.in") == "confirmed"
    assert await enrich("email", "sales@zenith.in") == "proposed"
    assert await enrich("gstin", "BAD") == "invalid"
    detail = (await client.get(f"{API}/accounts/{a['id']}", headers=h)).json()
    assert detail["account"]["phone"] == "+919876501234"
    prop = detail["proposals"][0]
    assert prop["value"] == "sales@zenith.in" and prop["source"] == "signature"
    r = await client.post(f"{API}/steward/proposals/{prop['id']}/accept", headers=h)
    assert r.json()["status"] == "accepted"
    detail = (await client.get(f"{API}/accounts/{a['id']}", headers=h)).json()
    assert detail["account"]["email"] == "sales@zenith.in" and detail["proposals"] == []


async def test_import_migration_mode(client, ctx):
    h, tid = ctx["h"], ctx["tid"]
    g1, g2 = gstin("24", "AAACM1234M"), gstin("27", "AAACN5678N")
    csv_text = (
        "Company Name,GSTIN,City,Mobile,Pincode\n"
        f"Mehta Castings Pvt Ltd,{g1},Rajkot,9825012345,360003\n"
        f"Mehta Castings Private Limited,{g1},Rajkot,,360003\n"
        f"Nova Hydraulics,{g2},Pune,,411019\n"
        "Nova Hydraulic Systems,,Pune,,411019\n"
        "Orbit Fasteners,27AAAAA0000A1Z0,Mumbai,123,40001\n"
        ",,,,\n"
    )
    r = await client.post(f"{API}/steward/imports", headers=h, data={"record_type": "account"},
                          files={"file": ("legacy.csv", csv_text.encode(), "text/csv")})
    assert r.status_code == 202, r.text
    async with tenant_session(tid) as s:
        batch = await s.get(ImportBatch, uuid.UUID(r.json()["id"]))
        await importer.run_import(s, batch)
    b = (await client.get(f"{API}/steward/imports/{r.json()['id']}", headers=h)).json()
    assert b["status"] == "done"
    assert b["created_count"] == 5 and b["error_count"] == 1  # the empty row
    assert b["auto_merged_count"] == 1  # Mehta (same GSTIN)
    assert b["review_count"] == 1  # Nova: one row has no GSTIN
    kept = [e for e in b["errors"] if e.get("kept")]
    assert kept and "Orbit" not in str(kept)  # problems are listed by row number
    dash = (await client.get(f"{API}/steward/hygiene", headers=h)).json()
    assert dash["accounts"]["active"] == 4 and dash["accounts"]["with_invalid_values"] >= 1


async def test_permissions_enforced(client, ctx):
    r = await client.get(f"{API}/steward/candidates")
    assert r.status_code == 401


def test_every_reference_to_accounts_and_contacts_is_registered():
    """A new table pointing at accounts/contacts must be registered so merges re-point it."""
    registered = {(m.__tablename__, c) for refs in REFERENCES.values() for m, c in refs}
    missing = []
    for table in Base.metadata.sorted_tables:
        for fk in table.foreign_keys:
            target = fk.column.table.name
            if target in ("accounts", "contacts") and fk.parent.name != "merged_into_id":
                if (table.name, fk.parent.name) not in registered | OWN_ROWS:
                    missing.append(f"{table.name}.{fk.parent.name}")
    assert missing == [], f"register these with app.steward.merge.register_reference: {missing}"


async def test_contacts_same_email_merge(client, ctx):
    h = ctx["h"]
    await client.post(f"{API}/contacts", headers=h, json={"full_name": "Anita Desai",
                                                          "email": "anita@kpl.in"})
    await client.post(f"{API}/contacts", headers=h, json={"full_name": "Anita R Desai",
                                                          "email": "Anita@KPL.in",
                                                          "designation": "Purchase Head"})
    await drain()
    active = (await client.get(f"{API}/contacts", headers=h)).json()
    assert len(active) == 1 and active[0]["designation"] == "Purchase Head"
    assert Account and Contact  # models imported for metadata registration


async def test_other_state_registration_becomes_branch_site(client, ctx):
    h = ctx["h"]
    g_mh, g_ka = gstin("27", "AAACK2222K"), gstin("29", "AAACK2222K")
    a = (await client.post(f"{API}/accounts", headers=h, json={
        "name": "Kirloskar Valves Ltd", "gstin": g_mh, "city": "Pune"})).json()
    b = (await client.post(f"{API}/accounts", headers=h, json={
        "name": "Kirloskar Valves Limited", "gstin": g_ka, "city": "Bengaluru",
        "address_line1": "Peenya Phase 2"})).json()
    await drain()
    accs = {x["id"]: x for x in (await client.get(f"{API}/accounts", headers=h)).json()}
    entity = next(x for x in accs.values() if x["kind"] == "entity")
    site = next(x for x in accs.values() if x["kind"] == "site")
    other_gstin = g_ka if entity["gstin"] == g_mh else g_mh
    assert site["parent_id"] == entity["id"] and site["gstin"] == other_gstin
    assert site["site_type"] == "branch"

    merge_id = (await client.get(f"{API}/steward/merges", headers=h)).json()[0]["id"]
    r = (await client.post(f"{API}/steward/merges/{merge_id}/undo", headers=h)).json()
    assert any("branch site" in n for n in r["undo_notes"])
    active = (await client.get(f"{API}/accounts", headers=h)).json()
    assert sorted(x["id"] for x in active) == sorted([a["id"], b["id"]])
