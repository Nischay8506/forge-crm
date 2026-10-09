"""An agent's track record, per month and agent version (decision R8; Step 4
safeguard 7). Counted from the audit trail, so every number links back to the
records behind it:

- proposed: actions the agent proposed (re-proposals after a person approved are
  not counted twice);
- acted alone: actions it carried out without a person (its level and the rules
  allowed it);
- asked / approved / rejected: actions it had to ask a person about, and what the
  person decided; a rejection is a person overriding the agent;
- undone: actions it carried out that a person later reversed (merges undone),
  also an override;
- blocked: actions the rules or a stop refused;
- failed: actions that went wrong while running.

override rate = (rejected + undone) / (acted alone + approved + rejected).

These numbers inform a person; they never change an agent's level. Raising a level
is always an explicit decision by someone with "agents.manage".
"""

from typing import Any

from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.versions import describe_change
from app.config import get_settings
from app.models import AgentRun, AgentVersion, AuditRecord

OUTCOMES = ("proposed", "acted_alone", "asked", "approved", "rejected", "undone", "blocked",
            "failed")

_KIND = {"asked": "decision.requested", "approved": "decision.approved",
         "rejected": "decision.rejected", "blocked": "action.blocked",
         "failed": "action.failed"}


def _undone_merge_ids():
    return select(AuditRecord.data["merge_event_id"].astext).where(
        AuditRecord.kind == "merge.undone").scalar_subquery()


def _merge_id():
    return func.coalesce(AuditRecord.data["result_ids"]["merge_event_id"].astext,
                         AuditRecord.data["result"]["merge_event_id"].astext)


def outcome_filter(outcome: str):
    """SQL condition on AuditRecord picking the records behind one count."""
    if outcome == "proposed":
        return and_(AuditRecord.kind == "action.proposed",
                    AuditRecord.data["approved_by"].astext.is_(None))
    if outcome == "acted_alone":
        return and_(AuditRecord.kind == "action.executed", AuditRecord.actor.startswith("agent:"))
    if outcome == "undone":
        return and_(AuditRecord.kind == "action.executed", _merge_id().in_(_undone_merge_ids()))
    return AuditRecord.kind == _KIND[outcome]


def _month():
    tz = get_settings().integration_local_time_zone
    return func.to_char(func.timezone(tz, AuditRecord.created_at), "YYYY-MM")


async def scorecard(session: AsyncSession, agent_key: str, *, months: int = 12
                    ) -> dict[str, Any]:
    month = _month().label("month")
    rows: dict[tuple[str, int | None], dict[str, int]] = {}
    for outcome in OUTCOMES:
        stmt = (select(month, AgentRun.agent_version, func.count())
                .join(AgentRun, AgentRun.id == AuditRecord.run_id)
                .where(AuditRecord.agent_key == agent_key, outcome_filter(outcome))
                .group_by(month, AgentRun.agent_version))
        for m, v, n in (await session.execute(stmt)).all():
            rows.setdefault((m, v), dict.fromkeys(OUTCOMES, 0))[outcome] = n
    out = []
    for (m, v), c in sorted(rows.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0),
                            reverse=True):
        base = c["acted_alone"] + c["approved"] + c["rejected"]
        out.append({"month": m, "version": v, **c,
                    "override_rate": round((c["rejected"] + c["undone"]) / base, 4)
                    if base else None})
    months_seen = sorted({r["month"] for r in out}, reverse=True)[:months]
    out = [r for r in out if r["month"] in months_seen]

    vers = list(await session.scalars(select(AgentVersion).where(
        AgentVersion.agent_key == agent_key).order_by(AgentVersion.number)))
    history, prev = [], None
    for v in vers:
        history.append({"number": v.number, "first_seen_at": v.first_seen_at,
                        "changes": describe_change(prev, v.definition)})
        prev = v.definition
    return {"agent_key": agent_key, "rows": out, "versions": list(reversed(history))}


def run_ids_for(agent_key: str, outcome: str, version: int | None,
                month: str | None) -> Select:
    """Subquery of run ids behind one count, for the runs list's filters."""
    stmt = (select(AuditRecord.run_id).join(AgentRun, AgentRun.id == AuditRecord.run_id)
            .where(AuditRecord.agent_key == agent_key, outcome_filter(outcome)))
    if version is not None:
        stmt = stmt.where(AgentRun.agent_version == version)
    if month:
        stmt = stmt.where(_month() == month)
    return stmt

