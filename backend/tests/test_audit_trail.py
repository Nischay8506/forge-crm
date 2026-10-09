"""Step 4 (F12.4): the audit trail's evidence, seals, hidden personal data, what
agents were given, and agent versions (decisions R1-R4, R8 and Nischay's Step 4
safeguards)."""

import dataclasses
import json
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.agents import runtime, versions
from app.agents.registry import AGENTS
from app.audit import chain, evidence, redact, seals
from app.db import get_sessionmaker, tenant_session
from app.llm import gateway, routing
from app.llm.providers import ProviderResponse
from app.models import AgentRun, AgentVersion, AuditEvidence, AuditRecord, Contact, Tenant
from tests.conftest import OWNER_URL, login

API = "/api/v1"


@pytest.fixture
async def ctx(client, make_tenant):
    slug, email, pw = await make_tenant()
    async with get_sessionmaker()() as s:
        tid = (await s.scalar(select(Tenant).where(Tenant.slug == slug))).id
    return {"h": await login(client, slug, email, pw), "tid": tid, "slug": slug}


async def _try(client, h, amount, account_id=None):
    r = await client.post(f"{API}/agents/sandbox/try", headers=h,
                          json={"amount": amount, "account_id": account_id})
    assert r.status_code == 200, r.text
    return r.json()


async def _owner(tid, *sql: tuple[str, dict]):
    """Run statements as the database owner with the guard triggers switched off, the
    way someone with full server access could."""
    owner = create_async_engine(OWNER_URL)
    async with owner.begin() as conn:
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tid)})
        for t in ("audit_records", "audit_evidence", "audit_seals"):
            await conn.execute(text(f"ALTER TABLE {t} DISABLE TRIGGER USER"))
        for stmt, params in sql:
            await conn.execute(text(stmt), {"t": str(tid), **params})
        for t in ("audit_records", "audit_evidence", "audit_seals"):
            await conn.execute(text(f"ALTER TABLE {t} ENABLE TRIGGER USER"))
    await owner.dispose()


async def _verify(tid) -> chain.ChainCheck:
    async with tenant_session(tid) as s:
        return await chain.verify(s, tid)


# R1: skeleton + encrypted evidence ---------------------------------------------------

async def test_records_keep_a_skeleton_and_encrypted_details(client, ctx):
    acc = (await client.post(f"{API}/accounts", headers=ctx["h"],
                             json={"name": "Shree Ganesh Traders"})).json()["id"]
    await _try(client, ctx["h"], "1500", acc)
    async with tenant_session(ctx["tid"]) as s:
        proposed = await s.scalar(select(AuditRecord).where(AuditRecord.kind == "action.proposed"))
        ev = await s.scalar(select(AuditEvidence).where(AuditEvidence.seq == proposed.seq))
    assert proposed.format == 2 and proposed.evidence_digest == ev.digest
    # The skeleton says what happened; the details are elsewhere.
    assert {"tool", "version", "outcome", "level", "agent_version"} <= set(proposed.data)
    assert not {"args", "summary", "facts", "inputs", "checks"} & set(proposed.data)
    # The details are encrypted at rest, and labelled for retention and holds.
    assert ev.data_enc.startswith("v1.etest.") and "Ganesh" not in ev.data_enc
    assert ev.retention_class == "customer_money" and str(ev.account_id) == acc
    details = evidence.read(ev)
    assert details["args"]["amount"] == "1500" and len(details["_nonce"]) == 32
    # R2: the account as the tool saw it.
    [seen] = details["inputs"]
    assert seen["record_type"] == "account" and seen["content"]["name"] == "Shree Ganesh Traders"
    assert (await _verify(ctx["tid"])).ok


async def test_secrets_never_reach_the_trail_and_skeletons_hold_no_personal_data(ctx):
    tid = ctx["tid"]
    async with tenant_session(tid) as s:
        rec = await chain.append(s, tid, "test.event", actor="test", data={
            "api_secret": "s3cr3t-value", "note": "header was token abcdef123:ghijkl456",
            "status": "write to priya@example.com", "count": 3})
        ev = await s.scalar(select(AuditEvidence).where(AuditEvidence.seq == rec.seq))
        details = evidence.read(ev)
    assert rec.data == {"count": 3}  # "status" looked personal, so it moved
    assert details["status"] == "write to priya@example.com"
    assert details["api_secret"] == "[secret removed]"
    assert details["note"] == "header was [secret removed]"


async def test_changed_or_missing_details_are_caught_and_removal_is_allowed(client, ctx):
    tid = ctx["tid"]
    await _try(client, ctx["h"], "10")
    async with tenant_session(tid) as s:
        first = await s.scalar(select(AuditEvidence).order_by(AuditEvidence.seq).limit(1))
        seq = first.seq
    # The app may remove details with a reason, never change them.
    async with tenant_session(tid) as s:
        with pytest.raises(Exception, match="never changed"):
            async with s.begin_nested():
                await s.execute(text("UPDATE audit_evidence SET data_enc = NULL WHERE seq = :q"),
                                {"q": seq})
        with pytest.raises(Exception, match="never changed"):
            async with s.begin_nested():
                await s.execute(text("UPDATE audit_evidence SET retention_class = 'x' "
                                     "WHERE seq = :q"), {"q": seq})
        await s.execute(text("UPDATE audit_evidence SET data_enc = NULL, removed_at = now(), "
                             "removed_reason = 'test' WHERE seq = :q"), {"q": seq})
    v = await _verify(tid)
    assert v.ok and v.details_removed == 1

    # Someone with server access swaps in different details: the fingerprint catches it.
    nxt = seq + 1
    forged = evidence.encrypt(tid, nxt, json.dumps({"tool": "x", "_nonce": "0" * 32}))
    await _owner(tid, ("UPDATE audit_evidence SET data_enc = :e WHERE tenant_id = :t "
                       "AND seq = :q", {"e": forged, "q": nxt}))
    v = await _verify(tid)
    assert not v.ok and v.problem == f"the details of record {nxt} were changed after they " \
                                      "were written"
    await _owner(tid, ("DELETE FROM audit_evidence WHERE tenant_id = :t AND seq = :q",
                       {"q": nxt}))
    assert (await _verify(tid)).problem == f"the details of record {nxt} are missing"


# R3: chained, signed seals ------------------------------------------------------------

async def test_seals_catch_a_rewritten_history(client, ctx):
    tid = ctx["tid"]
    for a in ("1", "2"):
        await _try(client, ctx["h"], a)
    async with tenant_session(tid) as s:
        s1 = await seals.seal_tenant(s, tid)
        assert s1 is not None and s1.prev_signature is None
        assert await seals.seal_tenant(s, tid) is None  # nothing new to seal
    await _try(client, ctx["h"], "3")
    async with tenant_session(tid) as s:
        s2 = await seals.seal_tenant(s, tid)
        assert s2.prev_signature == s1.signature and seals.check(tid, s2) == "ok"
    v = await _verify(tid)
    assert v.ok and v.seals_checked == 2 and v.sealed_upto == s2.seq

    # Rewrite record 2 and recompute every hash after it: the chain alone looks fine,
    # but the seals no longer match.
    async with tenant_session(tid) as s:
        recs = list(await s.scalars(select(AuditRecord).order_by(AuditRecord.seq)))
    prev = recs[0].hash
    stmts = []
    for r in recs[1:]:
        if r.seq == 2:
            r.data = {**r.data, "outcome": "act", "level": 3}
        r.prev_hash = prev
        r.hash = chain.compute_hash(r)
        prev = r.hash
        stmts.append(("UPDATE audit_records SET data = CAST(:d AS jsonb), prev_hash = :p, "
                      "hash = :h WHERE tenant_id = :t AND seq = :q",
                      {"d": json.dumps(r.data), "p": r.prev_hash, "h": r.hash, "q": r.seq}))
    await _owner(tid, *stmts)
    v = await _verify(tid)
    assert not v.ok and v.first_bad_seq == s1.seq
    assert v.problem.startswith(f"record {s1.seq} no longer matches its seal")


async def test_missing_reordered_or_forged_seals_and_cut_records_are_caught(client, ctx):
    tid = ctx["tid"]
    made = []
    for a in ("1", "2", "3"):
        await _try(client, ctx["h"], a)
        async with tenant_session(tid) as s:
            made.append(await seals.seal_tenant(s, tid))
    assert (await _verify(tid)).ok
    # A seal removed from the middle.
    await _owner(tid, ("DELETE FROM audit_seals WHERE tenant_id = :t AND seq = :q",
                       {"q": made[1].seq}))
    v = await _verify(tid)
    assert not v.ok and "missing, or the seals were reordered" in v.problem
    # Put it back with a forged signature.
    await _owner(tid, ("INSERT INTO audit_seals (tenant_id, seq, hash, sealed_at, key_id, "
                       "signature, prev_signature) VALUES (:t, :q, :h, :a, :k, :sig, :p)",
                       {"q": made[1].seq, "h": made[1].hash, "a": made[1].sealed_at,
                        "k": made[1].key_id, "sig": "0" * 64,
                        "p": made[1].prev_signature}))
    assert (await _verify(tid)).problem == f"a seal after the seal on record {made[1].seq} " \
                                           f"before the seal on record {made[2].seq} is " \
                                           "missing, or the seals were reordered"
    await _owner(tid, ("UPDATE audit_seals SET signature = :sig WHERE tenant_id = :t AND "
                       "seq = :q", {"sig": made[1].signature, "q": made[1].seq}))
    assert (await _verify(tid)).ok
    # Records cut from the end: the last seal shows they existed.
    last = made[2].seq
    await _owner(tid, ("DELETE FROM audit_evidence WHERE tenant_id = :t AND seq >= :q",
                       {"q": last - 1}),
                 ("DELETE FROM audit_records WHERE tenant_id = :t AND seq >= :q",
                  {"q": last - 1}))
    v = await _verify(tid)
    assert not v.ok and v.problem == f"records {last - 1} to {last} are missing (a seal " \
                                     "shows they existed)"


async def test_seal_keys_rotate_and_unknown_keys_are_reported(client, ctx, monkeypatch):
    from app.config import get_settings

    tid = ctx["tid"]
    await _try(client, ctx["h"], "1")
    async with tenant_session(tid) as s:
        old = await seals.seal_tenant(s, tid)
    old_spec = get_settings().audit_seal_keys
    monkeypatch.setenv("AUDIT_SEAL_KEYS", f"{seals.new_seal_key('snew')},{old_spec}")
    get_settings.cache_clear()
    try:
        await _try(client, ctx["h"], "2")
        async with tenant_session(tid) as s:
            new = await seals.seal_tenant(s, tid)
        assert new.key_id == "snew" and old.key_id == "stest"
        v = await _verify(tid)
        assert v.ok and v.seals_checked == 2
        # With the old key gone its seal can't be checked; it is reported, not hidden.
        monkeypatch.setenv("AUDIT_SEAL_KEYS", get_settings().audit_seal_keys.split(",")[0])
        get_settings.cache_clear()
        v = await _verify(tid)
        assert v.ok and v.unknown_seal_keys == ["stest"]
    finally:
        monkeypatch.setenv("AUDIT_SEAL_KEYS", old_spec)
        get_settings.cache_clear()


async def test_trim_needs_an_old_sealed_record_and_the_chain_still_verifies(client, ctx):
    tid = ctx["tid"]
    for a in ("1", "2"):
        await _try(client, ctx["h"], a)
    async with tenant_session(tid) as s:
        seal = await seals.seal_tenant(s, tid)
    await _try(client, ctx["h"], "3")
    async with tenant_session(tid) as s:
        with pytest.raises(Exception, match="younger than 96 months"):
            async with s.begin_nested():
                await s.execute(text("SELECT audit_trim(:t, :q)"), {"t": tid, "q": seal.seq})
        with pytest.raises(Exception, match="not sealed"):
            async with s.begin_nested():
                await s.execute(text("SELECT audit_trim(:t, :q)"), {"t": tid, "q": 1})
        # The app role cannot delete records itself, even with the trim flag set.
        with pytest.raises(Exception, match="permission denied|append-only"):
            async with s.begin_nested():
                await s.execute(text("SELECT set_config('forge.audit_trim', 'on', true)"))
                await s.execute(text("DELETE FROM audit_records WHERE seq = 1"))
    # Pretend the sealed records are nine years old (they are about to go anyway).
    await _owner(tid, ("UPDATE audit_records SET created_at = now() - interval '9 years' "
                       "WHERE tenant_id = :t AND seq <= :q", {"q": seal.seq}))
    async with tenant_session(tid) as s:
        n = await s.scalar(text("SELECT audit_trim(:t, :q)"), {"t": tid, "q": seal.seq})
    assert n == seal.seq
    v = await _verify(tid)
    assert v.ok and v.starts_at == seal.seq + 1 and v.checked >= 1


# R4: personal data hidden by default -------------------------------------------------

def test_redaction_finds_personal_data_but_not_company_data():
    shown, n = redact.redact({
        "full_name": "Priya Raman",
        "summary": "Call Priya Raman on +91 98450 12345 or 9845012345, mail priya@x.in",
        "pan": "ABCPR1234K is hers; AAACR5055K is the company's",
        "gstin": "29ABCPR1234K1Z5 (proprietor) and 29AAACR5055K1ZK (company)",
        "aadhaar": "2341 2341 2346",
        "note": "Aadhaar 234123412346 and order 123412341234",
        "city": "Hosur", "amount": "1500000", "account_id": "9845012345",
    })
    assert shown["full_name"] == "[name hidden]"
    assert shown["summary"] == ("Call [personal data hidden] on [phone hidden] or [phone hidden], "
                                "mail [email hidden]")
    assert shown["pan"] == "[PAN hidden] is hers; AAACR5055K is the company's"
    assert shown["gstin"] == "[GSTIN hidden] (proprietor) and 29AAACR5055K1ZK (company)"
    assert shown["aadhaar"] == "[Aadhaar hidden]"
    assert shown["note"] == "Aadhaar [Aadhaar hidden] and order 123412341234"
    assert (shown["city"], shown["amount"], shown["account_id"]) == ("Hosur", "1500000",
                                                                     "9845012345")
    assert n == 9
    assert redact.verhoeff_ok("234123412346") and not redact.verhoeff_ok("234123412345")


def test_redaction_hides_name_parts_but_keeps_company_names():
    r = redact.Redactor()
    r.collect({"full_name": "Dr Meera Sharma", "account_name": "Sharma Traders"})
    out = r.apply({
        "reason": "Meera asked to talk to a person; Sharma Traders said the same",
        "note": "Ms Sharma called. A rose for meera? Dr is not a name.",
        "title": "Erase Meera Sharma's data",
    })
    assert out["reason"] == "[name hidden] asked to talk to a person; Sharma Traders said the same"
    # Name parts match with their capital letter; the title "Dr" is not a name part.
    assert out["note"] == "Ms [name hidden] called. A rose for meera? Dr is not a name."
    assert out["title"] == "Erase [personal data hidden]'s data"
    assert r.hidden == 3


async def test_replay_hides_personal_data_until_revealed_with_a_reason(client, ctx):
    from tests.test_agents import _user_with_role

    h = ctx["h"]
    acc = (await client.post(f"{API}/accounts", headers=h, json={"name": "Raman Stores"})).json()
    r = await client.post(f"{API}/contacts", headers=h, json={
        "account_id": acc["id"], "full_name": "Priya Raman", "email": "priya@ramanstores.in",
        "phone": "9845012345"})
    assert r.status_code == 201, r.text
    contact_id = uuid.UUID(r.json()["id"])
    # An agent run that saw the contact.
    async with tenant_session(ctx["tid"]) as s:
        c = await runtime.start_run(s, ctx["tid"], "sandbox", "test")
        c.saw("contact", {"full_name": "Priya Raman", "email": "priya@ramanstores.in"},
              record_type="contact", record_id=contact_id)
        await runtime.call_tool(s, c, "sandbox.record_payment",
                                {"amount": "5", "account_id": acc["id"]})
        await runtime.finish_run(s, c)
        run_id = c.run_id
    rep = (await client.get(f"{API}/audit/runs/{run_id}", headers=h)).json()
    blob = json.dumps(rep)
    assert "Priya" not in blob and "priya@" not in blob and "9845012345" not in blob
    assert rep["personal_data"]["hidden"] >= 2 and not rep["personal_data"]["revealed"]
    log = (await client.get(f"{API}/audit?run_id={run_id}", headers=h)).json()
    assert "Priya" not in json.dumps(log)

    # Revealing needs the permission and a reason, and is itself recorded.
    _, plain = await _user_with_role(client, ctx, "Auditor", ["audit.read"])
    r = await client.post(f"{API}/audit/runs/{run_id}/reveal", headers=plain,
                          json={"reason": "Customer complaint 42 review"})
    assert r.status_code == 403
    _, dpo = await _user_with_role(client, ctx, "Privacy Lead",
                                   ["audit.read", "audit.personal_data"])
    assert (await client.post(f"{API}/audit/runs/{run_id}/reveal", headers=dpo,
                              json={"reason": "why"})).status_code == 422
    r = await client.post(f"{API}/audit/runs/{run_id}/reveal", headers=dpo,
                          json={"reason": "Customer complaint 42 review"})
    assert r.status_code == 200 and r.json()["personal_data"]["revealed"]
    assert "priya@ramanstores.in" in json.dumps(r.json())
    log = (await client.get(f"{API}/audit?kind=audit.personal_data_revealed", headers=h)).json()
    assert log[0]["record_id"] == str(run_id) and log[0]["record_type"] == "agent_run"
    assert log[0]["data"]["reason"] == "Customer complaint 42 review"


# R2: model calls keep the prompt and the answer --------------------------------------

class _Model:
    async def chat(self, model, messages, **kw):
        return ProviderResponse(text="Looks like the same company.", input_tokens=120,
                                output_tokens=8)


async def test_model_calls_keep_the_prompt_and_answer(ctx, monkeypatch):
    monkeypatch.setattr(gateway.providers_mod, "PROVIDERS", {"fake": _Model()})
    monkeypatch.setattr(routing, "ROUTES", {"fast": [routing.Route("fake", "m1")]})
    agent = dataclasses.replace(AGENTS["sandbox"], model_tier="fast")
    tid = ctx["tid"]
    async with tenant_session(tid) as s:
        c = await runtime.start_run(s, tid, "sandbox", "test")
        c.agent = agent
        c.saw("knowledge", "Passage: Raman Stores is a Hosur retailer.")
        res = await runtime.ask_model(s, c, task="steward.compare", temperature=0, messages=[
            {"role": "user", "content": "Are 'Raman Stores' and 'Raman Store' the same?"}])
        assert res.text == "Looks like the same company."
        rec = await s.scalar(select(AuditRecord).where(AuditRecord.kind == "model.called"))
        ev = evidence.read(await s.scalar(select(AuditEvidence)
                                          .where(AuditEvidence.seq == rec.seq)))
    assert rec.run_id == c.run_id
    assert {"model": "m1", "provider": "fake", "input_tokens": 120,
            "output_tokens": 8}.items() <= rec.data.items()
    assert ev["messages"][0]["content"].startswith("Are 'Raman Stores'")
    assert ev["answer"] == "Looks like the same company."
    assert ev["inputs"][0]["content"].startswith("Passage:") and ev["settings"] == {
        "temperature": 0}


# R8: agent versions ------------------------------------------------------------------

async def test_agent_versions_follow_the_definition(ctx, monkeypatch):
    tid = ctx["tid"]
    async with tenant_session(tid) as s:
        c = await runtime.start_run(s, tid, "sandbox", "test")
        run = await s.get(AgentRun, c.run_id)
        v1 = run.agent_version
        assert v1 is not None and c.agent_version == v1
    # A changed charter is a new version; going back finds the old number again.
    changed = dataclasses.replace(AGENTS["sandbox"], purpose="Test only, now with notes.")
    monkeypatch.setitem(AGENTS, "sandbox", changed)
    async with tenant_session(tid) as s:
        c2 = await runtime.start_run(s, tid, "sandbox", "test")
    assert c2.agent_version == v1 + 1
    async with get_sessionmaker()() as s:
        rows = {v.number: v for v in await s.scalars(
            select(AgentVersion).where(AgentVersion.agent_key == "sandbox"))}
    assert versions.describe_change(rows[v1].definition, rows[v1 + 1].definition) == [
        "Charter changed"]
    monkeypatch.undo()
    async with tenant_session(tid) as s:
        assert (await runtime.start_run(s, tid, "sandbox", "test")).agent_version == v1


async def test_verify_warns_when_sealing_has_stopped(ctx, monkeypatch):
    tid = ctx["tid"]

    class _Then(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(chain, "datetime", _Then)
    async with tenant_session(tid) as s:
        await chain.append(s, tid, "test.event", actor="test", data={"count": 1})
    monkeypatch.undo()
    v = await _verify(tid)
    assert v.ok and "not sealed yet" in v.warning
    async with tenant_session(tid) as s:
        await seals.seal_tenant(s, tid)
    v = await _verify(tid)
    assert v.ok and v.warning is None


async def test_evidence_tab_counts_by_month_and_version_with_links(client, ctx):
    from tests.test_agents import _rule, checker, decide

    h = ctx["h"]
    await _rule(client, h, conditions=[{"fact": "amount", "op": "gt", "value": 1000}],
                outcome="ask_person")
    await _rule(client, h, conditions=[{"fact": "amount", "op": "gt", "value": 5000}],
                outcome="block")
    assert (await _try(client, h, "10"))["status"] == "done"
    yes, no = await _try(client, h, "2000"), await _try(client, h, "3000")
    assert (await _try(client, h, "9000"))["status"] == "blocked"
    other = await checker(client, ctx)
    assert (await decide(client, other, yes["decision_id"])).status_code == 200
    assert (await decide(client, other, no["decision_id"], "reject",
                         note="Too much for a test")).status_code == 200
    card = (await client.get(f"{API}/agents/sandbox/scorecard", headers=h)).json()
    [row] = card["rows"]
    assert row["month"] == datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m")
    assert {k: row[k] for k in ("proposed", "acted_alone", "asked", "approved", "rejected",
                                "undone", "blocked", "failed")} == {
        "proposed": 4, "acted_alone": 1, "asked": 2, "approved": 1, "rejected": 1,
        "undone": 0, "blocked": 1, "failed": 0}
    assert row["override_rate"] == round(1 / 3, 4) and row["version"] >= 1
    assert card["versions"][-1]["changes"] == ["First version"]
    # Each number opens the runs behind it.
    runs = (await client.get(f"{API}/audit/runs", headers=h, params={
        "agent_key": "sandbox", "outcome": "rejected", "version": row["version"],
        "month": row["month"]})).json()
    assert len(runs) == 1 and runs[0]["tally"]["asked"] == 1
    # Good numbers never raise the level by themselves.
    assert (await client.get(f"{API}/agents/sandbox", headers=h)).json()["code"] == "L2"


# R5: retention and legal holds --------------------------------------------------------

async def test_retention_settings_are_editable_with_floors_and_a_legal_review(client, ctx):
    h = ctx["h"]
    r = (await client.get(f"{API}/audit/retention", headers=h)).json()
    assert r["profile"] == "india" and r["is_default"] and r["legal_review"] is None
    assert r["periods"] == {"skeleton": 96, "customer_money": 96, "other": 36,
                            "sync_logs": 12, "model_logs": 12}
    assert "s.128(5)" in r["basis"]["skeleton"]
    bad = await client.put(f"{API}/audit/retention", headers=h, json={"periods": {"skeleton": 60}})
    assert bad.status_code == 422 and "at least 8 years" in bad.text
    bad = await client.put(f"{API}/audit/retention", headers=h, json={"periods": {"other": 6}})
    assert bad.status_code == 422 and "at least 12 months" in bad.text
    r = (await client.put(f"{API}/audit/retention", headers=h, json={
        "periods": {"other": 48}, "basis": {"other": "Our contracts: 4 years"}})).json()
    assert r["periods"]["other"] == 48 and not r["is_default"]
    r = (await client.post(f"{API}/audit/retention/legal-review", headers=h, json={
        "reviewed_by": "R. Iyer & Co, Chartered Accountants", "note": "Checked 2026"})).json()
    assert r["legal_review"]["reviewed_by"].startswith("R. Iyer")
    # Changing a period again means the confirmation no longer covers it.
    r = (await client.put(f"{API}/audit/retention", headers=h,
                          json={"periods": {"sync_logs": 24}})).json()
    assert r["legal_review"] is None
    kinds = [a["kind"] for a in (await client.get(f"{API}/audit?kind=retention.",
                                                   headers=h)).json()]
    assert kinds == ["retention.changed", "retention.reviewed", "retention.changed"]


async def test_nightly_retention_removes_old_details_but_respects_legal_holds(client, ctx):
    from app.audit import retention
    from app.models import LlmCall
    from tests.test_agents import _user_with_role

    h, tid = ctx["h"], ctx["tid"]
    kept = (await client.post(f"{API}/accounts", headers=h, json={"name": "Disputed Ltd"})).json()
    gone = (await client.post(f"{API}/accounts", headers=h, json={"name": "Quiet Ltd"})).json()
    await _try(client, h, "10", kept["id"])
    await _try(client, h, "20", gone["id"])
    # Placing a hold needs "audit.manage".
    _, sales = await _user_with_role(client, ctx, "Sales", ["accounts.read"])
    body = {"account_id": kept["id"], "reason": "Payment dispute, notice received"}
    assert (await client.post(f"{API}/audit/legal-holds", headers=sales,
                              json=body)).status_code == 403
    hold = await client.post(f"{API}/audit/legal-holds", headers=h, json=body)
    assert hold.status_code == 201
    assert (await client.post(f"{API}/audit/legal-holds", headers=h,
                              json=body)).status_code == 422
    holds = (await client.get(f"{API}/audit/legal-holds?account_id={kept['id']}",
                              headers=sales)).json()
    assert holds[0]["account_name"] == "Disputed Ltd"
    # Everything is now nine years old.
    await _owner(tid, ("UPDATE audit_evidence SET created_at = now() - interval '9 years' "
                       "WHERE tenant_id = :t", {}))
    async with tenant_session(tid) as s:
        s.add(LlmCall(tenant_id=tid, task="t", tier="fast", provider="p", model="m",
                      status="ok", created_at=datetime(2020, 1, 1, tzinfo=UTC)))
    r = (await client.post(f"{API}/audit/retention/run", headers=h)).json()["removed"]
    assert r["customer_money"] >= 2 and r["llm_calls"] == 1 and r["skeleton_records"] == 0
    async with tenant_session(tid) as s:
        evs = list(await s.scalars(select(AuditEvidence).where(
            AuditEvidence.account_id.in_([uuid.UUID(kept["id"]), uuid.UUID(gone["id"])]))))
    by_account = {}
    for e in evs:
        by_account.setdefault(str(e.account_id), set()).add(e.data_enc is None)
    assert by_account[kept["id"]] == {False}  # held: untouched
    assert by_account[gone["id"]] == {True}
    assert {e.removed_reason for e in evs if e.data_enc is None} == {
        "retention: customer_money older than 96 months"}
    v = await _verify(tid)
    assert v.ok and v.details_removed >= 2
    run = (await client.get(f"{API}/audit?kind=retention.run", headers=h)).json()[0]
    assert run["data"]["removed"]["llm_calls"] == 1 and run["actor"] == "system:retention"
    # Lifting the hold lets the next run remove the rest.
    lift = await client.post(f"{API}/audit/legal-holds/{hold.json()['id']}/lift", headers=h,
                             json={"note": "Settled"})
    assert lift.status_code == 200 and lift.json()["lifted_by"]
    async with tenant_session(tid) as s:
        again = await retention.run_tenant(s, tid)
    assert again["customer_money"] >= 1


# R6: erasure with a second person ------------------------------------------------------

async def test_contact_erasure_needs_a_second_person_and_keeps_what_the_law_needs(client, ctx):
    from app.privacy import erasure
    from tests.test_agents import _user_with_role, decide

    h, tid = ctx["h"], ctx["tid"]
    acc = (await client.post(f"{API}/accounts", headers=h, json={"name": "Raman Stores"})).json()
    contact = (await client.post(f"{API}/contacts", headers=h, json={
        "account_id": acc["id"], "full_name": "Priya Raman", "email": "priya@ramanstores.in",
        "phone": "9845012345", "designation": "Owner"})).json()
    cid = uuid.UUID(contact["id"])
    # The trail mentions her twice: in what an agent saw (other) and in a payment
    # for her account (customer/money, which the law keeps).
    async with tenant_session(tid) as s:
        c = await runtime.start_run(s, tid, "sandbox", "test")
        c.saw("note", "Priya Raman asked for a refund on 9845012345")
        await runtime.call_tool(s, c, "sandbox.record_payment",
                                {"amount": "5", "account_id": acc["id"]})
        await chain.append(s, tid, "test.note", actor="test", record_type="contact",
                           record_id=cid, data={"note": "Spoke to Priya Raman today"})

    req = await client.post(f"{API}/privacy/erasures", headers=h,
                            json={"contact_id": str(cid), "reason": "Priya Raman asked us"})
    assert req.status_code == 201 and req.json()["status"] == "waiting"
    did = req.json()["decision_id"]
    card = (await client.get(f"{API}/decisions/{did}", headers=h)).json()
    assert "Priya" not in card["title"] and "Raman Stores" in card["title"]
    assert not card["can_decide"] and card["why_not"].startswith("A second person")
    own = await decide(client, h, did)
    assert own.status_code == 403 and "you asked for it" in own.text
    _, clerk = await _user_with_role(client, ctx, "Clerk", ["decisions.decide"])
    assert (await decide(client, clerk, did)).status_code == 403
    _, dpo = await _user_with_role(client, ctx, "Privacy Officer",
                                   ["decisions.decide", "privacy.erase"])
    ok = await decide(client, dpo, did)
    assert ok.status_code == 200 and ok.json()["status"] == "approved", ok.text
    result = ok.json()["result"]["result"]
    assert result["stores"]["contacts"] == 1 and result["stores"]["audit_details"] >= 2
    assert result["kept"]["audit_details_required_by_law"] >= 1

    async with tenant_session(tid) as s:
        c = await s.get(Contact, cid)
        assert (c.full_name, c.email, c.phone, c.status) == ("Erased contact", None, None,
                                                              "erased")
        removed = list(await s.scalars(select(AuditEvidence).where(
            AuditEvidence.removed_reason == "erasure of a contact's personal data")))
        assert len(removed) >= 2
        left = [evidence.read(e) for e in await s.scalars(select(AuditEvidence).where(
            AuditEvidence.data_enc.is_not(None)))]
        mask = await erasure.mask_for(s, tid)
    # Only the payment's details still mention her (the law keeps money records until
    # their period ends), and the erasure record itself names nobody.
    still = [e for e in left if "Priya" in json.dumps(e)]
    assert still and all(e["args"]["amount"] == "5" for e in still)
    log = (await client.get(f"{API}/audit?kind=contact.erased", headers=h)).json()[0]
    assert log["data"]["stores"]["contacts"] == 1 and log["data"]["reason"] == "[erased] asked us"
    assert (await _verify(tid)).ok
    # Later ERP syncs keep her email and phone hidden.
    assert mask({"email": "Priya@RamanStores.in", "mobile": "+91 98450 12345",
                 "other": "someone@else.in"}) == {"email": "[erased]", "mobile": "[erased]",
                                                  "other": "someone@else.in"}
    again = await client.post(f"{API}/privacy/erasures", headers=h,
                              json={"contact_id": str(cid), "reason": "again please"})
    assert again.status_code == 422


async def test_erasure_waits_for_a_legal_hold_to_be_lifted(client, ctx):
    h = ctx["h"]
    acc = (await client.post(f"{API}/accounts", headers=h, json={"name": "Held Ltd"})).json()
    contact = (await client.post(f"{API}/contacts", headers=h, json={
        "account_id": acc["id"], "full_name": "Arun Kumar"})).json()
    assert (await client.post(f"{API}/audit/legal-holds", headers=h, json={
        "account_id": acc["id"], "reason": "Court order pending"})).status_code == 201
    r = await client.post(f"{API}/privacy/erasures", headers=h,
                          json={"contact_id": contact["id"], "reason": "Asked to be forgotten"})
    assert r.status_code == 422 and "legal hold" in r.text


# R7: a message traced back to everything behind it ------------------------------------

async def test_a_test_message_traces_back_to_its_run_inputs_and_takeovers(client, ctx):
    h = ctx["h"]
    acc = (await client.post(f"{API}/accounts", headers=h, json={"name": "Velan Pumps"})).json()
    con = (await client.post(f"{API}/contacts", headers=h, json={
        "account_id": acc["id"], "full_name": "Meena Velan",
        "email": "meena@velanpumps.in"})).json()
    # A person held the account back from agents for a while.
    t = (await client.post(f"{API}/takeovers", headers=h, json={
        "record_type": "account", "record_id": acc["id"], "reason": "Renegotiating"})).json()
    blocked = (await client.post(f"{API}/agents/sandbox/draft-message", headers=h, json={
        "account_id": acc["id"], "contact_id": con["id"]})).json()
    assert blocked["status"] == "blocked" and blocked["message_id"] is None
    assert (await client.post(f"{API}/takeovers/{t['id']}/hand-back",
                              headers=h)).status_code == 200
    r = (await client.post(f"{API}/agents/sandbox/draft-message", headers=h, json={
        "account_id": acc["id"], "contact_id": con["id"], "channel": "whatsapp",
        "body": "Hi Meena Velan, your pump order is ready."})).json()
    assert r["status"] == "done" and r["message_id"]
    msgs = (await client.get(f"{API}/messages?account_id={acc['id']}", headers=h)).json()
    assert [(m["status"], m["ai_generated"], m["disclosure"], m["sent_at"]) for m in msgs] == [
        ("draft", True, "Sent by our AI assistant", None)]

    p = (await client.get(f"{API}/audit/messages/{r['message_id']}", headers=h)).json()
    assert p["message"]["body"] == "Hi [personal data hidden], your pump order is ready."
    assert p["message"]["contact_name"] == "[name hidden]"
    assert p["run"]["run"]["id"] == r["run_id"] and p["run"]["integrity"]["ok"]
    kinds = [s["kind"] for s in p["run"]["steps"]]
    assert kinds == ["action.proposed", "action.executed"]
    proposed = p["run"]["steps"][0]["data"]
    assert proposed["outcome"] == "act" and proposed["agent_version"] >= 1
    assert {i["record_type"] for i in proposed["inputs"]} == {"account", "contact"}
    assert [s["kind"] for s in p["takeovers"]] == ["takeover.started", "takeover.released"]
    assert p["active_takeovers"] == []
    assert "Meena" not in json.dumps(p)
