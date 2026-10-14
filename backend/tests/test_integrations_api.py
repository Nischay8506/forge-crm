"""F12.6 over the API: the setup wizard, connection pages, the mapping studio, the
account page's ERP tabs, the gated create, webhooks, integration alerts and tasks in
"Waiting for you", permissions, and the MCP server (decision E9)."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.v1 import integrations as api
from app.core import secrets
from app.db import get_sessionmaker, tenant_session
from app.integrations.connections import use_transport
from app.integrations.runner import run_sync, tick
from app.integrations.testing.fake_erpnext import FakeERPNext
from app.integrations.testing.seed import seed_demo
from app.models import ErpConnection, SyncFlow, Tenant
from app.steward.identifiers import gstin_check_digit
from tests.conftest import login
from tests.test_agents import decide

API = "/api/v1"


def gstin(state: str, pan: str) -> str:
    first = f"{state}{pan}1Z"
    return first + gstin_check_digit(first)


async def no_sleep(_: float) -> None:
    return None


@pytest.fixture
async def setup(client, make_tenant):
    slug, email, pw = await make_tenant()
    async with get_sessionmaker()() as s:
        tid = (await s.scalar(select(Tenant).where(Tenant.slug == slug))).id
    fake = FakeERPNext()
    seed_demo(fake, broken=True)
    url = f"http://erp-{uuid.uuid4().hex[:8]}.test"
    use_transport(url, fake)
    h = await login(client, slug, email, pw)
    yield {"h": h, "tid": tid, "fake": fake, "url": url, "slug": slug}
    use_transport(url, None)


async def _user(client, s, name: str, permissions: list[str]) -> dict[str, str]:
    role = (await client.post(f"{API}/roles", headers=s["h"],
                              json={"name": name, "permissions": permissions})).json()
    email = f"{name.lower().replace(' ', '')}@{s['slug']}.example.com"
    r = await client.post(f"{API}/users", headers=s["h"], json={
        "email": email, "full_name": name, "password": "Passw0rd!x", "role_ids": [role["id"]]})
    assert r.status_code == 201, r.text
    return await login(client, s["slug"], email, "Passw0rd!x")


async def _connect(client, s) -> dict:
    body = {"name": "ERPNext Pune", "base_url": s["url"], "api_key": s["fake"].api_key,
            "api_secret": s["fake"].api_secret, "settings": {"time_zone": "Asia/Kolkata"}}
    r = await client.post(f"{API}/integrations/connections", headers=s["h"], json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _sync(s, conn_id: str) -> None:
    async with tenant_session(s["tid"]) as db:
        flows = list(await db.scalars(select(SyncFlow).where(
            SyncFlow.connection_id == uuid.UUID(conn_id))))
    order = ["customer", "item", "price", "order", "invoice", "receipt", "stock", "workstation",
             "bom"]
    for f in sorted(flows, key=lambda f: order.index(f.entity)):
        await run_sync(s["tid"], f.id, sleep=no_sleep)


async def test_wizard_connect_sync_and_see_health(client, setup):
    s = setup
    bad = await client.post(f"{API}/integrations/test", headers=s["h"], json={
        "base_url": s["url"], "api_key": "wrong", "api_secret": "wrong"})
    assert bad.status_code == 200 and bad.json()["ok"] is False
    assert bad.json()["cause"] == "auth_rejected" and "API Access" in bad.json()["fix"]
    good = await client.post(f"{API}/integrations/test", headers=s["h"], json={
        "base_url": s["url"], "api_key": s["fake"].api_key, "api_secret": s["fake"].api_secret})
    assert good.json()["ok"] is True and good.json()["about"]["user"]

    conn = await _connect(client, s)
    raw = json.dumps(conn)
    assert s["fake"].api_secret not in raw and s["fake"].api_key not in raw
    assert conn["key_hint"] == secrets.hint(s["fake"].api_key)
    assert conn["health"] in ("ok", "problem") and len(conn["flows"]) == 9
    assert all(f["run_requested_at"] for f in conn["flows"])  # first sync asked for

    await _sync(s, conn["id"])
    detail = (await client.get(f"{API}/integrations/connections/{conn['id']}",
                               headers=s["h"])).json()
    assert detail["health"] == "ok" and detail["dead_letters"] == 1
    assert {r["entity"] for r in detail["runs"]} >= {"customer", "order", "stock"}
    overview = (await client.get(f"{API}/integrations", headers=s["h"])).json()
    assert overview["credential_keys_configured"] and overview["connections"][0]["id"] == conn["id"]

    # A dead letter can be skipped (audited) and the task closes.
    dead = (await client.get(f"{API}/integrations/connections/{conn['id']}/dead-letters",
                             headers=s["h"])).json()
    assert dead[0]["external_id"] == "SAL-ORD-2026-BROKEN"
    r = await client.post(f"{API}/integrations/dead-letters/{dead[0]['id']}/skip",
                          headers=s["h"], json={"note": "test order, ignore"})
    assert r.json()["status"] == "skipped"
    waiting = (await client.get(f"{API}/decisions?view=waiting", headers=s["h"])).json()
    assert not [d for d in waiting if d["kind"] == "task" and "could not be read" in d["title"]]


async def test_credentials_can_change_but_never_come_back(client, setup):
    s = setup
    conn = await _connect(client, s)
    s["fake"].api_key, s["fake"].api_secret = "rotated-key-9876", "rotated-secret-5432"
    r = await client.put(f"{API}/integrations/connections/{conn['id']}/credentials",
                         headers=s["h"], json={"api_key": "rotated-key-9876",
                                               "api_secret": "rotated-secret-5432"})
    assert r.status_code == 200 and r.json()["key_hint"] == "…9876"
    assert "rotated-secret-5432" not in r.text
    assert r.json()["last_check"]["ok"] is True
    audit = (await client.get(f"{API}/audit?kind=integration.credentials.changed",
                              headers=s["h"])).text
    assert "rotated-secret-5432" not in audit


async def test_alerts_and_tasks_wait_for_people_with_the_integrations_permission(client, setup):
    s = setup
    conn = await _connect(client, s)
    s["fake"].revoke()
    await tick(now=datetime.now(UTC) + timedelta(minutes=1), enqueue=lambda *a: None)
    waiting = (await client.get(f"{API}/decisions?view=waiting", headers=s["h"])).json()
    alerts = [d for d in waiting if d["kind"] == "alert"]
    assert len(alerts) == 1 and "rejected Forge's API key" in alerts[0]["title"]
    assert alerts[0]["record_name"] == "ERPNext Pune"
    assert alerts[0]["tool_title"] == "Integration problem"
    count = (await client.get(f"{API}/decisions/count", headers=s["h"])).json()
    assert count["alerts"] == 1 and count["waiting"] >= 1

    # Someone who decides agent actions but has no integrations permission sees none.
    h2 = await _user(client, s, "Approver", ["decisions.decide"])
    w2 = (await client.get(f"{API}/decisions?view=waiting", headers=h2)).json()
    assert not [d for d in w2 if d["kind"] in ("alert", "task")]

    ack = await client.post(f"{API}/decisions/{alerts[0]['id']}/acknowledge", headers=s["h"])
    assert ack.json()["status"] == "acknowledged"
    detail = (await client.get(f"{API}/integrations/connections/{conn['id']}",
                               headers=s["h"])).json()
    assert detail["health"] == "problem" and detail["problems"][0]["cause"] == "auth_rejected"


async def test_mapping_studio_and_account_tabs(client, setup):
    s = setup
    acc = (await client.post(f"{API}/accounts", headers=s["h"], json={
        "name": "Shakti Hydraulics", "city": "Faridabad"})).json()
    conn = await _connect(client, s)
    await _sync(s, conn["id"])

    review = (await client.get(f"{API}/integrations/customers?status=review",
                               headers=s["h"])).json()
    shakti = [c for c in review["items"] if c["name"] == "Shakti Hydraulics"][0]
    assert shakti["candidates"][0]["account_id"] == acc["id"]
    r = await client.post(f"{API}/integrations/customers/{shakti['id']}/link", headers=s["h"],
                          json={"account_id": acc["id"], "note": "same company"})
    assert r.json()["link_status"] == "linked" and r.json()["account_name"] == "Shakti Hydraulics"

    tabs = (await client.get(f"{API}/integrations/accounts/{acc['id']}", headers=s["h"])).json()
    assert tabs["links"][0]["external_id"] == "Shakti Hydraulics"
    assert tabs["links"][0]["credit_limit"] and tabs["links"][0]["payment_terms"] == "Net 30"
    assert tabs["orders"] and all(o["connection"] == "ERPNext Pune" for o in tabs["orders"])
    assert tabs["as_of"] is not None
    assert tabs["can_create_in"] == []  # already a customer in the only ERP

    codes = (await client.get(f"{API}/integrations/codes?status=open", headers=s["h"])).json()
    nos = [c for c in codes["items"] if c["list"] == "uom" and c["erp_code"] == "Nos"][0]
    assert nos["forge_code"] == "NOS" and nos["forge_label"] == "Numbers"
    done = await client.post(f"{API}/integrations/codes/confirm", headers=s["h"],
                             json={"ids": [nos["id"]]})
    assert done.json() == {"confirmed": 1}
    hist = (await client.get(f"{API}/integrations/codes/{nos['id']}/history",
                             headers=s["h"])).json()
    assert [h["status"] for h in hist] == ["confirmed", "suggested"]
    units = (await client.get(f"{API}/integrations/forge-codes?list=uom", headers=s["h"])).json()
    assert any(u["code"] == "KGS" for u in units)
    r = await client.post(f"{API}/integrations/forge-codes", headers=s["h"],
                          json={"list": "uom", "code": "XYZ"})
    assert r.status_code == 400  # units use the GST list


async def test_create_in_erp_needs_permission_and_refuses_duplicates(client, setup):
    s = setup
    conn = await _connect(client, s)
    await _sync(s, conn["id"])
    acc = (await client.post(f"{API}/accounts", headers=s["h"], json={
        "name": "Ratnagiri Foundry Pvt Ltd", "gstin": gstin("27", "AAHCR4444M")})).json()
    reader = await _user(client, s, "Reader", ["accounts.read", "integrations.read"])
    r = await client.post(f"{API}/integrations/accounts/{acc['id']}/create-in-erp",
                          headers=reader, json={"connection_id": conn["id"]})
    assert r.status_code == 403
    tabs = (await client.get(f"{API}/integrations/accounts/{acc['id']}", headers=s["h"])).json()
    assert tabs["can_create_in"] == [{"id": conn["id"], "name": "ERPNext Pune"}]
    r = await client.post(f"{API}/integrations/accounts/{acc['id']}/create-in-erp",
                          headers=s["h"], json={"connection_id": conn["id"]})
    assert r.status_code == 200 and r.json()["outcome"] == "done", r.text
    again = await client.post(f"{API}/integrations/accounts/{acc['id']}/create-in-erp",
                              headers=s["h"], json={"connection_id": conn["id"]})
    assert again.json()["outcome"] == "already"
    tabs = (await client.get(f"{API}/integrations/accounts/{acc['id']}", headers=s["h"])).json()
    assert tabs["links"][0]["link_method"] == "created_in_erp"
    assert tabs["writes"][0]["status"] == "succeeded"


async def test_webhook_only_nudges_the_sync(client, setup):
    s = setup
    conn = await _connect(client, s)
    token = (await client.post(f"{API}/integrations/connections/{conn['id']}/webhook-token",
                               headers=s["h"])).json()
    assert token["url"].endswith(f"/integrations/webhooks/{conn['id']}")
    async with tenant_session(s["tid"]) as db:
        for f in await db.scalars(select(SyncFlow).where(
                SyncFlow.connection_id == uuid.UUID(conn["id"]))):
            f.run_requested_at = None
    wrong = await client.post(f"{API}/integrations/webhooks/{conn['id']}",
                              headers={"X-Forge-Webhook-Token": "nope"}, json={})
    assert wrong.status_code == 403
    ok = await client.post(f"{API}/integrations/webhooks/{conn['id']}",
                           headers={"X-Forge-Webhook-Token": token["token"]},
                           json={"doctype": "Item", "name": "GBX-110", "item_name": "evil"})
    assert ok.status_code == 202 and ok.json()["entities"] == ["item"]
    async with tenant_session(s["tid"]) as db:
        nudged = [f.entity for f in await db.scalars(select(SyncFlow).where(
            SyncFlow.connection_id == uuid.UUID(conn["id"]),
            SyncFlow.run_requested_at.is_not(None)))]
        conn_row = await db.get(ErpConnection, uuid.UUID(conn["id"]))
        assert conn_row.webhook_token_hash != token["token"]
    assert nudged == ["item"]


async def test_manual_reconcile_is_handed_to_the_worker(client, setup, monkeypatch):
    s = setup
    conn = await _connect(client, s)
    handed = []
    monkeypatch.setattr(api, "enqueue", lambda *a: handed.append(a))
    r = await client.post(f"{API}/integrations/connections/{conn['id']}/sync", headers=s["h"],
                          json={"reconcile": True, "entities": ["item"]})
    assert r.json() == {"requested": ["item"]} and handed[0][0] == "reconcile"


# --- MCP (decision E9) --------------------------------------------------------------------------

async def test_mcp_tools_go_through_the_gate_and_writes_wait_for_a_person(client, setup):
    from app.integrations import mcp_server

    s = setup
    conn = await _connect(client, s)
    await _sync(s, conn["id"])
    reader = (await client.post(f"{API}/integrations/mcp-clients", headers=s["h"],
                                json={"name": "Claude Desktop"})).json()
    assert reader["token"].startswith("fmcp_")
    assert reader["token_hint"] == "…" + reader["token"][-4:]
    listed = (await client.get(f"{API}/integrations/mcp-clients", headers=s["h"])).json()
    assert "token" not in listed[0]

    rc = await mcp_server.client_for_token(reader["token"])
    found = await mcp_server.run_tool(rc, "mcp.find_accounts", {"query": "Bharat"})
    acc_id = found["accounts"][0]["id"]
    orders = await mcp_server.run_tool(rc, "mcp.account_orders", {"account_id": acc_id})
    assert orders["orders"] and orders["orders"][0]["erp"] == "ERPNext Pune"
    owed = await mcp_server.run_tool(rc, "mcp.outstanding_invoices", {"account_id": acc_id})
    assert "invoices" in owed
    with pytest.raises(Exception, match="only read"):
        await mcp_server.create_customer(rc, uuid.UUID(acc_id), uuid.UUID(conn["id"]))

    new = (await client.post(f"{API}/accounts", headers=s["h"], json={
        "name": "Kolhapur Alloys", "gstin": gstin("27", "AAKCK1212K")})).json()
    writer = (await client.post(f"{API}/integrations/mcp-clients", headers=s["h"],
                                json={"name": "Ops script", "allow_write": True})).json()
    wc = await mcp_server.client_for_token(writer["token"])
    result = await mcp_server.create_customer(wc, uuid.UUID(new["id"]), uuid.UUID(conn["id"]))
    assert result["outcome"] == "waiting" and "approve" in result["message"]
    assert not [d for d in s["fake"].docs["Customer"].values()
                if d["customer_name"] == "Kolhapur Alloys"]
    waiting = (await client.get(f"{API}/decisions?view=waiting", headers=s["h"])).json()
    card = [d for d in waiting if d["tool_key"] == "erp.create_customer"][0]
    assert card["agent_name"] == "Outside AI tools (MCP)"
    r = await decide(client, s["h"], card["id"])
    assert r.json()["status"] == "approved", r.text
    assert [d for d in s["fake"].docs["Customer"].values()
            if d["customer_name"] == "Kolhapur Alloys"]

    # Every call is in the audit chain under the outside-tools agent.
    audit = (await client.get(f"{API}/audit?agent_key=outside_ai&limit=100",
                              headers=s["h"])).json()
    tools = {a["data"].get("tool") for a in (audit["items"] if isinstance(audit, dict) else audit)}
    assert {"mcp.find_accounts", "mcp.account_orders", "erp.create_customer"} <= tools

    # Revoked tokens stop working.
    await client.post(f"{API}/integrations/mcp-clients/{reader['id']}/revoke", headers=s["h"])
    assert await mcp_server.client_for_token(reader["token"]) is None


async def test_mcp_over_http_needs_a_token(setup):
    from app.main import create_app

    s = setup
    app = create_app()
    async with app.state.mcp.session_manager.run(), AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t") as c:
        h = await login(c, s["slug"], f"admin@{s['slug']}.example.com", "Passw0rd!")
        token = (await c.post(f"{API}/integrations/mcp-clients", headers=h,
                              json={"name": "HTTP test"})).json()["token"]
        hdr = {"Accept": "application/json, text/event-stream",
               "Content-Type": "application/json"}
        r = await c.post("/mcp/", headers=hdr, json={"jsonrpc": "2.0", "id": 1,
                                                    "method": "tools/list", "params": {}})
        assert r.status_code == 401
        auth = {**hdr, "Authorization": f"Bearer {token}"}
        init = await c.post("/mcp/", headers=auth, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1"}}})
        assert init.status_code == 200, init.text
        listed = await c.post("/mcp/", headers={**auth, "Mcp-Protocol-Version": "2025-06-18"},
                              json={"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                                    "params": {}})
        assert listed.status_code == 200, listed.text
        names = {t["name"] for t in listed.json()["result"]["tools"]}
        assert names == {"find_accounts", "account_orders", "outstanding_invoices",
                         "create_erp_customer"}
        called = await c.post("/mcp/", headers={**auth, "Mcp-Protocol-Version": "2025-06-18"},
                              json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                    "params": {"name": "find_accounts",
                                               "arguments": {"query": "zz-nothing"}}})
        assert called.status_code == 200, called.text
        assert called.json()["result"]["isError"] is False
