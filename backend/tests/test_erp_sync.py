"""F12.6 with storage: credentials, the scheduled sync into the erp_* tables, health
alerts, customer linking (safeguards 2 and 3), the gated ERP write (safeguard 1) and
the mapping studio's code lists. Runs against the fake ERPNext."""

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.agents import controls
from app.agents.registry import AGENTS, load_definitions
from app.agents.runtime import decide
from app.config import get_settings
from app.core import secrets
from app.db import get_sessionmaker, tenant_session
from app.integrations import connections, linking, mapping, writes
from app.integrations.connections import ENTITY_ORDER, create_connection, use_transport
from app.integrations.runner import run_reconcile, run_sync, tick
from app.integrations.testing.fake_erpnext import FakeERPNext
from app.integrations.testing.seed import seed_demo
from app.models import (
    Account,
    AuditRecord,
    CodeMap,
    CodeMapHistory,
    Decision,
    ErpConnection,
    ErpCustomer,
    ErpItem,
    ErpOrder,
    ErpWrite,
    FieldProvenance,
    SyncDeadLetter,
    SyncFlow,
    SyncRun,
    Tenant,
)
from app.steward.identifiers import gstin_check_digit
from app.steward.records import create_account

load_definitions()


def gstin(state: str, pan: str) -> str:
    first = f"{state}{pan}1Z"
    return first + gstin_check_digit(first)


async def no_sleep(_: float) -> None:
    return None


async def _tenant_id(slug: str) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        return (await s.scalar(select(Tenant).where(Tenant.slug == slug))).id


async def _connect(tid, fake: FakeERPNext, name: str = "ERPNext Pune") -> SimpleNamespace:
    url = f"http://erp-{uuid.uuid4().hex[:8]}.test"
    use_transport(url, fake)
    async with tenant_session(tid) as s:
        conn = await create_connection(s, tid, kind="erpnext", name=name, base_url=url,
                                       api_key=fake.api_key, api_secret=fake.api_secret,
                                       settings={}, actor="user:test")
    return SimpleNamespace(tid=tid, fake=fake, conn_id=conn.id, url=url)


@pytest.fixture
async def erp(make_tenant):
    slug, *_ = await make_tenant()
    tid = await _tenant_id(slug)
    fake = FakeERPNext()
    seed_demo(fake, broken=True)
    e = await _connect(tid, fake)
    yield e
    use_transport(e.url, None)


async def _flows(e) -> dict[str, SyncFlow]:
    async with tenant_session(e.tid) as s:
        return {f.entity: f for f in await s.scalars(
            select(SyncFlow).where(SyncFlow.connection_id == e.conn_id))}


async def sync_all(e, **kw) -> dict[str, dict]:
    flows = await _flows(e)
    return {entity.value: await run_sync(e.tid, flows[entity.value].id, sleep=no_sleep, **kw)
            for entity in ENTITY_ORDER}


async def _scalar(e, stmt):
    async with tenant_session(e.tid) as s:
        return await s.scalar(stmt)


async def _all(e, stmt):
    async with tenant_session(e.tid) as s:
        return list(await s.scalars(stmt))


async def _open_cards(e, kind: str) -> list[Decision]:
    return await _all(e, select(Decision).where(Decision.kind == kind,
                                                Decision.status.in_(("open", "pending"))))


# --- credentials (safeguard 4) ---------------------------------------------------------------

async def test_credentials_are_encrypted_bound_and_never_shown(erp, caplog):
    async with tenant_session(erp.tid) as s:
        conn = await s.get(ErpConnection, erp.conn_id)
        assert erp.fake.api_secret not in conn.api_secret_enc
        assert conn.api_secret_enc.startswith("v1.ktest.")
        assert erp.fake.api_secret not in repr(conn) and erp.fake.api_key not in repr(conn)
        assert conn.secret_hint == secrets.hint(erp.fake.api_key)
        adapter = connections.adapter_for(conn)
        assert erp.fake.api_secret not in repr(adapter.settings)
        await adapter.aclose()
        # Bound to where it is stored: the key's ciphertext does not open as the secret,
        # and a single changed character is caught.
        with pytest.raises(secrets.CredentialKeyError):
            secrets.decrypt(conn.api_key_enc, context=f"{conn.tenant_id}:{conn.id}:api_secret")
        tampered = conn.api_secret_enc[:-2] + ("A" if conn.api_secret_enc[-2] != "A" else "B")
        with pytest.raises(secrets.CredentialKeyError):
            secrets.decrypt(tampered + conn.api_secret_enc[-1:],
                            context=f"{conn.tenant_id}:{conn.id}:api_secret")
    caplog.set_level(logging.DEBUG)
    await sync_all(erp)
    assert erp.fake.api_secret not in caplog.text
    audit = await _all(erp, select(AuditRecord))
    blob = json.dumps([a.data for a in audit])
    assert erp.fake.api_secret not in blob and erp.fake.api_key not in blob
    assert any(a.kind == "integration.connection.created" for a in audit)


async def test_key_rotation_reencrypts_and_old_key_can_go(erp, monkeypatch):
    old = get_settings().credential_keys
    monkeypatch.setenv("CREDENTIAL_KEYS", f"{secrets.new_key_spec('knew')},{old}")
    get_settings.cache_clear()
    try:
        counts = await connections.rotate_all_credentials()
        assert counts["current"] == "knew" and counts["rotated"] >= 2
        async with tenant_session(erp.tid) as s:
            conn = await s.get(ErpConnection, erp.conn_id)
            assert secrets.key_id(conn.api_key_enc) == "knew"
        # With only the new key left, the connection still works.
        new_only = get_settings().credential_keys.split(",")[0]
        monkeypatch.setenv("CREDENTIAL_KEYS", new_only)
        get_settings.cache_clear()
        async with tenant_session(erp.tid) as s:
            conn = await s.get(ErpConnection, erp.conn_id)
            check = await connections.check_connection(s, conn)
            assert check.ok
        assert any(a.kind == "integration.credentials.reencrypted"
                   for a in await _all(erp, select(AuditRecord)))
    finally:
        monkeypatch.setenv("CREDENTIAL_KEYS", old)
        get_settings.cache_clear()


def test_key_ring_rejects_bad_config(monkeypatch):
    for spec, words in (("", "not set"), ("k1:short", "32 bytes"), ("K 1:abc", "id:base64key"),
                        (f"k1:{'A' * 43}=,k1:{'B' * 43}=", "twice")):
        monkeypatch.setenv("CREDENTIAL_KEYS", spec)
        get_settings.cache_clear()
        with pytest.raises(secrets.CredentialKeyError, match=words):
            secrets.key_ring()
    monkeypatch.undo()
    get_settings.cache_clear()


# --- syncing into the tables ----------------------------------------------------------------

async def test_full_sync_fills_every_table_and_then_reads_only_changes(erp):
    results = await sync_all(erp, page_size=5)
    assert all(r and r["status"] in ("succeeded", "partial") for r in results.values()), results
    assert results["order"]["status"] == "partial" and results["order"]["problems"] == 1
    assert await _scalar(erp, select(func.count()).select_from(ErpCustomer)) == 8
    assert await _scalar(erp, select(func.count()).select_from(ErpItem)) == 8
    orders = await _all(erp, select(ErpOrder))
    assert len(orders) == 12 and all(o.data["lines"] for o in orders)

    # The unreadable order is a dead letter and a task card naming it.
    dead = await _all(erp, select(SyncDeadLetter))
    assert [d.external_id for d in dead] == ["SAL-ORD-2026-BROKEN"]
    tasks = await _open_cards(erp, "task")
    assert any("could not be read" in t.title and "BROKEN" in t.reasons[0] for t in tasks)

    flows = await _flows(erp)
    assert all(f.state.get("watermark") and f.last_success_at and not f.running_since
               for f in flows.values())
    erp.fake.tick(600)
    erp.fake.touch("Item", "GBX-110", item_name="Helical gearbox 110 mm (revised)")
    flows = await _flows(erp)
    again = await run_sync(erp.tid, flows["item"].id, sleep=no_sleep)
    assert again["status"] == "succeeded"  # re-reads the 2-minute overlap window too
    item = await _scalar(erp, select(ErpItem).where(ErpItem.external_id == "GBX-110"))
    assert item.name.endswith("(revised)")
    erp.fake.tick(600)
    erp.fake.touch("Item", "MTR-7K5", item_name="IE3 motor 7.5 kW (revised)")
    third = await run_sync(erp.tid, flows["item"].id, sleep=no_sleep)
    assert third["read"] == 2  # the change, plus the record on the last high-water mark

    # Fixing the broken order in the ERP resolves its dead letter and closes the task.
    row = erp.fake.child_rows["Sales Order Item"]
    for r in row:
        if r["parent"] == "SAL-ORD-2026-BROKEN":
            r["qty"] = 10.0
    erp.fake.touch("Sales Order", "SAL-ORD-2026-BROKEN")
    await run_sync(erp.tid, flows["order"].id, sleep=no_sleep)
    assert (await _all(erp, select(SyncDeadLetter)))[0].status == "resolved"
    assert not [t for t in await _open_cards(erp, "task") if "could not be read" in t.title]


async def test_a_failed_run_resumes_from_its_last_saved_page(erp):
    seed_demo(erp.fake, seed=8, burst=20)  # more customers than one page
    flows = await _flows(erp)
    erp.fake.fault = None
    real = erp.fake.handle_async_request
    calls = {"n": 0}

    async def flaky(request):
        if request.method == "GET" and "Customer" in str(request.url) and "Credit" not in str(
                request.url) and "Address" not in str(request.url):
            calls["n"] += 1
            if calls["n"] == 3:
                erp.fake.revoked = True
        return await real(request)

    erp.fake.handle_async_request = flaky
    first = await run_sync(erp.tid, flows["customer"].id, page_size=5, sleep=no_sleep)
    assert first["status"] == "failed" and first["error"] == "auth_rejected"
    flow = (await _flows(erp))["customer"]
    assert flow.state.get("cursor") and flow.consecutive_failures == 1
    stored = await _scalar(erp, select(func.count()).select_from(ErpCustomer))
    total = len(erp.fake.docs["Customer"])
    assert 5 <= stored < total  # the pages read before the key was revoked are kept

    erp.fake.revoked = False
    rest = await run_sync(erp.tid, flow.id, page_size=5, sleep=no_sleep)
    assert rest["status"] == "succeeded" and rest["read"] <= total - stored + 5
    assert await _scalar(erp, select(func.count()).select_from(ErpCustomer)) == total
    flow = (await _flows(erp))["customer"]
    assert flow.consecutive_failures == 0 and not flow.state.get("cursor")
    runs = await _all(erp, select(SyncRun).where(SyncRun.flow_id == flow.id))
    assert sorted(r.status for r in runs) == ["failed", "succeeded"]


async def test_reconciliation_marks_deleted_records_gone_without_deleting(erp):
    await sync_all(erp)
    del erp.fake.docs["Item"]["SPR-KIT"]
    flows = await _flows(erp)
    result = await run_reconcile(erp.tid, flows["item"].id, sleep=no_sleep)
    assert result["gone"] == 1 and result["newly_gone"] == 1
    item = await _scalar(erp, select(ErpItem).where(ErpItem.external_id == "SPR-KIT"))
    assert item is not None and item.gone_at is not None
    flow = (await _flows(erp))["item"]
    assert flow.reconciled_at and flow.last_reconcile["gone"] == 1


async def test_two_runs_of_one_flow_never_overlap(erp):
    flows = await _flows(erp)
    async with tenant_session(erp.tid) as s:
        f = await s.get(SyncFlow, flows["item"].id)
        f.running_since = datetime.now(UTC)
    assert await run_sync(erp.tid, flows["item"].id, sleep=no_sleep) is None
    async with tenant_session(erp.tid) as s:
        f = await s.get(SyncFlow, flows["item"].id)
        f.running_since = datetime.now(UTC) - timedelta(hours=1)  # crashed worker
    assert (await run_sync(erp.tid, flows["item"].id, sleep=no_sleep))["status"] == "succeeded"


# --- the schedule and alerts (safeguard 5) -------------------------------------------------

class Enqueued(list):
    """What tick() would start, for one tenant (other tests' connections are there too)."""

    def __init__(self, tenant_id=None):
        super().__init__()
        self.tenant_id = tenant_id

    def __call__(self, what, tenant_id, flow_id, trigger):
        if self.tenant_id in (None, tenant_id):
            self.append((what, flow_id, trigger))


async def _alert(e) -> Decision | None:
    rows = await _all(e, select(Decision).where(
        Decision.kind == "alert", Decision.record_id == e.conn_id,
        Decision.status.in_(("open", "acknowledged"))))
    return rows[0] if rows else None


async def test_a_revoked_key_raises_an_alert_naming_the_cause_and_clears_when_fixed(erp):
    t0 = datetime.now(UTC)
    q = Enqueued(erp.tid)
    counts = await tick(now=t0, enqueue=q)
    assert counts["checked"] >= 1 and {w for w, *_ in q} == {"sync"} and len(q) == 7
    assert await _alert(erp) is None

    erp.fake.revoke()
    await tick(now=t0 + timedelta(minutes=1), enqueue=Enqueued())
    alert = await _alert(erp)
    assert alert is not None and "rejected Forge's API key" in alert.title
    assert "API Access" in alert.reasons[0]
    assert any(a.kind == "integration.alert.raised" for a in await _all(erp, select(AuditRecord)))

    # Fixed: a new key entered on the connection; the next check clears the alert.
    erp.fake.revoked = False
    erp.fake.api_key, erp.fake.api_secret = "new-key-1234", "new-secret-5678"
    async with tenant_session(erp.tid) as s:
        conn = await s.get(ErpConnection, erp.conn_id)
        await connections.change_credentials(s, conn, "new-key-1234", "new-secret-5678",
                                             "user:test")
    await tick(now=t0 + timedelta(minutes=2), enqueue=Enqueued())
    assert await _alert(erp) is None
    kinds = [a.kind for a in await _all(erp, select(AuditRecord))]
    assert "integration.alert.resolved" in kinds and "integration.credentials.changed" in kinds


async def test_an_unreachable_erp_alerts_only_after_a_few_minutes(erp):
    t0 = datetime.now(UTC)
    await tick(now=t0, enqueue=Enqueued())
    erp.fake.fault = "offline"
    await tick(now=t0 + timedelta(minutes=1), enqueue=Enqueued())
    assert await _alert(erp) is None
    await tick(now=t0 + timedelta(minutes=4), enqueue=Enqueued())
    alert = await _alert(erp)
    assert alert is not None and "cannot be reached" in alert.title


async def test_data_falling_behind_and_stuck_runs_alert(erp):
    await sync_all(erp)
    now = datetime.now(UTC)
    async with tenant_session(erp.tid) as s:
        for f in await s.scalars(select(SyncFlow).where(SyncFlow.connection_id == erp.conn_id)):
            if f.entity == "invoice":
                f.last_success_at = now - timedelta(minutes=40)
            if f.entity == "stock":
                f.running_since = now - timedelta(minutes=45)
        conn = await s.get(ErpConnection, erp.conn_id)
        await connections.check_connection(s, conn, now=now)
    alert = await _alert(erp)
    codes = {p["code"] for p in alert.args["problems"]}
    assert {"lagging", "stuck"} <= codes and "Invoices" in " ".join(alert.reasons)


async def test_sync_due_on_schedule_and_on_request(erp):
    await sync_all(erp)
    q = Enqueued(erp.tid)
    await tick(now=datetime.now(UTC) + timedelta(minutes=1), enqueue=q)
    assert not [x for x in q if x[0] == "sync"]
    flows = await _flows(erp)
    async with tenant_session(erp.tid) as s:
        (await s.get(SyncFlow, flows["order"].id)).run_requested_at = datetime.now(UTC)
    q = Enqueued(erp.tid)
    await tick(now=datetime.now(UTC) + timedelta(minutes=1), enqueue=q)
    assert [(w, t) for w, fid, t in q if fid == flows["order"].id] == [("sync", "manual")]
    q = Enqueued(erp.tid)
    await tick(now=datetime.now(UTC) + timedelta(minutes=6), enqueue=q)
    assert len([x for x in q if x[0] == "sync"]) == 7


# --- linking customers to accounts (safeguards 2 and 3) -------------------------------------

async def test_customers_link_on_verified_ids_and_ambiguity_waits_for_a_person(erp):
    async with tenant_session(erp.tid) as s:
        # Shares Bharat Forge's GSTIN: links automatically.
        bharat = await create_account(s, erp.tid, {"name": "Bharat Forge Components",
                                                   "gstin": "27AABCB1234F1Z8"},
                                      source="manual", actor="user:test")
        # Same name as Shakti Hydraulics but no IDs: a person decides.
        shakti = await create_account(s, erp.tid, {"name": "Shakti Hydraulics",
                                                   "city": "Faridabad"},
                                      source="manual", actor="user:test")
        # Same PAN as Kirloskar, other state's GSTIN: another registration -> review.
        kirloskar = await create_account(s, erp.tid, {"name": "Kirloskar Pumps & Valves",
                                                      "gstin": gstin("27", "AAACK9012M")},
                                         source="manual", actor="user:test")
    await sync_all(erp)
    rows = {c.name: c for c in await _all(erp, select(ErpCustomer))}
    b = rows["Bharat Forge Components Pvt Ltd"]
    assert (b.link_status, b.account_id, b.link_method) == ("linked", bharat.id, "gstin")
    assert rows["Shakti Hydraulics"].link_status == "review"
    assert rows["Shakti Hydraulics"].candidates[0]["account_id"] == str(shakti.id)
    k = rows["Kirloskar Pumps & Valves"]
    assert k.link_status == "review" and "another registration" in k.link_reasons[0]
    assert k.account_id is None and kirloskar.id
    # The rest matched nothing and became accounts marked as coming from the ERP.
    created = [c for c in rows.values() if c.link_method == "new_account"]
    assert len(created) == 5
    acc = await _scalar(erp, select(Account).where(Account.id == created[0].account_id))
    assert acc.source == "erp"
    # One task card says how many wait for a person.
    review = [t for t in await _open_cards(erp, "task") if "need matching" in t.title]
    assert len(review) == 1 and review[0].title.startswith("2 customers")

    # The linked account got the ERP's billing address, with the ERP record as source.
    async with tenant_session(erp.tid) as s:
        acc = await s.get(Account, bharat.id)
        assert acc.city == "Pune" and acc.pincode == "411018"
        prov = await s.scalar(select(FieldProvenance).where(
            FieldProvenance.record_id == acc.id, FieldProvenance.field == "city",
            FieldProvenance.status == "current"))
        assert prov.source == "erp" and prov.source_ref.endswith(":Bharat Forge Components Pvt Ltd")
    kinds = [a.kind for a in await _all(erp, select(AuditRecord).where(
        AuditRecord.agent_key == "erp_connector"))]
    assert "action.executed" in kinds

    # A person settles the review: link Shakti, ignore Kirloskar. Both audited.
    async with tenant_session(erp.tid) as s:
        ec = await s.get(ErpCustomer, rows["Shakti Hydraulics"].id)
        await linking.link_by_person(s, ec, shakti.id, "user:test", "same company, checked")
        ec = await s.get(ErpCustomer, k.id)
        await linking.ignore_by_person(s, ec, "user:test", "branch handled separately")
    assert not [t for t in await _open_cards(erp, "task") if "need matching" in t.title]
    kinds = [a.kind for a in await _all(erp, select(AuditRecord))]
    assert "erp.customer.linked" in kinds and "erp.customer.ignored" in kinds


async def test_two_erps_disagreeing_go_to_review_not_overwrite(erp):
    await sync_all(erp)
    bharat = await _scalar(erp, select(ErpCustomer).where(
        ErpCustomer.name == "Bharat Forge Components Pvt Ltd"))
    account_id = bharat.account_id
    # A second ERP has the same company (same GSTIN) with another billing address.
    other = FakeERPNext(api_key="k2", api_secret="s2")
    addr = other.add("Address", {"name": "BF-Chakan", "address_line1": "Gat 12, Chakan MIDC",
                                 "city": "Chakan", "state": "Maharashtra", "pincode": "410501",
                                 "country": "India"})
    other.add("Customer", {"name": "BFCPL", "customer_name": "Bharat Forge Components Pvt Ltd",
                           "customer_type": "Company", "tax_id": "27AABCB1234F1Z8",
                           "customer_primary_address": addr["name"]})
    e2 = await _connect(erp.tid, other, name="ERPNext Chakan")
    try:
        await sync_all(e2)
        ec2 = await _scalar(e2, select(ErpCustomer).where(ErpCustomer.connection_id == e2.conn_id))
        assert ec2.link_status == "linked" and ec2.account_id == account_id
        acc = await _scalar(erp, select(Account).where(Account.id == account_id))
        assert acc.city == "Pune"  # not silently overwritten
        cards = [d for d in await _open_cards(erp, "approval")
                 if d.tool_key == linking.UPDATE_TOOL and d.record_id == account_id]
        assert len(cards) == 1 and "billing address" in cards[0].title
        assert "two ERPs disagree" in cards[0].reasons[0]
        # Approving applies it, with the second ERP record as the new source.
        async with tenant_session(erp.tid) as s:
            d = await decide(s, cards[0].id, approve=True, actor="user:test")
            assert d.status == "approved", d.result
        acc = await _scalar(erp, select(Account).where(Account.id == account_id))
        assert acc.city == "Chakan"
    finally:
        use_transport(e2.url, None)


# --- creating a customer in the ERP (safeguard 1) -------------------------------------------

async def _new_account(e, name, gst=None) -> Account:
    async with tenant_session(e.tid) as s:
        return await create_account(s, e.tid, {"name": name, "gstin": gst}, source="manual",
                                    actor="user:test")


async def _request(e, acc, **kw):
    async with tenant_session(e.tid) as s:
        return await writes.request_create(s, e.tid, connection_id=e.conn_id,
                                           account_id=acc.id, actor="user:test", **kw)


async def test_create_customer_checks_duplicates_and_is_sent_once(erp):
    await sync_all(erp)
    acc = await _new_account(erp, "Vidarbha Castings Pvt Ltd", gstin("27", "AAFCV4321L"))
    result = await _request(erp, acc)
    assert result["outcome"] == "done", result
    created = [d for d in erp.fake.docs["Customer"].values()
               if d["customer_name"] == "Vidarbha Castings Pvt Ltd"]
    assert len(created) == 1 and created[0]["tax_id"] == acc.gstin
    assert "Forge ref: fw-" in created[0]["customer_details"]
    ec = await _scalar(erp, select(ErpCustomer).where(
        ErpCustomer.external_id == created[0]["name"]))
    assert ec.account_id == acc.id and ec.link_method == "created_in_erp"

    # Asking again does nothing: same idempotency key, already done.
    again = await _request(erp, acc)
    assert again["outcome"] == "already" and again["write"]["status"] == "succeeded"
    assert len([d for d in erp.fake.docs["Customer"].values()
                if d["customer_name"] == "Vidarbha Castings Pvt Ltd"]) == 1

    # An account whose GSTIN the ERP already has is refused, with the reason.
    dup = await _new_account(erp, "Anand Auto Electrical Works", "24AAFFA7890Q1ZS")
    refused = await _request(erp, dup)
    assert refused["outcome"] == "refused" and "same GSTIN" in refused["reasons"][0]
    kinds = [a.kind for a in await _all(erp, select(AuditRecord))]
    assert {"erp.write.requested", "erp.write.succeeded", "erp.write.refused"} <= set(kinds)


async def test_a_lost_answer_is_looked_up_not_sent_twice(erp):
    acc = await _new_account(erp, "Nagpur Rolling Mills", gstin("27", "AAGCN5555P"))
    erp.fake.lose_next_answer = True
    first = await _request(erp, acc)
    assert first["outcome"] == "failed" and "cannot be created twice" in first["error"]
    w = await _scalar(erp, select(ErpWrite).where(ErpWrite.account_id == acc.id))
    assert w.status == "unknown"
    second = await _request(erp, acc)
    assert second["outcome"] == "done"
    assert len([d for d in erp.fake.docs["Customer"].values()
                if d["customer_name"] == "Nagpur Rolling Mills"]) == 1
    w = await _scalar(erp, select(ErpWrite).where(ErpWrite.account_id == acc.id))
    assert w.status == "succeeded" and w.attempts == 1


async def test_the_write_goes_through_the_gate(erp):
    acc = await _new_account(erp, "Satara Pressings", gstin("27", "AAKCS7777Q"))
    async with tenant_session(erp.tid) as s:
        await controls.set_level(s, erp.tid, AGENTS["erp_connector"], 0, "user:test")
    waiting = await _request(erp, acc)
    assert waiting["outcome"] == "waiting" and waiting["write"]["status"] == "pending"
    assert not [d for d in erp.fake.docs["Customer"].values()
                if d["customer_name"] == "Satara Pressings"]
    async with tenant_session(erp.tid) as s:
        d = await decide(s, uuid.UUID(waiting["decision_id"]), approve=True, actor="user:boss")
        assert d.status == "approved", d.result
    assert [d for d in erp.fake.docs["Customer"].values()
            if d["customer_name"] == "Satara Pressings"]
    # A kill-switch on the agent stops it outright.
    other = await _new_account(erp, "Karad Engineering", gstin("27", "AAKCK8888R"))
    async with tenant_session(erp.tid) as s:
        await controls.set_level(s, erp.tid, AGENTS["erp_connector"], 2, "user:test")
        await controls.stop(s, erp.tid, "agent", "ERP maintenance", "user:test",
                            agent_key="erp_connector")
    blocked = await _request(erp, other)
    assert blocked["outcome"] == "blocked" and blocked["write"]["status"] == "refused"


# --- code lists (W1 A) ------------------------------------------------------------------------

async def test_codes_are_found_seeded_and_suggested(erp):
    await sync_all(erp)
    maps = {(m.list, m.erp_code): m for m in await _all(erp, select(CodeMap))}
    # The first ERP's groups become Forge's list, mapped to themselves.
    assert maps[("customer_group", "OEM")].status == "confirmed"
    assert maps[("customer_group", "OEM")].forge_code == "OEM"
    # Units and tax map onto the GST standards, as suggestions for a person.
    nos = maps[("uom", "Nos")]
    assert (nos.forge_code, nos.status, nos.suggestion_source) == ("NOS", "suggested", "exact")
    assert mapping.suggest("uom", "Kilogram", mapping.UQC).code == "KGS"
    assert mapping.suggest("uom", "Piece", mapping.UQC).source == "alias"
    tax = maps[("tax", "Output GST In-state 18%")]
    assert (tax.forge_code, tax.status) == ("GST_18", "suggested")
    async with tenant_session(erp.tid) as s:
        n = await mapping.confirm_many(s, [nos.id, tax.id], "user:test")
        assert n == 2
        m = await s.get(CodeMap, maps[("warehouse", "Stores - FI")].id)
        with pytest.raises(Exception, match="not in Forge"):
            await mapping.change(s, m, forge_code="Nowhere", actor="user:test")
    history = await _all(erp, select(CodeMapHistory).where(CodeMapHistory.code_map_id == nos.id))
    assert [h.status for h in sorted(history, key=lambda h: h.version)] == ["suggested",
                                                                            "confirmed"]
    changed = [a for a in await _all(erp, select(AuditRecord)) if a.kind == "mapping.changed"]
    assert len(changed) == 2

    # A second ERP's codes map onto the first one's lists.
    other = FakeERPNext(api_key="k3", api_secret="s3")
    other.add("Customer", {"name": "X1", "customer_name": "Hosur Gears", "customer_group": "oem",
                           "territory": "South India"})
    e2 = await _connect(erp.tid, other, name="ERPNext Hosur")
    try:
        await sync_all(e2)
        m2 = {(m.list, m.erp_code): m for m in await _all(e2, select(CodeMap).where(
            CodeMap.connection_id == e2.conn_id))}
        assert (m2[("customer_group", "oem")].forge_code,
                m2[("customer_group", "oem")].status) == ("OEM", "suggested")
        assert m2[("territory", "South India")].status in ("unmapped", "suggested")
    finally:
        use_transport(e2.url, None)
