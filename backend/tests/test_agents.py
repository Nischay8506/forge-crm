"""Agent runtime, policy gate, autonomy, decisions and the audit chain (F12.2, F12.4)."""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.agents import policy
from app.audit import chain
from app.db import get_sessionmaker, tenant_session
from app.events import outbox
from app.models import AuditRecord, SandboxAction, Tenant
from app.steward import handlers
from app.steward.identifiers import gstin_check_digit
from evals import policy_gate
from tests.conftest import OWNER_URL, login

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
    return {"h": await login(client, slug, email, pw), "tid": tid, "slug": slug}


async def _user_with_role(client, ctx, name: str, permissions: list[str]):
    role = (await client.post(f"{API}/roles", headers=ctx["h"],
                              json={"name": name, "permissions": permissions})).json()
    email = f"{name.lower().replace(' ', '')}@{ctx['slug']}.example.com"
    r = await client.post(f"{API}/users", headers=ctx["h"], json={
        "email": email, "full_name": name, "password": "Passw0rd!x", "role_ids": [role["id"]]})
    assert r.status_code == 201, r.text
    return role, await login(client, ctx["slug"], email, "Passw0rd!x")


async def _rule(client, h, **body):
    body = {"agent_key": "sandbox", "tool_key": "sandbox.record_payment", **body}
    r = await client.post(f"{API}/policies", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _try(client, h, amount, account_id=None):
    r = await client.post(f"{API}/agents/sandbox/try", headers=h,
                          json={"amount": amount, "account_id": account_id})
    assert r.status_code == 200, r.text
    return r.json()


async def _account(client, h, name, tier=None):
    r = await client.post(f"{API}/accounts", headers=h, json={"name": name, "tier": tier})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# The admin screen ------------------------------------------------------------------

async def test_agent_screen_answers_what_it_can_do(client, ctx):
    agents = (await client.get(f"{API}/agents", headers=ctx["h"])).json()
    keys = [a["key"] for a in agents]
    assert keys[0] == "data_steward" and "sandbox" in keys  # test-only agents last
    await _rule(client, ctx["h"], conditions=[{"fact": "amount", "op": "gt", "value": 100000}],
                outcome="ask_person")
    d = (await client.get(f"{API}/agents/sandbox", headers=ctx["h"])).json()
    assert d["code"] == "L2" and d["tools"][0]["title"] == "Record a test payment"
    assert d["rules"][0]["sentence"] == ("When Sandbox Agent wants to record a test payment and "
                                         "amount (INR) is more than ₹1,00,000, ask a person first.")
    assert not d["stopped"]


async def test_policy_validation_and_preview(client, ctx):
    bad = await client.post(f"{API}/policies", headers=ctx["h"], json={
        "agent_key": "data_steward", "tool_key": "sandbox.record_payment", "outcome": "block",
        "conditions": [{"fact": "amount", "op": "in", "value": "x"}]})
    assert bad.status_code == 422
    problems = bad.json()["error"]["problems"]
    assert "Data Steward cannot use 'sandbox.record_payment'" in problems
    assert "'Amount (INR)' cannot use 'in'" in problems
    p = (await client.post(f"{API}/policies/preview", headers=ctx["h"], json={
        "agent_key": "data_steward", "tool_key": "steward.merge_records", "outcome": "ask_person",
        "conditions": [{"fact": "account_tier", "op": "eq", "value": "strategic"}]})).json()
    assert p["problems"] == []
    assert p["sentence"] == ("When Data Steward wants to merge two duplicate records and account "
                             "tier is strategic, ask a person first.")


# The gate ----------------------------------------------------------------------------

async def test_rules_ask_then_block_and_approval_resumes(client, ctx):
    h = ctx["h"]
    finance, fin_h = await _user_with_role(client, ctx, "Finance", ["decisions.decide"])
    _, sales_h = await _user_with_role(client, ctx, "Sales", ["decisions.decide"])
    await _rule(client, h, conditions=[{"fact": "amount", "op": "gt", "value": 100000}],
                outcome="ask_person", approver_role_id=finance["id"])
    await _rule(client, h, conditions=[{"fact": "amount", "op": "gt", "value": 500000}],
                outcome="block")

    assert (await _try(client, h, "90000"))["status"] == "done"
    waiting = await _try(client, h, "150000")
    assert waiting["status"] == "waiting" and waiting["decision_id"]
    blocked = await _try(client, h, "600000")
    assert blocked["status"] == "blocked" and "block it" in blocked["reasons"][0]

    # Finance sees it as theirs; Sales sees it but cannot decide it.
    assert (await client.get(f"{API}/decisions/count", headers=fin_h)).json()["waiting"] == 1
    assert (await client.get(f"{API}/decisions/count", headers=sales_h)).json()["waiting"] == 0
    card = (await client.get(f"{API}/decisions", headers=sales_h)).json()[0]
    assert card["approver_role"]["name"] == "Finance" and card["can_decide"] is False
    assert {"label": "Amount (INR)", "value": "₹1,50,000", "key": "amount"} in card["facts"]
    no = await client.post(f"{API}/decisions/{waiting['decision_id']}/approve", headers=sales_h)
    assert no.status_code == 403

    ok = await client.post(f"{API}/decisions/{waiting['decision_id']}/approve", headers=fin_h,
                           json={"note": "PO checked"})
    assert ok.status_code == 200 and ok.json()["status"] == "approved", ok.text
    again = await client.post(f"{API}/decisions/{waiting['decision_id']}/approve", headers=h)
    assert again.status_code == 400  # decided once only

    notices = (await client.get(f"{API}/decisions?view=notices", headers=h)).json()
    assert [n["title"] for n in notices] == ["Blocked: Record a test payment of ₹6,00,000"]
    async with tenant_session(ctx["tid"]) as s:
        amounts = sorted(float(a) for a in await s.scalars(select(SandboxAction.amount)))
    assert amounts == [90000.0, 150000.0]


async def test_reject_writes_nothing(client, ctx):
    await client.put(f"{API}/agents/sandbox/level", headers=ctx["h"], json={"level": 0})
    w = await _try(client, ctx["h"], "10")
    assert w["status"] == "waiting"
    r = await client.post(f"{API}/decisions/{w['decision_id']}/reject", headers=ctx["h"],
                          json={"note": "not needed"})
    assert r.json()["status"] == "rejected"
    async with tenant_session(ctx["tid"]) as s:
        assert (await s.scalars(select(SandboxAction))).all() == []


async def test_l1_acts_and_notifies(client, ctx):
    await client.put(f"{API}/agents/sandbox/level", headers=ctx["h"], json={"level": 1})
    assert (await _try(client, ctx["h"], "10"))["status"] == "done"
    notices = (await client.get(f"{API}/decisions?view=notices", headers=ctx["h"])).json()
    assert len(notices) == 1 and "it acts, then tells a person" in notices[0]["reasons"][0]
    ack = await client.post(f"{API}/decisions/{notices[0]['id']}/acknowledge", headers=ctx["h"])
    assert ack.json()["status"] == "acknowledged"


async def test_tier_and_account_overrides(client, ctx):
    h = ctx["h"]
    strategic = await _account(client, h, "Tata Forgings", "strategic")
    standard = await _account(client, h, "Small Works")
    r = await client.post(f"{API}/agents/sandbox/overrides", headers=h, json={
        "scope": "account_tier", "account_tier": "strategic", "autonomy_level": 0})
    assert r.status_code == 201, r.text
    assert (await _try(client, h, "10", strategic))["status"] == "waiting"
    assert (await _try(client, h, "10", standard))["status"] == "done"
    # A site without its own tier takes its company's.
    site = await client.post(f"{API}/accounts", headers=h, json={
        "name": "Tata Forgings Pune plant", "kind": "site", "site_type": "plant",
        "parent_id": strategic})
    assert site.status_code == 201, site.text
    assert (await _try(client, h, "12", site.json()["id"]))["status"] == "waiting"
    await client.post(f"{API}/agents/sandbox/overrides", headers=h, json={
        "scope": "account", "account_id": standard, "autonomy_level": 0})
    assert (await _try(client, h, "11", standard))["status"] == "waiting"
    bad = await client.post(f"{API}/agents/sandbox/overrides", headers=h, json={
        "scope": "account_tier", "account_tier": "gold", "autonomy_level": 1})
    assert bad.status_code == 400


async def test_kill_switches_and_takeover(client, ctx):
    h = ctx["h"]
    acc = await _account(client, h, "Bharat Pumps")
    for scope, extra in (("tenant", {}), ("agent", {"agent_key": "sandbox"}),
                         ("account", {"account_id": acc})):
        ks = await client.post(f"{API}/kill-switches", headers=h,
                               json={"scope": scope, "reason": "incident", **extra})
        assert ks.status_code == 201, ks.text
        out = await _try(client, h, "10", acc)
        assert out["status"] == "blocked" and "Stopped" in out["reasons"][0]
        if scope == "agent":
            assert (await client.get(f"{API}/agents/sandbox", headers=h)).json()["stopped"]
        await client.post(f"{API}/kill-switches/{ks.json()['id']}/release", headers=h)
    assert (await _try(client, h, "10", acc))["status"] == "done"

    t = await client.post(f"{API}/takeovers", headers=h,
                          json={"record_type": "account", "record_id": acc, "reason": "VIP"})
    assert t.status_code == 201
    out = await _try(client, h, "10", acc)
    assert out["status"] == "blocked" and "taken over" in out["reasons"][0]
    dup = await client.post(f"{API}/takeovers", headers=h,
                            json={"record_type": "account", "record_id": acc})
    assert dup.status_code == 400
    await client.post(f"{API}/takeovers/{t.json()['id']}/hand-back", headers=h)
    assert (await _try(client, h, "10", acc))["status"] == "done"
    # Stops are hard blocks: nobody can approve them, so no notice either.
    assert (await client.get(f"{API}/decisions?view=notices", headers=h)).json() == []


async def test_approval_rechecks_kill_switch(client, ctx):
    h = ctx["h"]
    await client.put(f"{API}/agents/sandbox/level", headers=h, json={"level": 0})
    w = await _try(client, h, "10")
    await client.post(f"{API}/kill-switches", headers=h,
                      json={"scope": "tenant", "reason": "freeze"})
    d = (await client.post(f"{API}/decisions/{w['decision_id']}/approve", headers=h)).json()
    assert d["status"] == "failed" and d["result"]["status"] == "blocked"


# Data Steward behind the gate --------------------------------------------------------

async def test_steward_merge_waits_for_approval_at_l0(client, ctx):
    h = ctx["h"]
    await client.put(f"{API}/agents/data_steward/level", headers=h, json={"level": 0})
    g = gstin("27", "AAPFU0939F")
    a = await client.post(f"{API}/accounts", headers=h, json={"name": "Acme Pumps", "gstin": g})
    b = await client.post(f"{API}/accounts", headers=h,
                          json={"name": "ACME Pumps Pvt Ltd", "gstin": g, "tier": "strategic"})
    assert a.status_code == b.status_code == 201
    await drain()
    await drain()
    assert len((await client.get(f"{API}/accounts", headers=h)).json()) == 2
    cards = (await client.get(f"{API}/decisions", headers=h)).json()
    assert len(cards) == 1, cards  # the second event for the same pair did not add a card
    card = cards[0]
    assert card["tool_key"] == "steward.merge_records" and card["recommendation"]
    facts = {f["key"]: f["value"] for f in card["facts"]}
    assert facts["account_tier"] == "strategic" and float(facts["match_score"]) >= 80
    ok = await client.post(f"{API}/decisions/{card['id']}/approve", headers=h)
    assert ok.json()["status"] == "approved", ok.text
    assert len((await client.get(f"{API}/accounts", headers=h)).json()) == 1
    merges = (await client.get(f"{API}/steward/merges", headers=h)).json()
    assert merges[0]["automatic"] is False and merges[0]["decided_by"].startswith("user:")


async def test_manual_merge_supersedes_waiting_approval(client, ctx):
    h = ctx["h"]
    await client.put(f"{API}/agents/data_steward/level", headers=h, json={"level": 0})
    g = gstin("29", "AAACB1234C")
    for name in ("Bangalore Valves", "Bangalore Valves Ltd"):
        await client.post(f"{API}/accounts", headers=h, json={"name": name, "gstin": g})
    await drain()
    cand = (await client.get(f"{API}/steward/candidates", headers=h)).json()[0]
    r = await client.post(f"{API}/steward/candidates/{cand['id']}/merge", headers=h,
                          json={"survivor_id": cand["left_id"]})
    assert r.status_code == 200, r.text
    assert (await client.get(f"{API}/decisions", headers=h)).json() == []
    done = (await client.get(f"{API}/decisions?view=done", headers=h)).json()
    assert done[0]["status"] == "superseded"


async def test_policy_rule_blocks_strategic_merges(client, ctx):
    h = ctx["h"]
    r = await client.post(f"{API}/policies", headers=h, json={
        "agent_key": "data_steward", "tool_key": "steward.merge_records", "outcome": "block",
        "conditions": [{"fact": "account_tier", "op": "eq", "value": "strategic"}]})
    assert r.status_code == 201, r.text
    g = gstin("24", "AAACG5555K")
    await client.post(f"{API}/accounts", headers=h,
                      json={"name": "Gujarat Alloys", "gstin": g, "tier": "strategic"})
    await client.post(f"{API}/accounts", headers=h, json={"name": "Gujarat Alloys Ltd",
                                                          "gstin": g})
    await drain()
    assert len((await client.get(f"{API}/accounts", headers=h)).json()) == 2
    assert len((await client.get(f"{API}/steward/candidates", headers=h)).json()) == 1
    notices = (await client.get(f"{API}/decisions?view=notices", headers=h)).json()
    assert len(notices) == 1 and notices[0]["title"].startswith("Blocked: Merge account")


# Audit chain -------------------------------------------------------------------------

async def test_audit_chain_verifies_and_detects_tampering(client, ctx):
    h = ctx["h"]
    for amount in ("1", "2", "3"):
        await _try(client, h, amount)
    v = (await client.get(f"{API}/audit/verify", headers=h)).json()
    assert v["ok"] and v["checked"] >= 6
    log = (await client.get(f"{API}/audit?kind=action.", headers=h)).json()
    assert {r["kind"] for r in log} == {"action.proposed", "action.executed"}

    # The app role cannot change or remove audit records.
    async with tenant_session(ctx["tid"]) as s:
        with pytest.raises(Exception, match="permission denied"):
            await s.execute(text("UPDATE audit_records SET actor = 'x'"))
    # Nor can the owner while the guard trigger is on ...
    owner = create_async_engine(OWNER_URL)
    async with owner.begin() as conn:
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                           {"t": str(ctx["tid"])})
        with pytest.raises(Exception, match="append-only"):
            async with conn.begin_nested():
                await conn.execute(text("DELETE FROM audit_records WHERE seq = 2"))
        # ... and if someone switches it off and edits history, verify() finds it.
        await conn.execute(text("ALTER TABLE audit_records DISABLE TRIGGER "
                                "audit_records_no_update"))
        await conn.execute(text("UPDATE audit_records SET data = data || '{\"amount\": \"9\"}' "
                                "WHERE seq = 2"))
        await conn.execute(text("ALTER TABLE audit_records ENABLE TRIGGER "
                                "audit_records_no_update"))
    await owner.dispose()
    v = (await client.get(f"{API}/audit/verify", headers=h)).json()
    assert v == {"ok": False, "checked": 1, "first_bad_seq": 2,
                 "problem": "record 2 was changed after it was written"}


async def test_audit_chain_is_per_tenant_and_gap_free(client, ctx, make_tenant):
    async with tenant_session(ctx["tid"]) as s:
        for i in range(3):
            await chain.append(s, ctx["tid"], "test.event", actor="test", data={"i": i})
    async with tenant_session(ctx["tid"]) as s:
        seqs = list(await s.scalars(select(AuditRecord.seq).order_by(AuditRecord.seq)))
        assert seqs == list(range(1, len(seqs) + 1))
        assert (await chain.verify(s, ctx["tid"])).ok


# Chaos test (F12.2 acceptance) -------------------------------------------------------

async def test_chaos_every_over_limit_call_is_stopped(ctx):
    async with tenant_session(ctx["tid"]) as s:
        rep = await policy_gate.run(s, ctx["tid"], seed=3, n=120)
    assert rep.failures == [], "\n".join(rep.failures)
    assert rep.violating > 30 and rep.violating_ok == rep.violating
    assert rep.correct == rep.calls and rep.chain_ok


async def test_chaos_test_catches_a_gate_that_ignores_blocks(ctx, monkeypatch):
    real = policy.strongest
    monkeypatch.setattr(policy, "strongest",
                        lambda rules: real([r for r in rules if r.outcome != "block"]))
    async with tenant_session(ctx["tid"]) as s:
        rep = await policy_gate.run(s, ctx["tid"], seed=3, n=60, approve_sample=0)
    assert rep.violating_ok < rep.violating


def test_rupees_use_indian_grouping():
    from app.core.format import inr

    assert [inr(v) for v in (999, 100000, "1234.5", 20000000)] == [
        "₹999", "₹1,00,000", "₹1,234.50", "₹2,00,00,000"]


# Roles ----------------------------------------------------------------------------------

async def test_roles_and_users(client, ctx):
    h = ctx["h"]
    bad = await client.post(f"{API}/roles", headers=h, json={"name": "X", "permissions": ["nope"]})
    assert bad.status_code == 422
    role, rh = await _user_with_role(client, ctx, "Viewer", ["agents.read"])
    assert (await client.get(f"{API}/agents", headers=rh)).status_code == 200
    assert (await client.put(f"{API}/agents/sandbox/level", headers=rh,
                             json={"level": 0})).status_code == 403
    users = (await client.get(f"{API}/users", headers=h)).json()
    assert any(u["roles"] == [{"id": role["id"], "name": "Viewer"}] for u in users)
    me = next(u for u in users if u["email"].startswith("admin@"))
    lock_out = await client.put(f"{API}/users/{me['id']}/roles", headers=h,
                                json={"role_ids": [role["id"]]})
    assert lock_out.status_code == 400
    assert uuid.UUID(role["id"])
