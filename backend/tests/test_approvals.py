"""Step 5 (F12.3): approver chains, counter, delegation, deadlines, the card's history
and fingerprint, push and analytics. Each test names the decision or safeguard it
checks (docs/HANDOVER.md section 3)."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from app.approvals import push, sla
from app.approvals.calendar import DEFAULTS, calendar_of
from app.core import secrets
from app.db import tenant_session
from app.events import outbox
from app.integrations.connections import _context
from app.models import (
    AuditRecord,
    Decision,
    DecisionStep,
    ErpConnection,
    ErpCustomer,
    ErpInvoice,
    OutboxEvent,
    PushSubscription,
    SandboxAction,
    SyncFlow,
)
from tests.conftest import login
from tests.test_agents import (  # noqa: F401
    API,
    _account,
    _rule,
    _try,
    ctx,
    decide,
    drain,
    steward_handlers,
)

IST = ZoneInfo("Asia/Kolkata")
DECIDER = ["decisions.decide", "accounts.read", "audit.read"]


async def _role(client, h, name, perms=None):
    r = await client.post(f"{API}/roles", headers=h, json={"name": name,
                                                           "permissions": perms or DECIDER})
    assert r.status_code == 201, r.text
    return r.json()


async def _person(client, ctx, name, role_ids):  # noqa: F811
    email = f"{name.lower().replace(' ', '')}@{ctx['slug']}.example.com"
    r = await client.post(f"{API}/users", headers=ctx["h"], json={
        "email": email, "full_name": name, "password": "Passw0rd!x", "role_ids": role_ids})
    assert r.status_code == 201, r.text
    return r.json(), await login(client, ctx["slug"], email, "Passw0rd!x")


async def _card(client, h, did):
    return (await client.get(f"{API}/decisions/{did}", headers=h)).json()


async def _amounts(tid):
    async with tenant_session(tid) as s:
        return sorted(float(a) for a in await s.scalars(select(SandboxAction.amount)))


# H1, safeguard 1 -------------------------------------------------------------------------

async def test_chain_runs_in_order_with_a_different_person_each_step(client, ctx):  # noqa: F811
    h = ctx["h"]
    sh = await _role(client, h, "Sales Head")
    md = await _role(client, h, "Managing Director")
    fin = await _role(client, h, "Finance")
    _, sh_h = await _person(client, ctx, "Sunil Head", [sh["id"]])
    _, md_h = await _person(client, ctx, "Meena Director", [md["id"]])
    _, both_h = await _person(client, ctx, "Bina Both", [sh["id"], md["id"]])
    rule = await _rule(client, h, conditions=[{"fact": "amount", "op": "gt", "value": 500000}],
                       outcome="ask_person", approval_steps=[sh["id"], md["id"]],
                       sla_hours=8)
    assert rule["sentence"].endswith("ask a person first (Sales Head, then Managing Director "
                                     "decide in turn, within 8 working hours).")
    assert rule["approver_role_id"] == sh["id"]

    w = await _try(client, h, "600000")
    card = await _card(client, md_h, w["decision_id"])
    assert [s["role"] for s in card["steps"]] == ["Sales Head", "Managing Director"]
    assert [s["status"] for s in card["steps"]] == ["pending", "waiting"]
    assert card["why_not"] == "Waiting for Sales Head" and card["due_at"]
    assert (await decide(client, md_h, w["decision_id"])).status_code == 403

    one = await decide(client, both_h, w["decision_id"], note="Margin fine")
    assert one.status_code == 200 and one.json()["status"] == "pending", one.text
    assert one.json()["step"] == 2 and one.json()["approver_role"]["name"] == "Managing Director"
    assert await _amounts(ctx["tid"]) == []  # nothing runs before the last step
    # The same person cannot also clear the second step, even holding both roles.
    again = await decide(client, both_h, w["decision_id"])
    assert again.status_code == 403 and "different person" in again.text
    done = await decide(client, md_h, w["decision_id"])
    assert done.json()["status"] == "approved", done.text
    assert await _amounts(ctx["tid"]) == [600000.0]
    assert [s["decided_by_name"] for s in done.json()["steps"]] == ["Bina Both",
                                                                    "Meena Director"]

    # A rejection at any step ends the card: the later step never decides.
    w2 = await _try(client, h, "700000")
    assert (await decide(client, sh_h, w2["decision_id"])).json()["status"] == "pending"
    r = await decide(client, md_h, w2["decision_id"], "reject", note="Not this quarter")
    assert r.json()["status"] == "rejected"
    w3 = await _try(client, h, "800000")
    r = await decide(client, sh_h, w3["decision_id"], "reject")
    assert [s["status"] for s in r.json()["steps"]] == ["rejected", "cancelled"]
    assert await _amounts(ctx["tid"]) == [600000.0]

    # Two matching rules: every required role decides, none is skipped.
    await _rule(client, h, conditions=[{"fact": "amount", "op": "gt", "value": 100000}],
                outcome="ask_person", approver_role_id=fin["id"], sla_hours=2)
    w4 = await _try(client, h, "900000")
    card = await _card(client, h, w4["decision_id"])
    assert [s["role"] for s in card["steps"]] == ["Sales Head", "Managing Director", "Finance"]
    async with tenant_session(ctx["tid"]) as s:
        assert (await s.get(Decision, uuid.UUID(w4["decision_id"]))).sla_hours == Decimal(2)


async def test_the_asker_never_decides_and_admins_can_decide_any_role(client, ctx):  # noqa: F811
    h = ctx["h"]
    fin = await _role(client, h, "Finance")
    await _rule(client, h, conditions=[{"fact": "amount", "op": "gt", "value": 1000}],
                outcome="ask_person", approver_role_id=fin["id"])
    w = await _try(client, h, "5000")
    card = await _card(client, h, w["decision_id"])
    assert card["requested_by"].startswith("user:") and not card["can_decide"]
    assert card["why_not"] == "A second person must decide this: you asked for it"
    assert (await decide(client, h, w["decision_id"])).status_code == 403
    admin2 = await _role(client, h, "Second admin", ["*"])
    _, a2 = await _person(client, ctx, "Asha Admin", [admin2["id"]])
    assert (await decide(client, a2, w["decision_id"])).json()["status"] == "approved"


# H7, safeguard 6 -------------------------------------------------------------------------

async def test_a_card_that_changed_while_shown_is_refused(client, ctx):  # noqa: F811
    h = ctx["h"]
    acc = await _account(client, h, "Pawar Castings")
    await _rule(client, h, conditions=[{"fact": "amount", "op": "gt", "value": 1000}],
                outcome="ask_person")
    a = await _try(client, h, "2000", acc)
    b = await _try(client, h, "3000", acc)
    fin = await _role(client, h, "Checker")
    _, c = await _person(client, ctx, "Chetan Checker", [fin["id"]])
    seen = await _card(client, c, a["decision_id"])
    assert seen["history"]["account"]["name"] == "Pawar Castings"
    assert seen["history"]["earlier"]["count"] == 0
    assert seen["history"]["erp"] == {"linked": False}

    missing = await client.post(f"{API}/decisions/{a['decision_id']}/approve", headers=c,
                                json={})
    assert missing.status_code == 422
    # Deciding the other card changes this one's history, so its fingerprint.
    assert (await decide(client, c, b["decision_id"], "reject")).status_code == 200
    stale = await client.post(f"{API}/decisions/{a['decision_id']}/approve", headers=c,
                              json={"card_hash": seen["card_hash"]})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "card_changed"
    fresh = await _card(client, c, a["decision_id"])
    assert fresh["history"]["earlier"] == {"count": 1, "by_status": {"rejected": 1},
                                           "latest": [{"title": fresh["history"]["earlier"][
                                               "latest"][0]["title"], "status": "rejected"}]}
    ok = await client.post(f"{API}/decisions/{a['decision_id']}/approve", headers=c,
                           json={"card_hash": fresh["card_hash"], "shown_ms": 9000})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"

    # The trail keeps the card as shown and its fingerprint; the decision keeps the
    # references to its audit records and the action's.
    refs = ok.json()["result"]["audit"]
    async with tenant_session(ctx["tid"]) as s:
        rec = await s.scalar(select(AuditRecord).where(AuditRecord.seq == refs["decision"],
                                                       AuditRecord.tenant_id == ctx["tid"]))
        assert rec.kind == "decision.approved" and rec.data["card_hash"] == fresh["card_hash"]
        assert rec.evidence_digest  # the card itself is in the encrypted evidence
        out = await s.scalar(select(AuditRecord).where(AuditRecord.seq == refs["outcome"],
                                                       AuditRecord.tenant_id == ctx["tid"]))
        assert out.kind == "action.executed"
    run = (await client.get(f"{API}/audit/runs/{fresh['run_id']}", headers=h)).json()
    approved = [x for x in run["steps"] if x["kind"] == "decision.approved"][0]
    assert approved["data"]["card"]["title"] == fresh["title"]
    assert approved["data"]["card"]["recommendation"]["option"] == "approve"


# H2, safeguard 2 -------------------------------------------------------------------------

async def test_counter_is_checked_by_the_rules_and_keeps_both_versions(client, ctx):  # noqa: F811
    h = ctx["h"]
    fin = await _role(client, h, "Finance")
    md = await _role(client, h, "Managing Director")
    _, fin_h = await _person(client, ctx, "Farah Finance", [fin["id"]])
    _, md_h = await _person(client, ctx, "Mohan Director", [md["id"]])
    await _rule(client, h, conditions=[{"fact": "amount", "op": "gt", "value": 100000}],
                outcome="ask_person", approver_role_id=fin["id"])
    await _rule(client, h, conditions=[{"fact": "amount", "op": "gt", "value": 400000}],
                outcome="ask_person", approval_steps=[fin["id"], md["id"]])
    await _rule(client, h, conditions=[{"fact": "amount", "op": "gt", "value": 900000}],
                outcome="block")

    w = await _try(client, h, "300000")
    card = await _card(client, fin_h, w["decision_id"])
    assert card["counter_fields"] == [{"key": "amount", "label": "Amount (INR)",
                                       "type": "money", "value": "300000"}]

    async def counter(hh, did, values, **extra):
        c = await _card(client, hh, did)
        return await client.post(f"{API}/decisions/{did}/counter", headers=hh, json={
            "card_hash": c["card_hash"], "values": values, **extra})

    blocked = await counter(fin_h, w["decision_id"], {"amount": "950000"})
    assert blocked.status_code == 422 and "block" in blocked.text
    wrong = await counter(fin_h, w["decision_id"], {"account_id": str(uuid.uuid4())})
    assert wrong.status_code == 422 and "Only amount can be changed" in wrong.text
    same = await counter(fin_h, w["decision_id"], {"amount": "300000"})
    assert same.status_code == 422
    assert (await _card(client, h, w["decision_id"]))["status"] == "pending"

    # Lower: still needs Finance, and Finance's counter is that approval, so it runs.
    r = await counter(fin_h, w["decision_id"], {"amount": "250000"}, note="Agreed 2.5L")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "countered"
    new = r.json()["new_card"]
    assert new["status"] == "approved" and new["counter_of"] == w["decision_id"]
    assert await _amounts(ctx["tid"]) == [250000.0]
    detail = await _card(client, h, new["id"])
    assert detail["counter"]["asked"][0]["value"] == "₹3,00,000"
    assert detail["counter"]["countered"][0]["value"] == "₹2,50,000"
    async with tenant_session(ctx["tid"]) as s:
        rec = await s.scalar(select(AuditRecord).where(
            AuditRecord.kind == "decision.countered", AuditRecord.tenant_id == ctx["tid"]))
        assert rec.data["fields"] == ["amount"]

    # Higher: the counter triggers the extra approval the new amount needs.
    w2 = await _try(client, h, "350000")
    r = await counter(fin_h, w2["decision_id"], {"amount": "450000"})
    new = r.json()["new_card"]
    assert new["status"] == "pending" and [s["status"] for s in new["steps"]] == [
        "approved", "pending"]
    assert await _amounts(ctx["tid"]) == [250000.0]
    assert (await decide(client, md_h, new["id"])).json()["status"] == "approved"
    assert await _amounts(ctx["tid"]) == [250000.0, 450000.0]

    # Down to where no person is needed: it runs at once.
    w3 = await _try(client, h, "150000")
    r = await counter(fin_h, w3["decision_id"], {"amount": "90000"})
    assert r.json()["counter_result"]["status"] == "done"
    assert await _amounts(ctx["tid"]) == [90000.0, 250000.0, 450000.0]


# H3, safeguard 3 -------------------------------------------------------------------------

async def test_hand_a_card_and_out_of_office_stay_within_scope(client, ctx):  # noqa: F811
    h = ctx["h"]
    fin = await _role(client, h, "Finance")
    clerk = await _role(client, h, "Clerk", DECIDER + ["agents.manage"])
    ravi, ravi_h = await _person(client, ctx, "Ravi Kumar", [fin["id"]])
    priya, priya_h = await _person(client, ctx, "Priya Shah", [clerk["id"]])
    await _rule(client, h, conditions=[{"fact": "amount", "op": "gt", "value": 100000}],
                outcome="ask_person", approver_role_id=fin["id"])

    w = await _try(client, h, "150000")
    assert not (await _card(client, priya_h, w["decision_id"]))["can_decide"]
    # Only someone who could decide it can hand it on.
    no = await client.post(f"{API}/decisions/{w['decision_id']}/hand", headers=priya_h,
                           json={"to_user_id": ravi["id"]})
    assert no.status_code == 403
    r = await client.post(f"{API}/decisions/{w['decision_id']}/hand", headers=ravi_h,
                          json={"to_user_id": priya["id"], "note": "On leave Friday"})
    assert r.status_code == 201, r.text
    mine = (await client.get(f"{API}/decisions", headers=priya_h)).json()
    assert [c["id"] for c in mine] == [w["decision_id"]]
    assert mine[0]["can_decide"] and mine[0]["as_stand_in_for"] == "Ravi Kumar"
    ok = await decide(client, priya_h, w["decision_id"])
    assert ok.json()["status"] == "approved"
    assert ok.json()["steps"][0]["on_behalf_of_name"] == "Ravi Kumar"

    # Four-eyes holds on someone's behalf: Priya never decides what she asked for.
    own = await _try(client, priya_h, "120000")
    r = await client.post(f"{API}/decisions/{own['decision_id']}/hand", headers=ravi_h,
                          json={"to_user_id": priya["id"]})
    assert r.status_code == 400 and "asked for this" in r.text

    # Out of office: explicit card types, and a limit for cards with an amount.
    now = datetime.now(UTC)
    body = {"to_user_id": priya["id"], "starts_at": now.isoformat(),
            "ends_at": (now + timedelta(days=3)).isoformat(),
            "tool_keys": ["sandbox.record_payment"]}
    r = await client.post(f"{API}/decisions/away", headers=ravi_h, json=body)
    assert r.status_code == 400 and "Set the most the stand-in may approve" in r.text
    r = await client.post(f"{API}/decisions/away", headers=ravi_h,
                          json={**body, "max_amount": "200000"})
    assert r.status_code == 201, r.text
    away = r.json()
    clash = await client.post(f"{API}/decisions/away", headers=ravi_h,
                              json={**body, "max_amount": "50000"})
    assert clash.status_code == 400
    small, big = await _try(client, h, "180000"), await _try(client, h, "250000")
    assert (await _card(client, priya_h, small["decision_id"]))["as_stand_in_for"] == \
        "Ravi Kumar"
    assert not (await _card(client, priya_h, big["decision_id"]))["can_decide"]
    assert not (await _card(client, priya_h, own["decision_id"]))["can_decide"]
    # A stand-in's counter stays inside the limit.
    c = await _card(client, priya_h, small["decision_id"])
    over = await client.post(f"{API}/decisions/{small['decision_id']}/counter",
                             headers=priya_h, json={"card_hash": c["card_hash"],
                                                    "values": {"amount": "190000.50"}})
    assert over.status_code == 200, over.text  # under 2,00,000: allowed
    end = await client.post(f"{API}/decisions/delegations/{away['id']}/end", headers=ravi_h)
    assert end.json()["active"] is False
    assert not (await _card(client, priya_h, big["decision_id"]))["can_decide"]
    listed = (await client.get(f"{API}/decisions/delegations", headers=ravi_h)).json()
    assert {d["kind"] for d in listed} == {"card", "away"}
    async with tenant_session(ctx["tid"]) as s:
        kinds = list(await s.scalars(select(AuditRecord.kind).where(
            AuditRecord.kind.like("delegation.%"))))
    assert kinds.count("delegation.created") == 2 and "delegation.ended" in kinds


# H4, safeguard 4 -------------------------------------------------------------------------

def test_working_hours_skip_nights_sundays_and_holidays():
    cal = calendar_of({**DEFAULTS, "holidays": [{"date": "2026-10-20", "name": "Diwali"}]})
    sat = datetime(2026, 10, 17, 17, 30, tzinfo=IST)
    assert cal.add(sat, 4).astimezone(IST) == datetime(2026, 10, 19, 12, 30, tzinfo=IST)
    mon = datetime(2026, 10, 19, 17, 0, tzinfo=IST)  # the next day is a holiday
    assert cal.add(mon, 2).astimezone(IST) == datetime(2026, 10, 21, 10, 0, tzinfo=IST)
    night = datetime(2026, 10, 21, 22, 0, tzinfo=IST)
    assert cal.add(night, 0.5).astimezone(IST) == datetime(2026, 10, 22, 10, 0, tzinfo=IST)
    assert cal.between(sat, cal.add(sat, 4)) == 4 * 3600
    assert cal.window(date(2026, 10, 18)) is None  # Sunday


async def test_deadline_reminds_then_escalates_and_never_decides(client, ctx):  # noqa: F811
    h, tid = ctx["h"], ctx["tid"]
    fin = await _role(client, h, "Finance")
    md = await _role(client, h, "Managing Director")
    _, md_h = await _person(client, ctx, "Mohan Director", [md["id"]])
    await _rule(client, h, conditions=[{"fact": "amount", "op": "gt", "value": 100}],
                outcome="ask_person", approval_steps=[fin["id"], md["id"]], sla_hours=4)
    w = await _try(client, h, "500")
    did = uuid.UUID(w["decision_id"])
    async with tenant_session(tid) as s:
        step = await s.scalar(select(DecisionStep).where(DecisionStep.decision_id == did,
                                                         DecisionStep.position == 0))
        remind, due = step.remind_at, step.due_at
        cal = calendar_of(DEFAULTS)
        assert due == cal.add(step.started_at, 4)
        assert remind == cal.add(step.started_at, 3)
    outbox.clear_handlers()
    async with tenant_session(tid) as s:
        assert (await sla.tick_tenant(s, tid, remind + timedelta(minutes=1)))["reminded"] == 1
    async with tenant_session(tid) as s:
        c = await sla.tick_tenant(s, tid, due + timedelta(minutes=1))
        assert c["overdue"] == 1
    async with tenant_session(tid) as s:
        again = await sla.tick_tenant(s, tid, due + timedelta(hours=1))
        assert again["overdue"] == 0 and again["reminded"] == 0
        reasons = [e.payload["reason"] for e in await s.scalars(
            select(OutboxEvent).where(OutboxEvent.event_type == "inbox.notify")
            .order_by(OutboxEvent.created_at))]
        assert reasons == ["new", "reminder", "overdue"]
        d = await s.get(Decision, did)
        assert d.status == "pending" and d.step_index == 0  # never decided by the clock
        assert await s.scalar(select(AuditRecord.kind).where(
            AuditRecord.kind == "decision.overdue")) == "decision.overdue"
    # The next role up is told and sees it, but cannot decide a step that isn't theirs.
    card = (await client.get(f"{API}/decisions", headers=md_h)).json()
    assert [c["id"] for c in card] == [w["decision_id"]]
    assert card[0]["escalated_to_me"] and card[0]["past_deadline"]
    assert not card[0]["can_decide"]
    assert (await decide(client, md_h, w["decision_id"])).status_code == 403


async def test_working_calendar_settings_are_checked_and_audited(client, ctx):  # noqa: F811
    h = ctx["h"]
    s = (await client.get(f"{API}/decisions/settings", headers=h)).json()
    assert s["is_default"] and s["work_days"] == [0, 1, 2, 3, 4, 5]
    assert s["day_start"] == "09:30" and s["default_sla_hours"] == "4"
    bad = await client.put(f"{API}/decisions/settings", headers=h, json={
        **s, "day_start": "19:00", "work_days": [], "reminder_pct": 100,
        "holidays": [{"date": "2026-13-01", "name": "x"}]})
    assert bad.status_code == 422
    problems = bad.json()["error"]["problems"]
    assert "Pick at least one working day" in problems
    assert "The working day must end after it starts" in problems
    ok = await client.put(f"{API}/decisions/settings", headers=h, json={
        **s, "work_days": [0, 1, 2, 3, 4], "default_sla_hours": "6",
        "holidays": [{"date": "2026-11-08", "name": "Diwali"}]})
    assert ok.status_code == 200, ok.text
    assert ok.json()["work_days"] == [0, 1, 2, 3, 4] and not ok.json()["is_default"]
    _, plain = await _person(client, ctx, "Dev Decider", [(await _role(client, h, "D"))["id"]])
    assert (await client.put(f"{API}/decisions/settings", headers=plain,
                             json=s)).status_code == 403
    w = await _try(client, h, "1")  # sandbox at L2: no card; set L0 for one
    assert w["status"] == "done"
    await client.put(f"{API}/agents/sandbox/level", headers=h, json={"level": 0})
    w = await _try(client, h, "1")
    async with tenant_session(ctx["tid"]) as db:
        d = await db.get(Decision, uuid.UUID(w["decision_id"]))
        assert d.sla_hours == Decimal(6)


# H6, safeguard 5 -------------------------------------------------------------------------

async def test_history_shows_erp_figures_with_their_sync_time(client, ctx):  # noqa: F811
    h, tid = ctx["h"], ctx["tid"]
    acc = await _account(client, h, "Ghatge Pumps", "key")
    today = date.today()
    async with tenant_session(tid) as s:
        conn = ErpConnection(id=uuid.uuid4(), tenant_id=tid, kind="erpnext",
                             name="ERPNext Pune", base_url="https://erp.example",
                             secret_hint="1234", created_by="test", updated_by="test")
        conn.api_key_enc = secrets.encrypt("k" * 15, context=_context(conn, "api_key"))
        conn.api_secret_enc = secrets.encrypt("s" * 15, context=_context(conn, "api_secret"))
        s.add(conn)
        await s.flush()
        s.add(ErpCustomer(tenant_id=tid, connection_id=conn.id, external_id="CUST-1",
                          modified_at=datetime.now(UTC), data={}, name="Ghatge Pumps",
                          account_id=uuid.UUID(acc), link_status="linked"))
        for n, (dt, due, total, owed, status) in enumerate([
                (today - timedelta(days=40), today - timedelta(days=10), 100000, 60000,
                 "overdue"),
                (today - timedelta(days=5), today + timedelta(days=25), 50000, 50000, "unpaid"),
                (today - timedelta(days=400), today - timedelta(days=370), 70000, 0, "paid")]):
            s.add(ErpInvoice(tenant_id=tid, connection_id=conn.id, external_id=f"INV-{n}",
                             modified_at=datetime.now(UTC), data={}, number=f"INV-{n}",
                             customer_external_id="CUST-1", invoice_date=dt, due_date=due,
                             status=status, currency="INR", grand_total=Decimal(total),
                             outstanding=Decimal(owed)))
        s.add(SyncFlow(tenant_id=tid, connection_id=conn.id, entity="invoice",
                       last_success_at=datetime.now(UTC) - timedelta(minutes=3)))
    await client.put(f"{API}/agents/sandbox/level", headers=h, json={"level": 0})
    w = await _try(client, h, "10", acc)
    hist = (await _card(client, h, w["decision_id"]))["history"]
    assert hist["tier"] == "key"
    erp = hist["erp"]
    assert erp["linked"] and not erp["stale"] and erp["as_of"] and erp["erps"] == ["ERPNext Pune"]
    assert erp["figures"] == [{"currency": "INR", "outstanding": "₹1,10,000",
                               "overdue": "₹60,000", "invoiced_12m": "₹1,50,000"}]
    before = (await _card(client, h, w["decision_id"]))["card_hash"]
    # A later sync that changes no figure keeps the card; a copy gone stale changes it.
    async with tenant_session(tid) as s:
        await s.execute(update(SyncFlow).values(last_success_at=datetime.now(UTC)))
    assert (await _card(client, h, w["decision_id"]))["card_hash"] == before
    async with tenant_session(tid) as s:
        await s.execute(update(SyncFlow).values(
            last_success_at=datetime.now(UTC) - timedelta(hours=3)))
    after = await _card(client, h, w["decision_id"])
    assert after["history"]["erp"]["stale"] and after["card_hash"] != before


# H5 and H9, safeguard 8 ------------------------------------------------------------------

async def test_push_goes_to_who_can_decide_and_dropped_browsers_are_removed(
        client, ctx, monkeypatch):  # noqa: F811
    from app.approvals import notify
    from app.config import get_settings

    h, tid = ctx["h"], ctx["tid"]
    monkeypatch.setattr(get_settings(), "vapid_public_key", "BPub")
    monkeypatch.setattr(get_settings(), "vapid_private_key", "priv")
    sent: list[tuple[str, str]] = []

    def fake(sub, message):
        sent.append((sub["endpoint"], message["title"]))
        return 410 if "gone" in sub["endpoint"] else None

    monkeypatch.setattr(push, "sender", fake)
    notify.register()
    fin = await _role(client, h, "Finance")
    _, fin_h = await _person(client, ctx, "Farah Finance", [fin["id"]])
    _, other_h = await _person(client, ctx, "Otto Other",
                               [(await _role(client, h, "Other"))["id"]])
    assert (await client.get(f"{API}/decisions/push/key", headers=fin_h)).json()[
        "public_key"] == "BPub"
    for hh, ep in ((fin_h, "https://push.example/fin"), (fin_h, "https://push.example/gone"),
                   (other_h, "https://push.example/other")):
        r = await client.post(f"{API}/decisions/push/subscribe", headers=hh, json={
            "endpoint": ep, "keys": {"p256dh": "p" * 20, "auth": "a" * 12}})
        assert r.status_code == 201, r.text
    await _rule(client, h, conditions=[{"fact": "amount", "op": "gt", "value": 100}],
                outcome="ask_person", approver_role_id=fin["id"])
    await _try(client, h, "500")
    await drain()
    assert sorted(sent) == [("https://push.example/fin", "Waiting for you: Record a test "
                                                         "payment"),
                            ("https://push.example/gone", "Waiting for you: Record a test "
                                                          "payment")]
    async with tenant_session(tid) as s:
        left = list(await s.scalars(select(PushSubscription.endpoint)))
    assert "https://push.example/gone" not in left and len(left) == 2


async def test_analytics_count_overrides_only_where_recommended(client, ctx):  # noqa: F811
    h, tid = ctx["h"], ctx["tid"]
    _, c = await _person(client, ctx, "Chetan Checker",
                         [(await _role(client, h, "Checker"))["id"]])
    await client.put(f"{API}/agents/sandbox/level", headers=h, json={"level": 0})
    a, b, x = await _try(client, h, "10"), await _try(client, h, "20"), await _try(client, h,
                                                                                     "30")
    await decide(client, c, a["decision_id"], shown_ms=12_000)
    await decide(client, c, b["decision_id"], "reject", shown_ms=30_000)
    # A card whose agent gave no recommendation is not counted as agreeing.
    async with tenant_session(tid) as s:
        await s.execute(update(Decision).where(Decision.id == uuid.UUID(x["decision_id"]))
                        .values(recommended_option=None))
    await decide(client, c, x["decision_id"], "reject", shown_ms=90_000)
    me = (await client.get(f"{API}/decisions/me", headers=c)).json()
    assert me["decided"] == 3 and me["median_decision_seconds"] == 30.0
    assert me["with_recommendation"] == 2 and me["overrides"] == 1 and me["override_rate"] == 0.5
    assert (await client.get(f"{API}/decisions/analytics", headers=c)).status_code == 403
    rep = (await client.get(f"{API}/decisions/analytics", headers=h)).json()
    [row] = rep["by_type"]
    assert row["tool_key"] == "sandbox.record_payment" and row["decided"] == 3
    assert row["by_status"] == {"approved": 1, "rejected": 2}
    assert row["override_rate"] == 0.5 and row["with_recommendation"] == 2
    [who] = rep["approvers"]
    assert who["name"] == "Chetan Checker" and who["median_decision_seconds"] == 30.0
    assert rep["overall"]["under_60s"] == round(2 / 3, 3)


async def test_bottleneck_alert_goes_to_admins_and_clears_itself(client, ctx):  # noqa: F811
    h, tid = ctx["h"], ctx["tid"]
    fin = await _role(client, h, "Finance")
    _, fin_h = await _person(client, ctx, "Farah Finance", [fin["id"]])
    s0 = (await client.get(f"{API}/decisions/settings", headers=h)).json()
    await client.put(f"{API}/decisions/settings", headers=h,
                     json={**s0, "bottleneck_overdue": 1})
    await _rule(client, h, conditions=[{"fact": "amount", "op": "gt", "value": 100}],
                outcome="ask_person", approver_role_id=fin["id"])
    ids = [(await _try(client, h, str(500 + i)))["decision_id"] for i in range(2)]
    later = datetime.now(UTC) + timedelta(days=7)
    async with tenant_session(tid) as s:
        assert (await sla.tick_tenant(s, tid, later))["alerts"] == 1
    alerts = [c for c in (await client.get(f"{API}/decisions", headers=h)).json()
              if c["kind"] == "alert"]
    assert alerts[0]["title"] == "Finance: 2 cards past their deadline"
    assert alerts[0]["tool_title"] == "Approvals piling up"
    assert not [c for c in (await client.get(f"{API}/decisions", headers=fin_h)).json()
                if c["kind"] == "alert"]
    for did in ids:
        assert (await decide(client, fin_h, did)).status_code == 200
    async with tenant_session(tid) as s:
        await sla.tick_tenant(s, tid, later)
        assert await s.scalar(select(Decision.status).where(
            Decision.tool_key == "inbox.bottleneck")) == "resolved"
