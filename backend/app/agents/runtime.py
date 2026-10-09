"""Agent runtime (decision T5: our own small runtime on Celery).

Agents run inside Celery tasks and outbox handlers. Every tool call goes through
call_tool(): facts and touched records come from the tool itself, the policy
gate decides, and each step lands in the audit chain under the run's id:

  action.proposed -> action.executed | action.failed | action.blocked | decision.requested

A call that needs a person becomes a Decision (decision T2). When the person
approves, decide() runs the same call again with the approval, so the agent's
work continues exactly as proposed.

The trail keeps what the agent was given (R2): action.proposed carries the
records the tool touches as they were at that moment, plus anything noted with
ctx.saw(); ask_model() records the full prompt and answer of every model call.
Every run and proposal carries the agent's version number (R8).
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.gate import GateResult, evaluate
from app.agents.registry import AGENTS, LEVELS, TOOLS, AgentContext, Touches
from app.agents.versions import current_version
from app.audit import chain
from app.core.errors import AppError, NotFound
from app.models import AgentRun, Decision

log = logging.getLogger(__name__)


@dataclass
class ToolOutcome:
    status: str  # done | waiting | blocked | failed
    summary: str
    result: dict[str, Any] = field(default_factory=dict)
    decision_id: uuid.UUID | None = None
    reasons: list[str] = field(default_factory=list)
    error: str | None = None


async def start_run(session: AsyncSession, tenant_id: uuid.UUID, agent_key: str, trigger: str,
                    trigger_ref: str | None = None) -> AgentContext:
    agent = AGENTS[agent_key]
    version = await current_version(session, agent)
    run = AgentRun(tenant_id=tenant_id, agent_key=agent_key, trigger=trigger,
                   trigger_ref=trigger_ref, status="running", agent_version=version)
    session.add(run)
    await session.flush()
    return AgentContext(tenant_id=tenant_id, agent=agent, run_id=run.id, agent_version=version)


async def finish_run(session: AsyncSession, ctx: AgentContext, status: str = "succeeded",
                     summary: dict[str, Any] | None = None) -> None:
    run = await session.get(AgentRun, ctx.run_id)
    if run is not None:
        run.status, run.finished_at, run.summary = status, datetime.now(UTC), summary


def _audit_kw(ctx: AgentContext, touches: Touches, evidence_class: str) -> dict[str, Any]:
    rec = touches.records[0] if touches.records else (
        ("account", touches.accounts[0]) if touches.accounts else (None, None))
    contact = next((i for t, i in touches.records if t == "contact"), None)
    return {"actor": ctx.approved_by or ctx.actor, "agent_key": ctx.agent.key,
            "run_id": ctx.run_id, "record_type": rec[0], "record_id": rec[1],
            "evidence_class": evidence_class,
            "account_id": touches.accounts[0] if touches.accounts else None,
            "contact_id": contact}


async def _inputs(session: AsyncSession, touches: Touches) -> list[dict[str, Any]]:
    """The touched records as the agent's tool saw them (R2)."""
    from app.steward.records import MODELS, snapshot

    out, done = [], set()
    now = datetime.now(UTC).isoformat()
    for record_type, record_id in (*touches.records,
                                   *(("account", a) for a in touches.accounts)):
        if (record_type, record_id) in done or record_type not in MODELS:
            continue
        done.add((record_type, record_id))
        rec = await session.get(MODELS[record_type], record_id)
        if rec is not None:
            out.append({"source": "record", "record_type": record_type,
                        "record_id": str(record_id), "read_at": now,
                        "content": snapshot(rec, record_type)})
    return out


def _result_ids(result: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in result.items()
            if (k == "id" or k.endswith("_id")) and isinstance(v, str | int)}


async def call_tool(session: AsyncSession, ctx: AgentContext, tool_key: str,
                    args: dict[str, Any] | BaseModel, *, recommendation: str | None = None,
                    dedupe_key: str | None = None, approved: bool = False) -> ToolOutcome:
    tool = TOOLS[tool_key]
    try:
        parsed = args if isinstance(args, tool.args) else tool.args.model_validate(
            args.model_dump() if isinstance(args, BaseModel) else args)
    except ValidationError as exc:
        raise AppError(f"Invalid arguments for {tool.key}: {exc.errors()[0]['msg']}") from exc
    facts = await tool.compute_facts(session, parsed) if tool.compute_facts else {}
    touches = await tool.touches(session, parsed) if tool.touches else Touches()
    summary = (await tool.describe(session, parsed, facts)) if tool.describe else tool.title
    gate = await evaluate(session, ctx, tool, facts, touches, approved=approved)
    kw = _audit_kw(ctx, touches, tool.evidence_class)
    stored_args = parsed.model_dump(mode="json")
    inputs = await _inputs(session, touches) + ctx.take_seen()
    await chain.append(session, ctx.tenant_id, "action.proposed", **kw, data={
        "tool": tool.key, "version": tool.version, "agent_version": ctx.agent_version,
        "summary": summary, "args": stored_args, "facts": facts, "inputs": inputs,
        "outcome": gate.outcome, "level": gate.level, "checks": gate.checks,
        "rule_ids": gate.rule_ids, "approved_by": ctx.approved_by})

    if gate.outcome == "block":
        await chain.append(session, ctx.tenant_id, "action.blocked", **kw,
                           data={"tool": tool.key, "reasons": gate.reasons})
        if not gate.hard_block and not approved:
            await _notice(session, ctx, tool.key, f"Blocked: {summary}", gate, stored_args,
                          facts, touches, dedupe_key)
        return ToolOutcome("blocked", summary, reasons=gate.reasons)

    if gate.outcome == "ask_person":
        if dedupe_key:
            existing = await session.scalar(select(Decision).where(
                Decision.dedupe_key == dedupe_key, Decision.status == "pending"))
            if existing is not None:
                return ToolOutcome("waiting", summary, decision_id=existing.id,
                                   reasons=gate.reasons)
        d = Decision(tenant_id=ctx.tenant_id, kind="approval", status="pending",
                     agent_key=ctx.agent.key, tool_key=tool.key, run_id=ctx.run_id,
                     title=summary, recommendation=recommendation, reasons=gate.reasons,
                     args=stored_args, facts=facts, checks=gate.checks,
                     approver_role_id=gate.approver_role_id, dedupe_key=dedupe_key,
                     **_subject(touches))
        session.add(d)
        await session.flush()
        await chain.append(session, ctx.tenant_id, "decision.requested", **kw,
                           data={"decision_id": d.id, "tool": tool.key, "reasons": gate.reasons,
                                 "approver_role_id": gate.approver_role_id})
        return ToolOutcome("waiting", summary, decision_id=d.id, reasons=gate.reasons)

    try:
        async with session.begin_nested():
            result = await tool.run(session, ctx, parsed) or {}
    except Exception as exc:  # noqa: BLE001 - the failure is recorded, the run carries on
        msg = exc.message if isinstance(exc, AppError) else f"{type(exc).__name__}: {exc}"
        if not isinstance(exc, AppError):
            log.exception("tool %s failed", tool.key)
        await chain.append(session, ctx.tenant_id, "action.failed", **kw,
                           data={"tool": tool.key, "error": msg[:1000]})
        return ToolOutcome("failed", summary, error=msg)
    await chain.append(session, ctx.tenant_id, "action.executed", **kw,
                       data={"tool": tool.key, "result": result,
                             "result_ids": _result_ids(result)})
    if gate.notify:
        await _notice(session, ctx, tool.key, summary, gate, stored_args, facts, touches, None,
                      reasons=[f"Done by {ctx.agent.name} at {LEVELS[1].split(':')[0]}: "
                               "it acts, then tells a person"], result=result)
    return ToolOutcome("done", summary, result=result)


def _subject(touches: Touches) -> dict[str, Any]:
    if touches.records:
        return {"record_type": touches.records[0][0], "record_id": touches.records[0][1]}
    if touches.accounts:
        return {"record_type": "account", "record_id": touches.accounts[0]}
    return {}


async def _notice(session: AsyncSession, ctx: AgentContext, tool_key: str, title: str,
                  gate: GateResult, args: dict[str, Any], facts: dict[str, Any],
                  touches: Touches, dedupe_key: str | None, reasons: list[str] | None = None,
                  result: dict[str, Any] | None = None) -> None:
    key = f"notice:{dedupe_key}" if dedupe_key else None
    if key and await session.scalar(select(Decision.id).where(
            Decision.dedupe_key == key, Decision.status == "open")):
        return
    session.add(Decision(tenant_id=ctx.tenant_id, kind="notice", status="open",
                         agent_key=ctx.agent.key, tool_key=tool_key, run_id=ctx.run_id,
                         title=title, reasons=reasons or gate.reasons, args=args, facts=facts,
                         checks=gate.checks, dedupe_key=key, result=result, **_subject(touches)))
    await session.flush()


# Deciding --------------------------------------------------------------------------

async def decide(session: AsyncSession, decision_id: uuid.UUID, *, approve: bool, actor: str,
                 note: str | None = None) -> Decision:
    d = await session.get(Decision, decision_id, with_for_update=True, populate_existing=True)
    if d is None or d.kind != "approval":
        raise NotFound("Decision not found")
    if d.status != "pending":
        raise AppError(f"This decision is already {d.status}")
    d.decided_by, d.decided_at, d.note = actor, datetime.now(UTC), (note or "").strip() or None
    tool = TOOLS.get(d.tool_key)
    kw = {"actor": actor, "agent_key": d.agent_key, "run_id": d.run_id,
          "record_type": d.record_type, "record_id": d.record_id,
          "evidence_class": tool.evidence_class if tool else None,
          "account_id": d.record_id if d.record_type == "account" else None,
          "contact_id": d.record_id if d.record_type == "contact" else None}
    if not approve:
        d.status = "rejected"
        await chain.append(session, d.tenant_id, "decision.rejected", **kw,
                           data={"decision_id": d.id, "note": d.note})
        return d
    await chain.append(session, d.tenant_id, "decision.approved", **kw,
                       data={"decision_id": d.id, "note": d.note})
    run = await session.get(AgentRun, d.run_id) if d.run_id else None
    ctx = AgentContext(tenant_id=d.tenant_id, agent=AGENTS[d.agent_key], run_id=d.run_id,
                       approved_by=actor, agent_version=run.agent_version if run else None)
    outcome = await call_tool(session, ctx, d.tool_key, d.args, approved=True)
    d.status = "approved" if outcome.status == "done" else "failed"
    d.result = {"status": outcome.status, "result": outcome.result, "error": outcome.error,
                "reasons": outcome.reasons}
    return d


async def acknowledge(session: AsyncSession, decision_id: uuid.UUID, actor: str) -> Decision:
    d = await session.get(Decision, decision_id)
    if d is None or d.kind != "notice":
        raise NotFound("Notice not found")
    if d.status == "open":
        d.status, d.decided_by, d.decided_at = "acknowledged", actor, datetime.now(UTC)
    return d


async def supersede(session: AsyncSession, dedupe_key: str, actor: str, why: str) -> int:
    """Close pending approvals a person made moot (e.g. merged the pair by hand)."""
    rows = (await session.execute(
        update(Decision).where(Decision.dedupe_key == dedupe_key, Decision.status == "pending")
        .values(status="superseded", decided_by=actor, decided_at=datetime.now(UTC), note=why)
        .returning(Decision.id).execution_options(synchronize_session="fetch"))).all()
    return len(rows)


# Model calls (R2) -----------------------------------------------------------------

async def ask_model(session: AsyncSession, ctx: AgentContext, *, task: str,
                    messages: list[dict[str, str]], record_type: str | None = None,
                    record_id: Any = None, **kwargs: Any) -> Any:
    """Every model call an agent makes goes through here: the gateway picks the model
    (budget, fallback, cost log), and the trail keeps the full prompt, anything the
    agent was given for it (ctx.saw) and the answer, under the run's id."""
    from app.llm import gateway

    if not ctx.agent.model_tier:
        raise AppError(f"{ctx.agent.name} has no model tier, so it cannot call a model")
    kw = {"actor": ctx.actor, "agent_key": ctx.agent.key, "run_id": ctx.run_id,
          "record_type": record_type, "record_id": record_id}
    given = ctx.take_seen()
    base = {"task": task, "tier": ctx.agent.model_tier, "agent_version": ctx.agent_version,
            "messages": messages, "inputs": given,
            "settings": {k: v for k, v in kwargs.items() if isinstance(v, str | int | float)}}
    try:
        res = await gateway.complete(session, ctx.tenant_id, task=task, tier=ctx.agent.model_tier,
                                     messages=messages, trace_id=str(ctx.run_id), **kwargs)
    except Exception as exc:
        await chain.append(session, ctx.tenant_id, "model.failed", **kw,
                           data={**base, "error": f"{type(exc).__name__}: {exc}"[:1000]})
        raise
    await chain.append(session, ctx.tenant_id, "model.called", **kw, data={
        **base, "provider": res.provider, "model": res.model,
        "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
        "answer": res.text})
    return res
