"""Approval analytics (decision H9, safeguard 8).

Per card type and per approver: how many decisions, the median wait (working hours
from when a step started to its decision, using the company calendar), the median
decision time (how long the card was on screen before the person decided, measured
by the browser), overrides and missed deadlines.

An override is a decision different from the option the agent recommended. Cards
whose agent recorded no recommendation are left out of the override rate, never
counted as agreeing (safeguard 8). A counter differs from "approve", so it counts as
an override of an "approve" recommendation.
"""

import statistics
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import TOOLS
from app.approvals.calendar import calendar
from app.models import Decision, DecisionStep, Role, User

OPTION = {"approved": "approve", "rejected": "reject", "countered": "counter",
          "failed": "approve"}
DECIDED_STEP = ("approved", "rejected", "countered")


def _median(xs: list[float]) -> float | None:
    return round(statistics.median(xs), 2) if xs else None


def _rate(n: int, of: int) -> float | None:
    return round(n / of, 3) if of else None


def _actor_id(actor: str | None) -> uuid.UUID | None:
    if actor and actor.startswith("user:"):
        try:
            return uuid.UUID(actor[5:])
        except ValueError:
            return None
    return None


async def step_rows(session: AsyncSession, since: datetime) -> list[tuple[Decision, DecisionStep]]:
    rows = (await session.execute(
        select(Decision, DecisionStep).join(DecisionStep, DecisionStep.decision_id == Decision.id)
        .where(DecisionStep.decided_at >= since, DecisionStep.status.in_(DECIDED_STEP)))).all()
    return [(d, s) for d, s in rows]


async def report(session: AsyncSession, tenant_id: uuid.UUID, days: int = 30) -> dict[str, Any]:
    cal, _ = await calendar(session, tenant_id)
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    steps = await step_rows(session, since)
    names = dict((await session.execute(select(User.id, User.full_name))).all())
    roles = dict((await session.execute(select(Role.id, Role.name))).all())

    by_person: dict[str, dict[str, Any]] = {}
    for d, s in steps:
        p = by_person.setdefault(s.decided_by or "?", {
            "waits": [], "times": [], "decided": 0, "overrides": 0, "with_rec": 0,
            "late": 0, "for_others": 0})
        p["decided"] += 1
        if s.started_at:
            p["waits"].append(cal.between(s.started_at, s.decided_at) / 3600)
        if s.decision_ms is not None:
            p["times"].append(s.decision_ms / 1000)
        if d.recommended_option:
            p["with_rec"] += 1
            p["overrides"] += OPTION[s.status] != d.recommended_option
        p["late"] += s.overdue_at is not None
        p["for_others"] += s.on_behalf_of is not None
    approvers = []
    for actor, p in sorted(by_person.items(), key=lambda x: -x[1]["decided"]):
        uid = _actor_id(actor)
        approvers.append({
            "actor": actor, "name": names.get(uid, "A team member") if uid else actor,
            "decided": p["decided"], "median_wait_hours": _median(p["waits"]),
            "median_decision_seconds": _median(p["times"]), "overrides": p["overrides"],
            "with_recommendation": p["with_rec"], "override_rate": _rate(p["overrides"],
                                                                         p["with_rec"]),
            "missed_deadlines": p["late"], "for_others": p["for_others"]})

    decided = list(await session.scalars(select(Decision).where(
        Decision.kind == "approval", Decision.decided_at >= since,
        Decision.status.in_(tuple(OPTION)))))
    pending = list(await session.scalars(select(Decision).where(
        Decision.kind == "approval", Decision.status == "pending")))
    types: dict[str, dict[str, Any]] = {}

    def t(key: str) -> dict[str, Any]:
        return types.setdefault(key, {"by_status": {}, "waits": [], "times": [], "overrides": 0,
                                      "with_rec": 0, "late": 0, "pending": 0,
                                      "pending_late": 0})
    for d in decided:
        x = t(d.tool_key)
        x["by_status"][d.status] = x["by_status"].get(d.status, 0) + 1
        x["waits"].append(cal.between(d.created_at, d.decided_at) / 3600)
        x["times"] += [s.decision_ms / 1000 for s in d.steps if s.decision_ms is not None]
        if d.recommended_option:
            x["with_rec"] += 1
            x["overrides"] += OPTION[d.status] != d.recommended_option
        x["late"] += any(s.overdue_at is not None for s in d.steps)
    for d in pending:
        x = t(d.tool_key)
        x["pending"] += 1
        step = d.current_step
        x["pending_late"] += bool(step and step.overdue_at)
    by_type = [{
        "tool_key": k, "title": TOOLS[k].title if k in TOOLS else k,
        "decided": sum(x["by_status"].values()), "by_status": x["by_status"],
        "median_wait_hours": _median(x["waits"]),
        "median_decision_seconds": _median(x["times"]), "overrides": x["overrides"],
        "with_recommendation": x["with_rec"], "override_rate": _rate(x["overrides"],
                                                                     x["with_rec"]),
        "missed_deadlines": x["late"], "pending": x["pending"],
        "pending_past_deadline": x["pending_late"]} for k, x in sorted(types.items())]

    waiting_roles: dict[str, dict[str, Any]] = {}
    for d in pending:
        step = d.current_step
        if step is None:
            continue
        key = str(step.role_id) if step.role_id else "anyone"
        r = waiting_roles.setdefault(key, {"role_id": key if step.role_id else None,
                                           "role": roles.get(step.role_id, "Anyone who can "
                                                                         "decide"),
                                           "waiting": 0, "past_deadline": 0})
        r["waiting"] += 1
        r["past_deadline"] += step.overdue_at is not None
    all_times = [s.decision_ms / 1000 for _, s in steps if s.decision_ms is not None]
    return {"days": days, "since": since, "by_type": by_type, "approvers": approvers,
            "waiting_by_role": sorted(waiting_roles.values(), key=lambda r: -r["waiting"]),
            "overall": {"decisions": len(decided), "steps": len(steps),
                        "median_decision_seconds": _median(all_times),
                        "under_60s": _rate(sum(1 for x in all_times if x < 60), len(all_times)),
                        "measured": len(all_times)}}


async def mine(session: AsyncSession, actor: str, days: int = 30) -> dict[str, Any]:
    """The inbox header for one person: their median decision time and override rate."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = [(d, s) for d, s in await step_rows(session, since) if s.decided_by == actor]
    times = [s.decision_ms / 1000 for _, s in rows if s.decision_ms is not None]
    with_rec = [(d, s) for d, s in rows if d.recommended_option]
    overrides = sum(1 for d, s in with_rec if OPTION[s.status] != d.recommended_option)
    return {"days": days, "decided": len(rows), "median_decision_seconds": _median(times),
            "override_rate": _rate(overrides, len(with_rec)), "overrides": overrides,
            "with_recommendation": len(with_rec)}
