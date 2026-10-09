"""Integration alerts and tasks in "Waiting for you" (decision E7, safeguard 5).

Two new kinds of card next to approvals and notices:
- alert: something is wrong with a connection (revoked key, missing permission, ERP
  unreachable, sync failing, data falling behind, a run stuck). One card per
  connection, listing every current problem with the most serious as its title. It
  updates as problems change and resolves itself when the connection is healthy.
- task: records a person has to act on (records that could not be read, customers
  waiting to be matched). One card per flow or connection, closed when nothing is
  left to do.

Raising, changing and resolving a card are all recorded in the audit chain.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import chain
from app.models import Decision

AGENT_KEY = "erp_connector"
ALERT_TOOL = "integration.alert"
TASK_TOOL = "integration.task"
# Shown on the card where an agent action would show its tool's title.
CARD_TITLES = {ALERT_TOOL: "Integration problem", TASK_TOOL: "Integration task"}
OPEN = ("open", "acknowledged")
SYSTEM = "system:integrations"


@dataclass
class Problem:
    code: str  # check_failed | flow_failing | lagging | stuck
    severity: int  # higher is more serious; the most serious titles the card
    title: str
    detail: str
    fix: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


async def _open_card(session: AsyncSession, key: str) -> Decision | None:
    return await session.scalar(select(Decision).where(
        Decision.dedupe_key == key, Decision.status.in_(OPEN)))


async def sync_alert(session: AsyncSession, tenant_id: uuid.UUID, *, key: str,
                     record_type: str, record_id: uuid.UUID, subject: str,
                     problems: list[Problem]) -> Decision | None:
    """Make the alert card match the current problems: raise it, update it, or resolve
    it when there are none."""
    card = await _open_card(session, f"alert:{key}")
    now = datetime.now(UTC)
    if not problems:
        if card is not None:
            card.status, card.decided_by, card.decided_at = "resolved", SYSTEM, now
            card.note = f"{subject} is healthy again"
            await chain.append(session, tenant_id, "integration.alert.resolved", actor=SYSTEM,
                               agent_key=AGENT_KEY, record_type=record_type,
                               record_id=record_id, data={"decision_id": card.id})
        return None
    problems = sorted(problems, key=lambda p: -p.severity)
    title = f"{subject}: {problems[0].title}"[:300]
    reasons = [p.detail + (f" What to do: {p.fix}" if p.fix else "") for p in problems]
    codes = sorted({p.code for p in problems})
    args = {"problems": [{"code": p.code, "title": p.title, **p.data} for p in problems]}
    if card is None:
        card = Decision(tenant_id=tenant_id, kind="alert", status="open", agent_key=AGENT_KEY,
                        tool_key=ALERT_TOOL, title=title, reasons=reasons, args=args,
                        record_type=record_type, record_id=record_id,
                        dedupe_key=f"alert:{key}")
        session.add(card)
        await session.flush()
        await chain.append(session, tenant_id, "integration.alert.raised", actor=SYSTEM,
                           agent_key=AGENT_KEY, record_type=record_type, record_id=record_id,
                           data={"decision_id": card.id, "title": title, "problems": codes})
    elif card.title != title or card.reasons != reasons:
        before = sorted({p["code"] for p in card.args.get("problems", [])})
        card.title, card.reasons, card.args = title, reasons, args
        if card.status == "acknowledged" and before != codes:
            card.status = "open"  # something new is wrong: show it again
        if before != codes:
            await chain.append(session, tenant_id, "integration.alert.changed", actor=SYSTEM,
                               agent_key=AGENT_KEY, record_type=record_type,
                               record_id=record_id,
                               data={"decision_id": card.id, "title": title, "problems": codes})
    return card


async def sync_task(session: AsyncSession, tenant_id: uuid.UUID, *, key: str,
                    record_type: str, record_id: uuid.UUID, title: str | None,
                    reasons: list[str] | None = None, args: dict[str, Any] | None = None,
                    done_note: str = "Nothing left to do") -> Decision | None:
    """Open, update or close a task card. title=None closes it."""
    card = await _open_card(session, f"task:{key}")
    if title is None:
        if card is not None:
            card.status, card.decided_by, card.decided_at = "done", SYSTEM, datetime.now(UTC)
            card.note = done_note
            await chain.append(session, tenant_id, "integration.task.closed", actor=SYSTEM,
                               agent_key=AGENT_KEY, record_type=record_type,
                               record_id=record_id, data={"decision_id": card.id})
        return None
    if card is None:
        card = Decision(tenant_id=tenant_id, kind="task", status="open", agent_key=AGENT_KEY,
                        tool_key=TASK_TOOL, title=title[:300], reasons=reasons or [],
                        args=args or {}, record_type=record_type, record_id=record_id,
                        dedupe_key=f"task:{key}")
        session.add(card)
        await session.flush()
        await chain.append(session, tenant_id, "integration.task.opened", actor=SYSTEM,
                           agent_key=AGENT_KEY, record_type=record_type, record_id=record_id,
                           data={"decision_id": card.id, "title": card.title})
    else:
        card.title, card.reasons, card.args = title[:300], reasons or [], args or {}
    return card


async def acknowledge_alert(session: AsyncSession, decision_id: uuid.UUID,
                            actor: str) -> Decision | None:
    """A person has seen the alert. It leaves "Waiting for you" but stays on the
    Integrations page until the problem is fixed, and comes back if something new
    goes wrong."""
    d = await session.get(Decision, decision_id)
    if d is None or d.kind != "alert":
        return None
    if d.status == "open":
        d.status, d.decided_by, d.decided_at = "acknowledged", actor, datetime.now(UTC)
        await chain.append(session, d.tenant_id, "integration.alert.acknowledged", actor=actor,
                           agent_key=AGENT_KEY, record_type=d.record_type,
                           record_id=d.record_id, data={"decision_id": d.id})
    return d
