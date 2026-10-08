"""Agent runtime (decision T5: our own small runtime on Celery).

Agents run inside Celery tasks and outbox handlers. Every tool call goes through
call_tool(): facts and touched records come from the tool itself, the policy
gate decides, and each step lands in the audit chain under the run's id:

  action.proposed -> action.executed | action.failed | action.blocked | decision.requested

A call that needs a person becomes a Decision (decision T2). When the person
approves, decide() runs the same call again with the approval, so the agent's
work continues exactly as proposed.
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
    run = AgentRun(tenant_id=tenant_id, agent_key=agent_key, trigger=trigger,
                   trigger_ref=trigger_ref, status="running")
    session.add(run)
    await session.flush()
    return AgentContext(tenant_id=tenant_id, agent=agent, run_id=run.id)


async def finish_run(session: AsyncSession, ctx: AgentContext, status: str = "succeeded",
                     summary: dict[str, Any] | None = None) -> None:
    run = await session.get(AgentRun, ctx.run_id)
    if run is not None:
        run.status, run.finished_at, run.summary = status, datetime.now(UTC), summary


def _audit_kw(ctx: AgentContext, touches: Touches) -> dict[str, Any]:
    rec = touches.records[0] if touches.records else (
        ("account", touches.accounts[0]) if touches.accounts else (None, None))
    return {"actor": ctx.approved_by or ctx.actor, "agent_key": ctx.agent.key,
            "run_id": ctx.run_id, "record_type": rec[0], "record_id": rec[1]}


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
    kw = _audit_kw(ctx, touches)
    stored_args = parsed.model_dump(mode="json")
    await chain.append(session, ctx.tenant_id, "action.proposed", **kw, data={
        "tool": tool.key, "version": tool.version, "summary": summary, "args": stored_args,
        "facts": facts, "outcome": gate.outcome, "level": gate.level, "checks": gate.checks,
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
                       data={"tool": tool.key, "result": result})
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
    kw = {"actor": actor, "agent_key": d.agent_key, "run_id": d.run_id,
          "record_type": d.record_type, "record_id": d.record_id}
    if not approve:
        d.status = "rejected"
        await chain.append(session, d.tenant_id, "decision.rejected", **kw,
                           data={"decision_id": d.id, "note": d.note})
        return d
    await chain.append(session, d.tenant_id, "decision.approved", **kw,
                       data={"decision_id": d.id, "note": d.note})
    ctx = AgentContext(tenant_id=d.tenant_id, agent=AGENTS[d.agent_key], run_id=d.run_id,
                       approved_by=actor)
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
