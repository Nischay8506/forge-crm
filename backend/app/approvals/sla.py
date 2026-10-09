"""Deadlines, reminders, escalation and bottleneck alerts (decisions H4 and H9).

Runs every minute in the worker (forge.inbox_tick). For each step deciding now:
  - at its reminder time (75% of its working hours by default) the people who may
    decide it are reminded;
  - at its deadline it is marked past due, the card turns red, and the next role up
    (the chain's next role, else the company's escalation role, else full-access
    admins) is told. Nothing is ever approved or rejected because time ran out, and
    no step is skipped (safeguard 4).
Bottleneck alert cards go to full-access admins and people who manage the inbox
when a role has more cards past their deadline than the company's limit (default
more than 5), or a card type's median wait (in working hours) is at least twice
last week's. An alert resolves itself once the cause is gone.
"""

import logging
import statistics
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import TOOLS
from app.approvals.calendar import Calendar, calendar
from app.approvals.notify import notify, tell
from app.audit import chain
from app.core.permissions import ALL, has_permission
from app.models import Decision, DecisionStep, Role

log = logging.getLogger(__name__)

SYSTEM = "system:inbox"
AGENT_KEY = "approval_inbox"
ALERT_TOOL = "inbox.bottleneck"
OPEN = ("open", "acknowledged")
MIN_SAMPLE = 3  # cards per week before a median wait is compared


async def tick_tenant(session: AsyncSession, tenant_id: uuid.UUID,
                      now: datetime | None = None, *, bottlenecks: bool = True
                      ) -> dict[str, int]:
    now = now or datetime.now(UTC)
    cal, cfg = await calendar(session, tenant_id)
    steps = list(await session.scalars(select(DecisionStep).where(
        DecisionStep.status == "pending",
        or_((DecisionStep.remind_at <= now) & DecisionStep.reminded_at.is_(None),
            (DecisionStep.due_at <= now) & DecisionStep.overdue_at.is_(None)))))
    counts = {"reminded": 0, "overdue": 0, "alerts": 0}
    for step in steps:
        d = await session.get(Decision, step.decision_id)
        if d is None or d.status != "pending" or d.current_step is not step:
            continue
        if step.due_at is not None and step.due_at <= now and step.overdue_at is None:
            step.overdue_at = now
            step.reminded_at = step.reminded_at or now
            await chain.append(session, tenant_id, "decision.overdue", actor=SYSTEM,
                               agent_key=d.agent_key, run_id=d.run_id,
                               record_type=d.record_type, record_id=d.record_id, data={
                                   "decision_id": d.id, "step": step.position + 1,
                                   "steps": len(d.steps), "role_id": step.role_id,
                                   "due_at": step.due_at})
            await notify(session, d, "overdue")
            counts["overdue"] += 1
        elif step.reminded_at is None:
            step.reminded_at = now
            await notify(session, d, "reminder")
            counts["reminded"] += 1
    if bottlenecks:
        counts["alerts"] = await check_bottlenecks(session, tenant_id, cal, cfg, now)
    return counts


async def _managers(session: AsyncSession) -> set[uuid.UUID]:
    from app.approvals.chain import people

    return {uid for uid, p in (await people(session)).items()
            if ALL in p.permissions or has_permission(p.permissions, "decisions.manage")}


async def _sync_alert(session: AsyncSession, tenant_id: uuid.UUID, key: str,
                      title: str | None, reasons: list[str], args: dict[str, Any]) -> int:
    """Raise, update or resolve one bottleneck alert. Returns 1 if newly raised."""
    card = await session.scalar(select(Decision).where(
        Decision.dedupe_key == key, Decision.kind == "alert", Decision.status.in_(OPEN)))
    now = datetime.now(UTC)
    if title is None:
        if card is not None:
            card.status, card.decided_by, card.decided_at = "resolved", SYSTEM, now
            card.note = "Cleared: no longer piling up"
            await chain.append(session, tenant_id, "inbox.alert.resolved", actor=SYSTEM,
                               agent_key=AGENT_KEY, data={"decision_id": card.id})
        return 0
    if card is not None:
        card.title, card.reasons, card.args = title[:300], reasons, args
        return 0
    card = Decision(tenant_id=tenant_id, kind="alert", status="open", agent_key=AGENT_KEY,
                    tool_key=ALERT_TOOL, title=title[:300], reasons=reasons, args=args,
                    dedupe_key=key)
    session.add(card)
    await session.flush()
    await chain.append(session, tenant_id, "inbox.alert.raised", actor=SYSTEM,
                       agent_key=AGENT_KEY, data={"decision_id": card.id, "kind": key})
    await tell(session, tenant_id, await _managers(session), "Approvals are piling up",
               title, "/decisions", key)
    return 1


async def overdue_by_role(session: AsyncSession) -> dict[uuid.UUID | None, int]:
    rows = (await session.execute(
        select(DecisionStep.role_id).join(Decision, Decision.id == DecisionStep.decision_id)
        .where(DecisionStep.status == "pending", DecisionStep.overdue_at.is_not(None),
               Decision.status == "pending"))).all()
    out: dict[uuid.UUID | None, int] = {}
    for (role_id,) in rows:
        out[role_id] = out.get(role_id, 0) + 1
    return out


async def weekly_waits(session: AsyncSession, cal: Calendar, now: datetime
                       ) -> dict[str, tuple[list[float], list[float]]]:
    """Per card type: working-hour waits of steps decided this week and last week."""
    since = now - timedelta(days=14)
    rows = (await session.execute(
        select(Decision.tool_key, DecisionStep.started_at, DecisionStep.decided_at)
        .join(Decision, Decision.id == DecisionStep.decision_id)
        .where(DecisionStep.decided_at >= since, DecisionStep.started_at.is_not(None),
               DecisionStep.status.in_(("approved", "rejected", "countered"))))).all()
    out: dict[str, tuple[list[float], list[float]]] = {}
    week = now - timedelta(days=7)
    for tool_key, started, decided in rows:
        this, last = out.setdefault(tool_key, ([], []))
        (this if decided >= week else last).append(cal.between(started, decided) / 3600)
    return out


async def check_bottlenecks(session: AsyncSession, tenant_id: uuid.UUID, cal: Calendar,
                            cfg: dict[str, Any], now: datetime) -> int:
    raised = 0
    limit = int(cfg["bottleneck_overdue"])
    names = dict((await session.execute(select(Role.id, Role.name))).all())
    late = await overdue_by_role(session)
    keys_seen = set()
    for role_id, n in late.items():
        key = f"bottleneck:role:{role_id or 'anyone'}"
        keys_seen.add(key)
        who = names.get(role_id, "Anyone who can decide") if role_id else "Anyone who can decide"
        title = (f"{who}: {n} cards past their deadline" if n > limit else None)
        raised += await _sync_alert(session, tenant_id, key, title, [
            f"{n} cards waiting for {who} are past their deadline (the alert starts above "
            f"{limit}).", "Nothing is approved or skipped because of the delay. Hand cards "
            "to a colleague, set a stand-in, or ask the people in this role to decide."],
            {"role_id": str(role_id) if role_id else None, "count": n})
    for key in await _open_keys(session, "bottleneck:role:"):
        if key not in keys_seen:
            raised += await _sync_alert(session, tenant_id, key, None, [], {})
    ratio = float(cfg["bottleneck_wait_ratio"])
    waits = await weekly_waits(session, cal, now)
    seen_slow = set()
    for tool_key, (this, last) in waits.items():
        key = f"bottleneck:slow:{tool_key}"
        seen_slow.add(key)
        title = None
        if len(this) >= MIN_SAMPLE and len(last) >= MIN_SAMPLE:
            now_m, before = statistics.median(this), statistics.median(last)
            if before > 0 and now_m >= ratio * before:
                what = TOOLS[tool_key].title if tool_key in TOOLS else tool_key
                title = (f"{what}: median wait {now_m:.1f} working hours, up from "
                         f"{before:.1f} last week")
        raised += await _sync_alert(session, tenant_id, key, title, [
            f"Cards of this type now wait at least {ratio:g} times as long as last week "
            "for a decision (median, in working hours).",
            "See the Approvals view for who they wait for."],
            {"tool_key": tool_key, "this_week": len(this), "last_week": len(last)})
    for key in await _open_keys(session, "bottleneck:slow:"):
        if key not in seen_slow:
            raised += await _sync_alert(session, tenant_id, key, None, [], {})
    return raised


async def _open_keys(session: AsyncSession, prefix: str) -> list[str]:
    return list(await session.scalars(select(Decision.dedupe_key).where(
        Decision.kind == "alert", Decision.status.in_(OPEN),
        Decision.dedupe_key.like(f"{prefix}%"))))


async def tick_all(now: datetime | None = None) -> dict[str, int]:
    """The every-minute job across tenants. Bottlenecks are checked every 15 minutes."""
    from app.db import get_sessionmaker, tenant_session
    from app.models import Tenant

    now = now or datetime.now(UTC)
    async with get_sessionmaker()() as s:
        tenant_ids = list(await s.scalars(select(Tenant.id)))
    total = {"reminded": 0, "overdue": 0, "alerts": 0}
    for tid in tenant_ids:
        try:
            async with tenant_session(tid) as s:
                c = await tick_tenant(s, tid, now, bottlenecks=now.minute % 15 == 0)
            for k, v in c.items():
                total[k] += v
        except Exception:  # one tenant's failure must not stop the others
            log.exception("inbox tick for tenant %s failed", tid)
    return total
