"""The approval inbox (F12.3): what a decision led to.

A decision is auditable only with its downstream effect (F12.3 Done-When), so each
decided card can list what happened next: the steps of its run after the decision
(the action carried out, or why it failed) and later records that point at what the
action produced, such as a merge being undone.
"""

import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditRecord, Decision


def _produced(result: dict[str, Any] | None) -> list[tuple[str, str]]:
    """(key, id) pairs for the records an action produced, e.g. ("merge_event_id", ...)."""
    inner = (result or {}).get("result") or {}
    pairs = []
    for k, v in inner.items():
        if not (k.endswith("_id") and isinstance(v, str)):
            continue
        try:
            uuid.UUID(v)
        except ValueError:
            continue
        pairs.append((k, v))
    return pairs


async def what_happened_next(session: AsyncSession, d: Decision,
                             limit: int = 20) -> list[AuditRecord]:
    """Audit records after the decision: its run's later steps, and later records
    naming what the action produced. Oldest first."""
    if d.decided_at is None:
        return []
    links = [AuditRecord.data.contains({k: v}) for k, v in _produced(d.result)]
    if d.run_id:
        links.append(AuditRecord.run_id == d.run_id)
    if not links:
        return []
    stmt = (select(AuditRecord)
            .where(AuditRecord.created_at >= d.decided_at, or_(*links),
                   AuditRecord.kind.not_in(("decision.approved", "decision.rejected")))
            .order_by(AuditRecord.seq).limit(limit))
    return list(await session.scalars(stmt))
