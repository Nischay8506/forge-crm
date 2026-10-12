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
from app.approvals.calendar import calendar
from app.approvals.chain import close_steps, open_steps, start_step
from app.approvals.notify import notify
from app.audit import chain
from app.core.errors import AppError, NotFound
from app.models import AgentRun, Decision, DecisionStep

log = logging.getLogger(__name__)


@dataclass
class ToolOutcome:
    status: str  # done | waiting | blocked | failed
    summary: str
    result: dict[str, Any] = field(default_factory=dict)
    decision_id: uuid.UUID | None = None
    reasons: list[str] = field(default_factory=list)
    error: str | None = None
    # The audit records this call wrote: the proposal, and how it ended (executed,
    # failed, blocked or a person asked), for the decision's references (safeguard 6).
    audit: dict[str, int] = field(default_factory=dict)


RECOMMENDED_OPTIONS = ("approve", "reject", "counter")


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
                    recommend: str | None = None, requested_by: str | None = None,
                    counter_of: uuid.UUID | None = None, dedupe_key: str | None = None,
                    approved: bool = False) -> ToolOutcome:
    """Run one tool call through the gate. When a person must decide, the card gets
    `recommend` (the option the agent recommends: approve, reject or counter, H6) with
    `recommendation` as its reason, and `requested_by` (the person who asked for the
    action, who may never decide it)."""
    if recommend is not None and recommend not in RECOMMENDED_OPTIONS:
        raise ValueError(f"Unknown recommended option {recommend}")
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
    proposed = await chain.append(session, ctx.tenant_id, "action.proposed", **kw, data={
        "tool": tool.key, "version": tool.version, "agent_version": ctx.agent_version,
        "summary": summary, "args": stored_args, "facts": facts, "inputs": inputs,
        "outcome": gate.outcome, "level": gate.level, "checks": gate.checks,
        "rule_ids": gate.rule_ids, "approved_by": ctx.approved_by})

    if gate.outcome == "block":
        blocked = await chain.append(session, ctx.tenant_id, "action.blocked", **kw,
                                     data={"tool": tool.key, "reasons": gate.reasons})
        if not gate.hard_block and not approved:
            await _notice(session, ctx, tool.key, f"Blocked: {summary}", gate, stored_args,
                          facts, touches, dedupe_key)
        return ToolOutcome("blocked", summary, reasons=gate.reasons,
                           audit={"proposed": proposed.seq, "outcome": blocked.seq})

    if gate.outcome == "ask_person":
        if dedupe_key:
            existing = await session.scalar(select(Decision).where(
                Decision.dedupe_key == dedupe_key, Decision.status == "pending"))
            if existing is not None:
                return ToolOutcome("waiting", summary, decision_id=existing.id,
                                   reasons=gate.reasons)
        asker = requested_by or (stored_args.get("requested_by")
                                 if isinstance(stored_args.get("requested_by"), str) else None)
        d = Decision(tenant_id=ctx.tenant_id, kind="approval", status="pending",
                     agent_key=ctx.agent.key, tool_key=tool.key, run_id=ctx.run_id,
                     title=summary, recommendation=recommendation,
                     recommended_option=recommend, reasons=gate.reasons,
                     args=stored_args, facts=facts, checks=gate.checks,
                     approver_role_id=gate.approver_role_id, dedupe_key=dedupe_key,
                     requested_by=asker, counter_of=counter_of, **_subject(touches))
        cal, cfg = await calendar(session, ctx.tenant_id)
        open_steps(d, gate.approval_steps, gate.sla_hours or cfg["default_sla_hours"], cal,
                   cfg["reminder_pct"])
        session.add(d)
        await session.flush()
        asked = await chain.append(session, ctx.tenant_id, "decision.requested", **kw, data={
            "decision_id": d.id, "tool": tool.key, "reasons": gate.reasons,
            "approver_role_id": gate.approver_role_id,
            "approval_steps": [str(r) for r in gate.approval_steps],
            "sla_hours": str(d.sla_hours), "recommended": recommend,
            "requested_by": asker, "counter_of": counter_of})
        await notify(session, d, "new")
        return ToolOutcome("waiting", summary, decision_id=d.id, reasons=gate.reasons,
                           audit={"proposed": proposed.seq, "outcome": asked.seq})

    try:
        async with session.begin_nested():
            result = await tool.run(session, ctx, parsed) or {}
    except Exception as exc:  # noqa: BLE001 - the failure is recorded, the run carries on
        msg = exc.message if isinstance(exc, AppError) else f"{type(exc).__name__}: {exc}"
        if not isinstance(exc, AppError):
            log.exception("tool %s failed", tool.key)
        failed = await chain.append(session, ctx.tenant_id, "action.failed", **kw,
                                    data={"tool": tool.key, "error": msg[:1000]})
        return ToolOutcome("failed", summary, error=msg,
                           audit={"proposed": proposed.seq, "outcome": failed.seq})
    executed = await chain.append(session, ctx.tenant_id, "action.executed", **kw,
                                  data={"tool": tool.key, "result": result,
                                        "result_ids": _result_ids(result)})
    if gate.notify:
        await _notice(session, ctx, tool.key, summary, gate, stored_args, facts, touches, None,
                      reasons=[f"Done by {ctx.agent.name} at {LEVELS[1].split(':')[0]}: "
                               "it acts, then tells a person"], result=result)
    return ToolOutcome("done", summary, result=result,
                       audit={"proposed": proposed.seq, "outcome": executed.seq})


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

def _decision_kw(d: Decision, actor: str) -> dict[str, Any]:
    tool = TOOLS.get(d.tool_key)
    return {"actor": actor, "agent_key": d.agent_key, "run_id": d.run_id,
            "record_type": d.record_type, "record_id": d.record_id,
            "evidence_class": tool.evidence_class if tool else None,
            "account_id": d.record_id if d.record_type == "account" else None,
            "contact_id": d.record_id if d.record_type == "contact" else None}


async def decide(session: AsyncSession, decision_id: uuid.UUID, *, approve: bool, actor: str,
                 note: str | None = None, decision_ms: int | None = None,
                 on_behalf_of: str | None = None, card: dict[str, Any] | None = None,
                 card_hash: str | None = None) -> Decision:
    """Record one person's decision on the card's current step (the caller has checked
    they may decide it, and that the card is the one they saw). A rejection ends the
    card. An approval starts the next step of the chain, or, after the last one, runs
    the action through the gate again (H1, safeguard 1). The audit record keeps the
    card as shown and its fingerprint (H7, safeguard 6)."""
    d = await session.get(Decision, decision_id, with_for_update=True, populate_existing=True)
    if d is None or d.kind != "approval":
        raise NotFound("Decision not found")
    if d.status != "pending":
        raise AppError(f"This decision is already {d.status}")
    now = datetime.now(UTC)
    note = (note or "").strip() or None
    step = d.current_step
    if step is not None:
        step.decided_by, step.on_behalf_of, step.decided_at = actor, on_behalf_of, now
        step.note, step.decision_ms, step.card_hash = note, decision_ms, card_hash
    kw = _decision_kw(d, actor)
    data = {"decision_id": d.id, "note": note, "decision_ms": decision_ms,
            "on_behalf_of": on_behalf_of, "step": d.step_index + 1, "steps": len(d.steps),
            "role_id": step.role_id if step else None, "card_hash": card_hash, "card": card,
            "recommended": d.recommended_option}
    if not approve:
        if step is not None:
            step.status = "rejected"
        close_steps(d, "cancelled")
        d.status = "rejected"
        d.decided_by, d.decided_at, d.note, d.decision_ms = actor, now, note, decision_ms
        rec = await chain.append(session, d.tenant_id, "decision.rejected", **kw, data=data)
        d.result = {"status": "rejected", "result": {}, "error": None, "reasons": [],
                    "audit": {"decision": rec.seq}}
        return d
    if step is not None:
        step.status = "approved"
    rec = await chain.append(session, d.tenant_id, "decision.approved", **kw, data=data)
    # The next step not yet approved (a counter can approve a later step ahead).
    nxt = next((s for s in d.steps if s.position > d.step_index and s.status != "approved"),
               None)
    if nxt is not None:
        d.step_index = nxt.position
        cal, cfg = await calendar(session, d.tenant_id)
        start_step(nxt, d.sla_hours or cfg["default_sla_hours"], cal, cfg["reminder_pct"], now)
        d.approver_role_id = nxt.role_id
        await notify(session, d, "step")
        return d
    d.decided_by, d.decided_at, d.note, d.decision_ms = actor, now, note, decision_ms
    return await _carry_out(session, d, actor, rec.seq)


async def approve_ahead(session: AsyncSession, d: Decision, step: DecisionStep, *, actor: str,
                        on_behalf_of: str | None, note: str | None,
                        card: dict[str, Any] | None, card_hash: str | None) -> None:
    """Record an approval for a later step before its turn (a counter, H2): the
    earlier steps still decide first, and this step is then passed over."""
    now = datetime.now(UTC)
    step.status, step.decided_by, step.on_behalf_of, step.decided_at = (
        "approved", actor, on_behalf_of, now)
    step.note, step.card_hash = note, card_hash
    await chain.append(session, d.tenant_id, "decision.approved", **_decision_kw(d, actor),
                       data={"decision_id": d.id, "note": note, "on_behalf_of": on_behalf_of,
                             "step": step.position + 1, "steps": len(d.steps),
                             "role_id": step.role_id, "card_hash": card_hash, "card": card,
                             "recommended": d.recommended_option, "ahead": True})


async def _carry_out(session: AsyncSession, d: Decision, actor: str,
                     decision_seq: int) -> Decision:
    """Every step approved: run the action as proposed, through the gate again."""
    run = await session.get(AgentRun, d.run_id) if d.run_id else None
    ctx = AgentContext(tenant_id=d.tenant_id, agent=AGENTS[d.agent_key], run_id=d.run_id,
                       approved_by=actor, agent_version=run.agent_version if run else None)
    outcome = await call_tool(session, ctx, d.tool_key, d.args, approved=True)
    d.status = "approved" if outcome.status == "done" else "failed"
    d.result = {"status": outcome.status, "result": outcome.result, "error": outcome.error,
                "reasons": outcome.reasons, "audit": {"decision": decision_seq, **outcome.audit}}
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
    if rows:
        await session.execute(
            update(DecisionStep).where(DecisionStep.decision_id.in_([r[0] for r in rows]),
                                       DecisionStep.status.in_(("waiting", "pending")))
            .values(status="superseded").execution_options(synchronize_session="fetch"))
    return len(rows)


# Model calls (R2) -----------------------------------------------------------------

def _for_trail(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The prompt as kept in the trail: pictures sent to a vision model are named by
    size, not copied (the file itself is kept in the file store)."""
    out = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            content = [p if p.get("type") != "image_url" else
                       {"type": "image", "bytes": len(str(p.get("image_url", {}).get("url", "")))
                        * 3 // 4}
                       for p in content]
        out.append({**m, "content": content})
    return out


async def ask_model(session: AsyncSession, ctx: AgentContext, *, task: str,
                    messages: list[dict[str, Any]], record_type: str | None = None,
                    record_id: Any = None, tier: str | None = None, **kwargs: Any) -> Any:
    """Every model call an agent makes goes through here: the gateway picks the model
    (budget, fallback, cost log), and the trail keeps the full prompt, anything the
    agent was given for it (ctx.saw) and the answer, under the run's id. `tier`
    replaces the agent's own tier for one call (e.g. "vision" for a scanned drawing)."""
    from app.llm import gateway

    if not ctx.agent.model_tier:
        raise AppError(f"{ctx.agent.name} has no model tier, so it cannot call a model")
    tier = tier or ctx.agent.model_tier
    kw = {"actor": ctx.actor, "agent_key": ctx.agent.key, "run_id": ctx.run_id,
          "record_type": record_type, "record_id": record_id}
    given = ctx.take_seen()
    base = {"task": task, "tier": tier, "agent_version": ctx.agent_version,
            "messages": _for_trail(messages), "inputs": given,
            "settings": {k: v for k, v in kwargs.items() if isinstance(v, str | int | float)}}
    try:
        res = await gateway.complete(session, ctx.tenant_id, task=task, tier=tier,
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
