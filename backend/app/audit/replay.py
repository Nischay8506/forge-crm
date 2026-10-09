"""Replay (F12.4): an agent run rebuilt step by step from the audit chain.

A run's steps are its audit records (same run_id), shown with the run itself, the
decision cards it raised and the model calls made under it. Each step's hash is
recomputed, its details checked against their fingerprint and its link to the
record before it checked, so the replay says whether these steps are intact;
/audit/verify still checks the whole chain. A replay only reads what was stored:
nothing is re-run (Step 4 safeguard 6).

Each step's data is its skeleton plus its decrypted details ("evidence", R1).
Personal data is hidden unless the caller asked to reveal it (R4): see shown().
"""

import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import AGENTS, TOOLS
from app.audit import evidence as ev_crypto
from app.audit.chain import GENESIS, compute_hash, evidence_for, evidence_problem
from app.audit.redact import Redactor
from app.core.errors import NotFound
from app.models import (
    Account,
    AgentRun,
    AuditEvidence,
    AuditRecord,
    Contact,
    Decision,
    ErpConnection,
    LlmCall,
)

# How each step kind counts in a run's tally.
TALLY = {"action.executed": "done", "decision.requested": "asked", "action.blocked": "blocked",
         "action.failed": "failed"}


async def record_name(session: AsyncSession, record_type: str | None,
                      record_id: uuid.UUID | None) -> str | None:
    if not record_id:
        return None
    if record_type == "account":
        return await session.scalar(select(Account.name).where(Account.id == record_id))
    if record_type == "contact":
        return await session.scalar(select(Contact.full_name).where(Contact.id == record_id))
    if record_type == "erp_connection":
        return await session.scalar(select(ErpConnection.name)
                                    .where(ErpConnection.id == record_id))
    return None


def _evidence(a: AuditRecord, ev: AuditEvidence | None
              ) -> tuple[dict[str, Any], dict[str, Any]]:
    """(details, what state they are in) for one record."""
    if (a.format or 1) < 2:
        return {}, {"state": "inline"}  # Step 2 records: everything is in data
    if a.evidence_digest is None:
        return {}, {"state": "none"}
    if ev is None:
        return {}, {"state": "missing"}
    if ev.data_enc is None:
        return {}, {"state": "removed", "removed_at": ev.removed_at,
                    "reason": ev.removed_reason}
    try:
        data = ev_crypto.read(ev) or {}
    except ev_crypto.EvidenceKeyError as e:
        return {}, {"state": "unreadable", "reason": str(e)}
    data.pop("_nonce", None)
    return data, {"state": "present", "class": ev.retention_class}


def audit_row(a: AuditRecord, ev: AuditEvidence | None = None) -> dict[str, Any]:
    details, info = _evidence(a, ev)
    return {"seq": a.seq, "created_at": a.created_at, "actor": a.actor, "kind": a.kind,
            "agent_key": a.agent_key, "run_id": str(a.run_id) if a.run_id else None,
            "record_type": a.record_type,
            "record_id": str(a.record_id) if a.record_id else None,
            "data": {**a.data, **details}, "evidence": info, "format": a.format or 1,
            "prev_hash": a.prev_hash, "hash": a.hash}


async def audit_rows(session: AsyncSession, records: list[AuditRecord]) -> list[dict[str, Any]]:
    """Rows with their details, for records of one tenant."""
    if not records:
        return []
    evs = await evidence_for(session, records[0].tenant_id,
                             [r.seq for r in records if r.evidence_digest])
    return [audit_row(r, evs.get(r.seq)) for r in records]


def shown(rows: list[dict[str, Any]], *, extra: list[dict[str, Any]] | None = None,
          names: list[str] | None = None, reveal: bool = False) -> dict[str, Any]:
    """Hide personal data in rows' data (and in `extra` dicts, such as decision cards)
    in place, unless revealing. `names` are known personal names to hide anywhere."""
    if reveal:
        return {"hidden": 0, "revealed": True}
    r = Redactor()
    for row in rows:
        r.collect(row["data"])
    for item in extra or []:
        r.collect(item)
    for n in names or []:
        r.collect(n, "full_name")
    for row in rows:
        row["data"] = r.apply(row["data"])
    for item in extra or []:
        item.update(r.apply(item))
    return {"hidden": r.hidden, "revealed": False}


def _run_row(r: AgentRun, tally: dict[str, int], waiting: int) -> dict[str, Any]:
    agent = AGENTS.get(r.agent_key)
    return {"id": str(r.id), "agent_key": r.agent_key,
            "agent_name": agent.name if agent else r.agent_key, "trigger": r.trigger,
            "trigger_ref": r.trigger_ref, "status": r.status, "started_at": r.started_at,
            "finished_at": r.finished_at, "summary": r.summary,
            "tally": {k: tally.get(k, 0) for k in ("done", "asked", "blocked", "failed")},
            "waiting": waiting}


async def _tallies(session: AsyncSession, run_ids: list[uuid.UUID]
                   ) -> tuple[dict[uuid.UUID, dict[str, int]], dict[uuid.UUID, int]]:
    tally: dict[uuid.UUID, dict[str, int]] = defaultdict(dict)
    if not run_ids:
        return tally, {}
    for run_id, kind, n in (await session.execute(
            select(AuditRecord.run_id, AuditRecord.kind, func.count())
            .where(AuditRecord.run_id.in_(run_ids), AuditRecord.kind.in_(TALLY))
            .group_by(AuditRecord.run_id, AuditRecord.kind))).all():
        tally[run_id][TALLY[kind]] = n
    waiting = dict((await session.execute(
        select(Decision.run_id, func.count())
        .where(Decision.run_id.in_(run_ids), Decision.kind == "approval",
               Decision.status == "pending")
        .group_by(Decision.run_id))).all())
    return tally, waiting


async def list_runs(session: AsyncSession, *, agent_key: str | None = None,
                    record_id: uuid.UUID | None = None, before: datetime | None = None,
                    limit: int = 50) -> list[dict[str, Any]]:
    """Newest first. `record_id` keeps runs that touched that record."""
    stmt = select(AgentRun)
    if agent_key:
        stmt = stmt.where(AgentRun.agent_key == agent_key)
    if record_id:
        stmt = stmt.where(AgentRun.id.in_(select(AuditRecord.run_id).where(
            AuditRecord.record_id == record_id, AuditRecord.run_id.is_not(None))))
    if before:
        stmt = stmt.where(AgentRun.started_at < before)
    runs = list(await session.scalars(stmt.order_by(AgentRun.started_at.desc()).limit(limit)))
    tally, waiting = await _tallies(session, [r.id for r in runs])
    return [_run_row(r, tally.get(r.id, {}), waiting.get(r.id, 0)) for r in runs]


async def check_steps(session: AsyncSession, tenant_id: uuid.UUID,
                      records: list[AuditRecord]) -> dict[str, Any]:
    """Recompute each record's hash, check its details against their fingerprint and
    check it points at the record before it."""
    wanted = [r.seq - 1 for r in records if r.seq > 1]
    before = dict((await session.execute(
        select(AuditRecord.seq, AuditRecord.hash)
        .where(AuditRecord.tenant_id == tenant_id, AuditRecord.seq.in_(wanted)))).all())
    before[0] = GENESIS
    if records and min(r.seq for r in records) > 1:
        # After retention trimmed the oldest records, a seal stands in for them.
        from app.models import AuditSeal

        for seq, h in (await session.execute(
                select(AuditSeal.seq, AuditSeal.hash)
                .where(AuditSeal.tenant_id == tenant_id, AuditSeal.seq.in_(wanted)))).all():
            before.setdefault(seq, h)
    evs = await evidence_for(session, tenant_id, [r.seq for r in records if r.evidence_digest])
    problems = []
    for r in records:
        if compute_hash(r) != r.hash:
            problems.append({"seq": r.seq, "problem": f"record {r.seq} was changed after it "
                                                      "was written"})
        elif problem := evidence_problem(r, evs.get(r.seq)):
            problems.append({"seq": r.seq, "problem": problem})
        elif r.seq - 1 not in before:
            problems.append({"seq": r.seq, "problem": f"record {r.seq - 1}, just before "
                                                      f"record {r.seq}, is missing"})
        elif r.prev_hash != before[r.seq - 1]:
            problems.append({"seq": r.seq, "problem": f"record {r.seq} does not point at "
                                                      f"record {r.seq - 1}"})
    return {"ok": not problems, "checked": len(records), "problems": problems}


def _decision_row(d: Decision) -> dict[str, Any]:
    tool = TOOLS.get(d.tool_key)
    return {"id": str(d.id), "kind": d.kind, "status": d.status, "title": d.title,
            "tool_title": tool.title if tool else d.tool_key, "reasons": d.reasons,
            "recommendation": d.recommendation, "approver_role_id":
            str(d.approver_role_id) if d.approver_role_id else None,
            "created_at": d.created_at, "decided_by": d.decided_by,
            "decided_at": d.decided_at, "note": d.note, "result": d.result}


def _llm_row(c: LlmCall) -> dict[str, Any]:
    return {"id": str(c.id), "created_at": c.created_at, "task": c.task, "tier": c.tier,
            "provider": c.provider, "model": c.model, "input_tokens": c.input_tokens,
            "output_tokens": c.output_tokens, "cost_usd": str(c.cost_usd),
            "latency_ms": c.latency_ms, "status": c.status, "error": c.error}


async def replay(session: AsyncSession, run_id: uuid.UUID, *,
                 reveal: bool = False) -> dict[str, Any]:
    run = await session.get(AgentRun, run_id)
    if run is None:
        raise NotFound("Agent run not found")
    records = list(await session.scalars(
        select(AuditRecord).where(AuditRecord.run_id == run_id).order_by(AuditRecord.seq)))
    decisions = list(await session.scalars(
        select(Decision).where(Decision.run_id == run_id).order_by(Decision.created_at)))
    calls = list(await session.scalars(
        select(LlmCall).where(LlmCall.trace_id == str(run_id)).order_by(LlmCall.created_at)))
    tally, waiting = await _tallies(session, [run.id])

    subjects: dict[tuple[str, uuid.UUID], str | None] = {}
    for r in records:
        if r.record_type and r.record_id and (r.record_type, r.record_id) not in subjects:
            subjects[(r.record_type, r.record_id)] = await record_name(
                session, r.record_type, r.record_id)
    steps = await audit_rows(session, records)
    cards = [_decision_row(d) for d in decisions]
    subject_rows = [{"record_type": t, "record_id": str(i), "name": n}
                    for (t, i), n in subjects.items()]
    people = [s["name"] for s in subject_rows if s["record_type"] == "contact" and s["name"]]
    personal = shown(steps, extra=cards, names=people, reveal=reveal)
    if not reveal:
        for s in subject_rows:
            if s["record_type"] == "contact" and s["name"]:
                s["name"] = "[name hidden]"
    return {
        "run": _run_row(run, tally.get(run.id, {}), waiting.get(run.id, 0)),
        "steps": steps,
        "decisions": cards,
        "model_calls": [_llm_row(c) for c in calls],
        "records": subject_rows,
        "integrity": await check_steps(session, run.tenant_id, records),
        "personal_data": personal,
    }
