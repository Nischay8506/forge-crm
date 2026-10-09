"""Agents admin (F12.2): what each agent can do, its autonomy level and overrides,
kill-switches, policy rules, and a 'try it' call for the test-only Sandbox Agent.

The admin screen answers "what can agent X do" from GET /agents/{key} alone.
"""

import uuid
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import controls, policy, runtime, scorecard, versions
from app.agents.registry import AGENTS, FACTS, LEVELS, OPS, TOOLS, AgentDef, load_definitions
from app.audit import chain, replay
from app.core.deps import CurrentUser, require_permission
from app.core.errors import NotFound, ValidationFailed
from app.db import get_db
from app.models import (
    Account,
    AgentVersion,
    AuditRecord,
    AutonomyOverride,
    KillSwitch,
    PolicyRule,
    Role,
)
from app.models.golden import ACCOUNT_TIERS

router = APIRouter(tags=["agents"])
load_definitions()


def _actor(u: CurrentUser) -> str:
    return f"user:{u.user.id}"


def _level(level: int) -> dict[str, Any]:
    code, _, text = LEVELS[level].partition(" ")
    return {"level": level, "code": code, "label": text}


async def _account_names(db: AsyncSession, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return dict((await db.execute(select(Account.id, Account.name)
                                  .where(Account.id.in_(ids)))).all())


def _tool(key: str) -> dict[str, Any]:
    t = TOOLS[key]
    return {"key": t.key, "title": t.title, "description": t.description,
            "side_effect": t.side_effect, "version": t.version,
            "facts": [{"key": f, "label": FACTS[f].label} for f in t.facts]}


def _override(o: AutonomyOverride, names: dict[uuid.UUID, str]) -> dict[str, Any]:
    target = (f"{o.account_tier} accounts" if o.scope == "account_tier"
              else names.get(o.account_id, "an account"))
    return {"id": str(o.id), "scope": o.scope, "account_tier": o.account_tier,
            "account_id": str(o.account_id) if o.account_id else None, "target": target,
            "note": o.note, **_level(o.autonomy_level), "created_by": o.created_by}


def _switch(ks: KillSwitch, names: dict[uuid.UUID, str]) -> dict[str, Any]:
    if ks.scope == "tenant":
        target = "All agents"
    elif ks.scope == "agent":
        target = AGENTS[ks.agent_key].name if ks.agent_key in AGENTS else ks.agent_key
    else:
        target = f"All agents on {names.get(ks.account_id, 'an account')}"
    return {"id": str(ks.id), "scope": ks.scope, "agent_key": ks.agent_key,
            "account_id": str(ks.account_id) if ks.account_id else None, "target": target,
            "reason": ks.reason, "created_by": ks.created_by, "created_at": ks.created_at,
            "released_at": ks.released_at, "released_by": ks.released_by}


def _applies(rule: PolicyRule, agent: AgentDef) -> bool:
    return ((rule.agent_key in (None, agent.key))
            and (rule.tool_key is None or rule.tool_key in agent.tools))


async def _rule(db: AsyncSession, r: PolicyRule) -> dict[str, Any]:
    return {"id": str(r.id), "agent_key": r.agent_key, "tool_key": r.tool_key,
            "conditions": r.conditions, "outcome": r.outcome,
            "approver_role_id": str(r.approver_role_id) if r.approver_role_id else None,
            "note": r.note, "enabled": r.enabled, "sentence": await policy.sentence(db, r),
            "created_by": r.created_by, "updated_by": r.updated_by,
            "updated_at": r.updated_at}


async def _agent(db: AsyncSession, agent: AgentDef, *, detail: bool) -> dict[str, Any]:
    level = await controls.tenant_level(db, agent)
    overrides = list(await db.scalars(select(AutonomyOverride)
                                      .where(AutonomyOverride.agent_key == agent.key)
                                      .order_by(AutonomyOverride.created_at)))
    switches = [ks for ks in await controls.active_switches(db)
                if ks.scope == "tenant" or ks.agent_key == agent.key]
    names = await _account_names(db, {o.account_id for o in overrides})
    out: dict[str, Any] = {
        "key": agent.key, "name": agent.name, "purpose": agent.purpose,
        "test_only": agent.test_only, "model_tier": agent.model_tier,
        "triggers": list(agent.triggers), "actor": agent.actor,
        "default_level": agent.default_level, "max_level": agent.max_level,
        **_level(level), "tools": [_tool(t) for t in agent.tools],
        "overrides": [_override(o, names) for o in overrides],
        "stopped": bool(switches), "stops": [_switch(ks, {}) for ks in switches],
    }
    if not detail:
        return out
    rules = [r for r in await db.scalars(select(PolicyRule).order_by(PolicyRule.created_at))
             if _applies(r, agent)]
    out["rules"] = [await _rule(db, r) for r in rules]
    counts = dict((await db.execute(
        select(AuditRecord.kind, func.count()).where(
            AuditRecord.agent_key == agent.key,
            AuditRecord.kind.in_(("action.executed", "action.blocked", "action.failed",
                                  "decision.requested")))
        .group_by(AuditRecord.kind))).all())
    out["activity"] = {"done": counts.get("action.executed", 0),
                       "blocked": counts.get("action.blocked", 0),
                       "failed": counts.get("action.failed", 0),
                       "asked": counts.get("decision.requested", 0)}
    recent = await replay.audit_rows(db, list(await db.scalars(
        select(AuditRecord).where(AuditRecord.agent_key == agent.key)
        .order_by(AuditRecord.seq.desc()).limit(15))))
    replay.shown(recent)
    out["recent"] = [{k: a[k] for k in ("seq", "kind", "actor", "created_at", "data")}
                     for a in recent]
    fp = versions.fingerprint(versions.definition(agent))
    out["version"] = await db.scalar(select(AgentVersion.number).where(
        AgentVersion.agent_key == agent.key, AgentVersion.fingerprint == fp))
    return out


@router.get("/agents")
async def list_agents(
    _: CurrentUser = Depends(require_permission("agents.read")),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    return [await _agent(db, a, detail=False)
            for a in sorted(AGENTS.values(), key=lambda a: (a.test_only, a.name))]


@router.get("/agents/{key}")
async def get_agent(
    key: str, _: CurrentUser = Depends(require_permission("agents.read")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await _agent(db, controls.agent_or_404(key), detail=True)


@router.get("/agents/{key}/scorecard")
async def get_scorecard(
    key: str, months: int = Query(12, ge=1, le=36),
    _: CurrentUser = Depends(require_permission("agents.read")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """The agent's track record by month and version (R8). It informs; it never changes
    the agent's level."""
    agent = controls.agent_or_404(key)
    return await scorecard.scorecard(db, agent.key, months=months)


class LevelIn(BaseModel):
    level: int = Field(ge=0, le=3)


@router.put("/agents/{key}/level")
async def set_level(
    key: str, body: LevelIn, current: CurrentUser = Depends(require_permission("agents.manage")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    agent = controls.agent_or_404(key)
    await controls.set_level(db, current.tenant_id, agent, body.level, _actor(current))
    await db.flush()
    return await _agent(db, agent, detail=True)


class OverrideIn(BaseModel):
    scope: Literal["account_tier", "account"]
    account_tier: str | None = None
    account_id: uuid.UUID | None = None
    autonomy_level: int = Field(ge=0, le=3)
    note: str | None = Field(default=None, max_length=300)


@router.post("/agents/{key}/overrides", status_code=201)
async def add_override(
    key: str, body: OverrideIn,
    current: CurrentUser = Depends(require_permission("agents.manage")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    o = await controls.add_override(db, current.tenant_id, controls.agent_or_404(key),
                                    body.model_dump(), _actor(current))
    return _override(o, await _account_names(db, {o.account_id}))


@router.delete("/agents/overrides/{override_id}", status_code=204)
async def remove_override(
    override_id: uuid.UUID, current: CurrentUser = Depends(require_permission("agents.manage")),
    db: AsyncSession = Depends(get_db),
) -> None:
    await controls.remove_override(db, current.tenant_id, override_id, _actor(current))


# Sandbox ---------------------------------------------------------------------------

class TryIn(BaseModel):
    account_id: uuid.UUID | None = None
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    note: str | None = Field(default=None, max_length=300)


class DraftIn(BaseModel):
    account_id: uuid.UUID
    contact_id: uuid.UUID | None = None
    channel: Literal["email", "whatsapp"] = "email"
    subject: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=4000)


@router.post("/agents/sandbox/draft-message")
async def draft_test_message(
    body: DraftIn, current: CurrentUser = Depends(require_permission("agents.manage")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Ask the Sandbox Agent to draft a test message (R7). It is never sent."""
    ctx = await runtime.start_run(db, current.tenant_id, "sandbox", "try_message",
                                  _actor(current))
    out = await runtime.call_tool(db, ctx, "sandbox.draft_message", body.model_dump(),
                                  recommendation="Test message asked for by a person")
    await runtime.finish_run(db, ctx, summary={"status": out.status})
    return {"status": out.status, "summary": out.summary, "reasons": out.reasons,
            "decision_id": str(out.decision_id) if out.decision_id else None,
            "error": out.error, "message_id": out.result.get("message_id"),
            "run_id": str(ctx.run_id)}


@router.post("/agents/sandbox/try")
async def try_sandbox(
    body: TryIn, current: CurrentUser = Depends(require_permission("agents.manage")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Ask the test-only Sandbox Agent to record a pretend payment, to see what the
    gate does with it (done, waiting for a person, or blocked, and why)."""
    ctx = await runtime.start_run(db, current.tenant_id, "sandbox", "try_it", _actor(current))
    out = await runtime.call_tool(db, ctx, "sandbox.record_payment", body.model_dump(),
                                  recommendation="Test payment asked for on the Agents screen")
    await runtime.finish_run(db, ctx, summary={"status": out.status})
    return {"status": out.status, "summary": out.summary, "reasons": out.reasons,
            "decision_id": str(out.decision_id) if out.decision_id else None,
            "error": out.error, "result": out.result}


# Kill-switches -----------------------------------------------------------------------

class StopIn(BaseModel):
    scope: Literal["tenant", "agent", "account"]
    agent_key: str | None = None
    account_id: uuid.UUID | None = None
    reason: str = Field(min_length=1, max_length=500)


@router.get("/kill-switches")
async def list_switches(
    include_released: bool = False, limit: int = Query(50, le=200),
    _: CurrentUser = Depends(require_permission("agents.read")),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = select(KillSwitch)
    if not include_released:
        stmt = stmt.where(KillSwitch.released_at.is_(None))
    rows = list(await db.scalars(stmt.order_by(KillSwitch.created_at.desc()).limit(limit)))
    names = await _account_names(db, {r.account_id for r in rows})
    return [_switch(r, names) for r in rows]


@router.post("/kill-switches", status_code=201)
async def stop_agents(
    body: StopIn, current: CurrentUser = Depends(require_permission("agents.manage")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ks = await controls.stop(db, current.tenant_id, body.scope, body.reason, _actor(current),
                             agent_key=body.agent_key, account_id=body.account_id)
    return _switch(ks, await _account_names(db, {ks.account_id}))


@router.post("/kill-switches/{switch_id}/release")
async def release_switch(
    switch_id: uuid.UUID, current: CurrentUser = Depends(require_permission("agents.manage")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ks = await controls.release(db, current.tenant_id, switch_id, _actor(current))
    return _switch(ks, await _account_names(db, {ks.account_id}))


# Policy rules ------------------------------------------------------------------------

class Condition(BaseModel):
    fact: str
    op: str
    value: Any


class RuleIn(BaseModel):
    agent_key: str | None = None
    tool_key: str | None = None
    conditions: list[Condition] = []
    outcome: Literal["allow", "ask_person", "block"]
    approver_role_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=300)
    enabled: bool = True


async def _checked(db: AsyncSession, body: RuleIn) -> dict[str, Any]:
    data = body.model_dump(mode="json")
    problems = policy.validate(data)
    if body.approver_role_id and await db.get(Role, body.approver_role_id) is None:
        problems.append("That role does not exist")
    if problems:
        raise ValidationFailed(problems)
    data["approver_role_id"] = body.approver_role_id
    return data


def _snapshot(r: PolicyRule) -> dict[str, Any]:
    return {"agent_key": r.agent_key, "tool_key": r.tool_key, "conditions": r.conditions,
            "outcome": r.outcome, "approver_role_id": r.approver_role_id, "note": r.note,
            "enabled": r.enabled}


@router.get("/policies/catalog")
async def policy_catalog(
    _: CurrentUser = Depends(require_permission("agents.read")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Everything the rule builder offers: agents and their actions, the facts each
    action provides, comparison words, outcomes, tiers and roles."""
    roles = await db.scalars(select(Role).order_by(Role.name))
    return {
        "agents": [{"key": a.key, "name": a.name, "test_only": a.test_only,
                    "tools": list(a.tools)} for a in AGENTS.values()],
        "tools": [_tool(t) for t in TOOLS],
        "facts": [{"key": f.key, "label": f.label, "type": f.type, "choices": list(f.choices),
                   "ops": list(policy.NUMERIC_OPS if f.type in ("number", "money")
                               else policy.CHOICE_OPS)} for f in FACTS.values()],
        "ops": OPS, "outcomes": policy.OUTCOMES, "account_tiers": list(ACCOUNT_TIERS),
        "levels": [_level(level) for level in LEVELS],
        "roles": [{"id": str(r.id), "name": r.name} for r in roles],
    }


@router.get("/policies")
async def list_policies(
    agent_key: str | None = None, _: CurrentUser = Depends(require_permission("agents.read")),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    rules = await db.scalars(select(PolicyRule).order_by(PolicyRule.created_at))
    if agent_key:
        agent = controls.agent_or_404(agent_key)
        rules = [r for r in rules if _applies(r, agent)]
    return [await _rule(db, r) for r in rules]


@router.post("/policies/preview")
async def preview_policy(
    body: RuleIn, _: CurrentUser = Depends(require_permission("agents.read")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """The sentence a draft rule would read as, and anything wrong with it."""
    data = body.model_dump(mode="json")
    problems = policy.validate(data)
    sentence = None
    if not problems:
        draft = PolicyRule(**{**data, "approver_role_id": body.approver_role_id})
        sentence = await policy.sentence(db, draft)
    return {"sentence": sentence, "problems": problems}


@router.post("/policies", status_code=201)
async def create_policy(
    body: RuleIn, current: CurrentUser = Depends(require_permission("agents.manage")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    data = await _checked(db, body)
    r = PolicyRule(tenant_id=current.tenant_id, created_by=_actor(current),
                   updated_by=_actor(current), **data)
    db.add(r)
    await db.flush()
    await chain.append(db, current.tenant_id, "policy.created", actor=_actor(current),
                       agent_key=r.agent_key, data={"rule_id": r.id, "rule": _snapshot(r),
                                                    "sentence": await policy.sentence(db, r)})
    await db.refresh(r)
    return await _rule(db, r)


@router.put("/policies/{rule_id}")
async def update_policy(
    rule_id: uuid.UUID, body: RuleIn,
    current: CurrentUser = Depends(require_permission("agents.manage")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    r = await db.get(PolicyRule, rule_id)
    if r is None:
        raise NotFound("Rule not found")
    data = await _checked(db, body)
    before = _snapshot(r)
    for k, v in data.items():
        setattr(r, k, v)
    r.updated_by = _actor(current)
    await db.flush()
    await chain.append(db, current.tenant_id, "policy.updated", actor=_actor(current),
                       agent_key=r.agent_key, data={"rule_id": r.id, "before": before,
                                                    "after": _snapshot(r),
                                                    "sentence": await policy.sentence(db, r)})
    await db.refresh(r)
    return await _rule(db, r)


@router.delete("/policies/{rule_id}", status_code=204)
async def delete_policy(
    rule_id: uuid.UUID, current: CurrentUser = Depends(require_permission("agents.manage")),
    db: AsyncSession = Depends(get_db),
) -> None:
    r = await db.get(PolicyRule, rule_id)
    if r is None:
        raise NotFound("Rule not found")
    await chain.append(db, current.tenant_id, "policy.deleted", actor=_actor(current),
                       agent_key=r.agent_key, data={"rule_id": r.id, "rule": _snapshot(r),
                                                    "sentence": await policy.sentence(db, r)})
    await db.delete(r)
