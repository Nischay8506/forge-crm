"""'Waiting for you' (decision T2): actions agents are waiting on, and notices of
what they did at L1 or were blocked from doing. People with the integrations
permission also see integration alerts and tasks here (decision E7). Approving runs the action as it
was proposed, through the gate again (kill-switches, takeovers and block rules
still apply). The full inbox (channels, SLA, delegation) is F12.3, Step 5.
"""

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import runtime
from app.agents.policy import display_value
from app.agents.registry import AGENTS, FACTS, TOOLS, load_definitions
from app.audit.replay import record_name
from app.core.deps import CurrentUser, require_permission
from app.core.errors import Forbidden, NotFound
from app.core.permissions import ALL, has_permission
from app.db import get_db
from app.integrations.alerts import CARD_TITLES, acknowledge_alert
from app.models import Decision, Role, UserRole

router = APIRouter(prefix="/decisions", tags=["decisions"])
load_definitions()


def _actor(u: CurrentUser) -> str:
    return f"user:{u.user.id}"


async def _my_roles(db: AsyncSession, current: CurrentUser) -> set[uuid.UUID]:
    return set(await db.scalars(select(UserRole.role_id)
                                .where(UserRole.user_id == current.user.id)))


def _why_not(d: Decision, current: CurrentUser, roles: set[uuid.UUID]) -> str | None:
    """Why this person cannot decide this card, or None if they can. Anyone allowed to
    decide may, unless a rule named who decides (then only that role, or full access);
    some tools also need a permission and a second person (four eyes, e.g. erasure)."""
    if d.kind != "approval" or d.status != "pending":
        return "This is not waiting for a decision"
    tool = TOOLS.get(d.tool_key)
    if tool and tool.four_eyes and d.args.get("requested_by") == f"user:{current.user.id}":
        return "A second person must decide this: you asked for it"
    if tool and tool.decider_permission and not has_permission(current.permissions,
                                                               tool.decider_permission):
        return "Only people allowed to erase personal data can decide this" \
            if tool.decider_permission == "privacy.erase" else \
            f"This needs the permission {tool.decider_permission}"
    if d.approver_role_id is None or ALL in current.permissions or d.approver_role_id in roles:
        return None
    return "role"


def _can_decide(d: Decision, current: CurrentUser, roles: set[uuid.UUID]) -> bool:
    return _why_not(d, current, roles) is None


INTEGRATION_KINDS = ("alert", "task")


def _sees_integrations(current: CurrentUser) -> bool:
    """Integration alerts and tasks go to people with the integrations permission (E7)."""
    return has_permission(current.permissions, "integrations.read")


async def _out(db: AsyncSession, d: Decision, current: CurrentUser,
               roles: set[uuid.UUID]) -> dict[str, Any]:
    agent, tool = AGENTS.get(d.agent_key), TOOLS.get(d.tool_key)
    role = await db.get(Role, d.approver_role_id) if d.approver_role_id else None
    return {
        "id": str(d.id), "kind": d.kind, "status": d.status, "title": d.title,
        "agent_key": d.agent_key, "agent_name": agent.name if agent else d.agent_key,
        "tool_key": d.tool_key,
        "tool_title": tool.title if tool else CARD_TITLES.get(d.tool_key, d.tool_key),
        "recommendation": d.recommendation, "reasons": d.reasons, "args": d.args,
        "facts": [{"key": k, "label": FACTS[k].label, "value": display_value(k, v)}
                  for k, v in d.facts.items() if k in FACTS],
        "checks": d.checks,
        "approver_role": {"id": str(role.id), "name": role.name} if role else None,
        "record_type": d.record_type, "record_id": str(d.record_id) if d.record_id else None,
        "record_name": await record_name(db, d.record_type, d.record_id),
        "run_id": str(d.run_id) if d.run_id else None,
        "created_at": d.created_at, "decided_by": d.decided_by, "decided_at": d.decided_at,
        "note": d.note, "result": d.result, "can_decide": _can_decide(d, current, roles),
        "why_not": why if (why := _why_not(d, current, roles)) not in (
            None, "role", "This is not waiting for a decision") else None,
    }


@router.get("")
async def list_decisions(
    view: Literal["waiting", "notices", "done"] = "waiting", limit: int = Query(50, le=200),
    offset: int = 0, current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """waiting: approvals still open; notices: unread notices; done: everything
    already decided or read, newest first."""
    stmt = select(Decision)
    if view == "waiting":
        waiting = (Decision.kind == "approval") & (Decision.status == "pending")
        if _sees_integrations(current):
            waiting = waiting | (Decision.kind.in_(INTEGRATION_KINDS)
                                 & (Decision.status == "open"))
        stmt = stmt.where(waiting).order_by(Decision.created_at)
    elif view == "notices":
        stmt = stmt.where(Decision.kind == "notice", Decision.status == "open")
        stmt = stmt.order_by(Decision.created_at.desc())
    else:
        stmt = stmt.where(Decision.status.not_in(("pending", "open")))
        if not _sees_integrations(current):
            stmt = stmt.where(Decision.kind.not_in(INTEGRATION_KINDS))
        stmt = stmt.order_by(Decision.decided_at.desc().nulls_last())
    roles = await _my_roles(db, current)
    rows = await db.scalars(stmt.limit(limit).offset(offset))
    return [await _out(db, d, current, roles) for d in rows]


@router.get("/count")
async def count_decisions(
    current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """For the menu badge: approvals this person can decide, and unread notices."""
    roles = await _my_roles(db, current)
    pending = (await db.execute(
        select(Decision.approver_role_id, func.count())
        .where(Decision.kind == "approval", Decision.status == "pending")
        .group_by(Decision.approver_role_id))).all()
    mine = sum(n for role_id, n in pending
               if role_id is None or ALL in current.permissions or role_id in roles)
    notices = await db.scalar(select(func.count()).select_from(Decision).where(
        Decision.kind == "notice", Decision.status == "open"))
    alerts = tasks = 0
    if _sees_integrations(current):
        rows = dict((await db.execute(
            select(Decision.kind, func.count()).where(Decision.kind.in_(INTEGRATION_KINDS),
                                                      Decision.status == "open")
            .group_by(Decision.kind))).all())
        alerts, tasks = rows.get("alert", 0), rows.get("task", 0)
    return {"waiting": mine + alerts + tasks, "approvals": mine, "alerts": alerts,
            "tasks": tasks, "notices": notices or 0}


@router.get("/{decision_id}")
async def get_decision(
    decision_id: uuid.UUID,
    current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    d = await db.get(Decision, decision_id)
    if d is None or (d.kind in INTEGRATION_KINDS and not _sees_integrations(current)):
        raise NotFound("Decision not found")
    return await _out(db, d, current, await _my_roles(db, current))


class DecideIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


async def _decide(db: AsyncSession, current: CurrentUser, decision_id: uuid.UUID,
                  approve: bool, note: str | None) -> dict[str, Any]:
    d = await db.get(Decision, decision_id)
    if d is None or d.kind != "approval":
        raise NotFound("Decision not found")
    roles = await _my_roles(db, current)
    why = _why_not(d, current, roles) if d.status == "pending" else None
    if why == "role":
        role = await db.get(Role, d.approver_role_id)
        raise Forbidden(f"Only {role.name if role else 'the named role'} can decide this")
    if why:
        raise Forbidden(why)
    d = await runtime.decide(db, decision_id, approve=approve, actor=_actor(current), note=note)
    await db.flush()
    return await _out(db, d, current, roles)


@router.post("/{decision_id}/approve")
async def approve(
    decision_id: uuid.UUID, body: DecideIn | None = None,
    current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await _decide(db, current, decision_id, True, body.note if body else None)


@router.post("/{decision_id}/reject")
async def reject(
    decision_id: uuid.UUID, body: DecideIn | None = None,
    current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await _decide(db, current, decision_id, False, body.note if body else None)


@router.post("/{decision_id}/acknowledge")
async def acknowledge(
    decision_id: uuid.UUID,
    current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    d = await db.get(Decision, decision_id)
    if d is not None and d.kind == "alert":
        if not _sees_integrations(current):
            raise NotFound("Decision not found")
        d = await acknowledge_alert(db, decision_id, _actor(current))
    else:
        d = await runtime.acknowledge(db, decision_id, _actor(current))
    await db.flush()
    return await _out(db, d, current, await _my_roles(db, current))
