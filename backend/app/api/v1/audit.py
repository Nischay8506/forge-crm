"""Audit log, run replay (F12.4) and record takeovers (decision T8)."""

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import controls
from app.audit import chain, replay
from app.core.deps import CurrentUser, require_permission
from app.core.errors import Forbidden, NotFound
from app.core.permissions import has_permission
from app.db import get_db
from app.models import AuditRecord, RecordTakeover

router = APIRouter(tags=["audit"])


def _actor(u: CurrentUser) -> str:
    return f"user:{u.user.id}"


@router.get("/audit")
async def list_audit(
    kind: str | None = None, agent_key: str | None = None, record_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None, actor: str | None = None,
    before_seq: int | None = None, limit: int = Query(50, le=500),
    _: CurrentUser = Depends(require_permission("audit.read")),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Newest first. `kind` may end in '.' to match a family, e.g. 'action.'."""
    stmt = select(AuditRecord)
    if kind:
        stmt = stmt.where(AuditRecord.kind.startswith(kind) if kind.endswith(".")
                          else AuditRecord.kind == kind)
    if agent_key:
        stmt = stmt.where(AuditRecord.agent_key == agent_key)
    if record_id:
        stmt = stmt.where(AuditRecord.record_id == record_id)
    if run_id:
        stmt = stmt.where(AuditRecord.run_id == run_id)
    if actor:
        stmt = stmt.where(AuditRecord.actor == actor)
    if before_seq:
        stmt = stmt.where(AuditRecord.seq < before_seq)
    rows = await db.scalars(stmt.order_by(AuditRecord.seq.desc()).limit(limit))
    return [replay.audit_row(a) for a in rows]


@router.get("/audit/verify")
async def verify_audit(
    current: CurrentUser = Depends(require_permission("audit.read")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Recompute the whole chain for this company and say whether anything changed."""
    res = await chain.verify(db, current.tenant_id)
    return {"ok": res.ok, "checked": res.checked, "first_bad_seq": res.first_bad_seq,
            "problem": res.problem}


# Replay ------------------------------------------------------------------------------

@router.get("/audit/runs")
async def list_runs(
    agent_key: str | None = None, record_id: uuid.UUID | None = None,
    before: datetime | None = None, limit: int = Query(50, le=200),
    _: CurrentUser = Depends(require_permission("audit.read")),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Agent runs, newest first, each with how its steps ended."""
    return await replay.list_runs(db, agent_key=agent_key, record_id=record_id, before=before,
                                  limit=limit)


@router.get("/audit/runs/{run_id}")
async def replay_run(
    run_id: uuid.UUID, _: CurrentUser = Depends(require_permission("audit.read")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """One run step by step: its audit records, decision cards and model calls, and
    whether those records are intact."""
    return await replay.replay(db, run_id)


# Takeovers ---------------------------------------------------------------------------

def _takeover(t: RecordTakeover) -> dict[str, Any]:
    return {"id": str(t.id), "record_type": t.record_type, "record_id": str(t.record_id),
            "taken_by": t.taken_by, "reason": t.reason, "started_at": t.started_at,
            "released_at": t.released_at, "released_by": t.released_by}


class TakeoverIn(BaseModel):
    record_type: Literal["account", "contact"]
    record_id: uuid.UUID
    reason: str | None = Field(default=None, max_length=500)


@router.get("/takeovers")
async def list_takeovers(
    record_type: str | None = None, record_id: uuid.UUID | None = None,
    _: CurrentUser = Depends(require_permission("accounts.read")),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Active takeovers, optionally for one record."""
    stmt = select(RecordTakeover).where(RecordTakeover.released_at.is_(None))
    if record_type:
        stmt = stmt.where(RecordTakeover.record_type == record_type)
    if record_id:
        stmt = stmt.where(RecordTakeover.record_id == record_id)
    return [_takeover(t) for t in await db.scalars(stmt.order_by(RecordTakeover.started_at))]


@router.post("/takeovers", status_code=201)
async def take_over(
    body: TakeoverIn, current: CurrentUser = Depends(require_permission("records.takeover")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from app.steward.records import MODELS

    if await db.get(MODELS[body.record_type], body.record_id) is None:
        raise NotFound(f"{body.record_type.title()} not found")
    t = await controls.take_over(db, current.tenant_id, body.record_type, body.record_id,
                                 _actor(current), body.reason)
    return _takeover(t)


@router.post("/takeovers/{takeover_id}/hand-back")
async def hand_back(
    takeover_id: uuid.UUID, current: CurrentUser = Depends(require_permission("records.takeover")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """The person who took the record over hands it back; so can an agents admin."""
    t = await db.get(RecordTakeover, takeover_id)
    if t is None or t.released_at is not None:
        raise NotFound("Active takeover not found")
    if t.taken_by != _actor(current) and not has_permission(current.permissions,
                                                            "agents.manage"):
        raise Forbidden("Only the person who took this record over can hand it back")
    return _takeover(await controls.hand_back(db, current.tenant_id, takeover_id,
                                              _actor(current)))
