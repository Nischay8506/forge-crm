"""Audit log, run replay (F12.4) and record takeovers (decision T8)."""

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import controls
from app.audit import chain, replay, retention, seals
from app.core.deps import CurrentUser, require_permission
from app.core.errors import Forbidden, NotFound
from app.core.permissions import has_permission
from app.db import get_db
from app.models import Account, AuditRecord, Contact, LegalHold, Message, RecordTakeover

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
    rows = await replay.audit_rows(db, list(await db.scalars(
        stmt.order_by(AuditRecord.seq.desc()).limit(limit))))
    replay.shown(rows)  # personal data stays hidden here; reveal works per replay (R4)
    return rows


@router.get("/audit/verify")
async def verify_audit(
    current: CurrentUser = Depends(require_permission("audit.read")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Recompute the whole chain for this company and say whether anything changed."""
    res = await chain.verify(db, current.tenant_id)
    return {"ok": res.ok, "checked": res.checked, "first_bad_seq": res.first_bad_seq,
            "problem": res.problem, "starts_at": res.starts_at,
            "details_removed": res.details_removed, "seals": {
                "state": seals.state(), "checked": res.seals_checked,
                "sealed_upto": res.sealed_upto, "sealed_at": res.sealed_at,
                "unknown_keys": res.unknown_seal_keys},
            "warning": res.warning}


# Replay ------------------------------------------------------------------------------

@router.get("/audit/runs")
async def list_runs(
    agent_key: str | None = None, record_id: uuid.UUID | None = None,
    before: datetime | None = None, version: int | None = None,
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    outcome: Literal["proposed", "acted_alone", "asked", "approved", "rejected", "undone",
                     "blocked", "failed"] | None = None,
    limit: int = Query(50, le=200),
    _: CurrentUser = Depends(require_permission("audit.read")),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Agent runs, newest first, each with how its steps ended."""
    return await replay.list_runs(db, agent_key=agent_key, record_id=record_id, before=before,
                                  version=version, month=month, outcome=outcome, limit=limit)


@router.get("/audit/runs/{run_id}")
async def replay_run(
    run_id: uuid.UUID, _: CurrentUser = Depends(require_permission("audit.read")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """One run step by step: its audit records, decision cards and model calls, and
    whether those records are intact. Personal data is hidden (R4)."""
    return await replay.replay(db, run_id)


class RevealIn(BaseModel):
    reason: str = Field(min_length=10, max_length=500)


@router.post("/audit/runs/{run_id}/reveal")
async def reveal_run(
    run_id: uuid.UUID, body: RevealIn,
    current: CurrentUser = Depends(require_permission("audit.read")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """The same replay with personal data shown, for a person allowed to see it, after
    they say why. The reveal itself goes into the audit trail (R4)."""
    if not has_permission(current.permissions, "audit.personal_data"):
        raise Forbidden("Seeing personal data in the audit trail needs the permission "
                        "\"See personal data in the audit trail\"")
    out = await replay.replay(db, run_id, reveal=True)
    await chain.append(db, current.tenant_id, "audit.personal_data_revealed",
                       actor=_actor(current), record_type="agent_run", record_id=run_id,
                       data={"reason": body.reason.strip()})
    return out


# Retention and legal holds (R5) ------------------------------------------------------

@router.get("/audit/retention")
async def get_retention(
    current: CurrentUser = Depends(require_permission("audit.read")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """The company's retention periods per category, the law each follows, and whether
    a CA or lawyer has confirmed them."""
    return {**await retention.current(db, current.tenant_id),
            "categories": retention.CATEGORIES,
            "profiles": {k: v["label"] for k, v in retention.PROFILES.items()},
            "minimums": {"skeleton": retention.SKELETON_FLOOR, "other": retention.MINIMUM}}


class RetentionIn(BaseModel):
    profile: str | None = None
    periods: dict[str, int] | None = None
    basis: dict[str, str] | None = None


@router.put("/audit/retention")
async def put_retention(
    body: RetentionIn, current: CurrentUser = Depends(require_permission("audit.manage")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await retention.change(db, current.tenant_id, _actor(current), profile=body.profile,
                           periods=body.periods, basis=body.basis)
    return await get_retention(current, db)


class ReviewIn(BaseModel):
    reviewed_by: str = Field(min_length=3, max_length=200)
    note: str | None = Field(default=None, max_length=1000)


@router.post("/audit/retention/legal-review")
async def legal_review(
    body: ReviewIn, current: CurrentUser = Depends(require_permission("audit.manage")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Record who confirmed the current periods (Step 4 safeguard 4)."""
    await retention.record_review(db, current.tenant_id, _actor(current),
                                  reviewed_by=body.reviewed_by, note=body.note)
    return await get_retention(current, db)


@router.post("/audit/retention/run")
async def run_retention(
    current: CurrentUser = Depends(require_permission("audit.manage")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Run tonight's clean-up now, for this company."""
    return {"removed": await retention.run_tenant(db, current.tenant_id)}


def _hold(h: LegalHold, names: dict[uuid.UUID, str]) -> dict[str, Any]:
    return {"id": str(h.id), "account_id": str(h.account_id),
            "account_name": names.get(h.account_id), "reason": h.reason,
            "placed_by": h.placed_by, "placed_at": h.placed_at, "lifted_by": h.lifted_by,
            "lifted_at": h.lifted_at}


@router.get("/audit/legal-holds")
async def list_holds(
    account_id: uuid.UUID | None = None, include_lifted: bool = False,
    _: CurrentUser = Depends(require_permission("accounts.read")),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = select(LegalHold)
    if account_id:
        stmt = stmt.where(LegalHold.account_id == account_id)
    if not include_lifted:
        stmt = stmt.where(LegalHold.lifted_at.is_(None))
    holds = list(await db.scalars(stmt.order_by(LegalHold.placed_at.desc())))
    names = dict((await db.execute(select(Account.id, Account.name).where(
        Account.id.in_([h.account_id for h in holds])))).all()) if holds else {}
    return [_hold(h, names) for h in holds]


class HoldIn(BaseModel):
    account_id: uuid.UUID
    reason: str = Field(min_length=5, max_length=500)


@router.post("/audit/legal-holds", status_code=201)
async def place_hold(
    body: HoldIn, current: CurrentUser = Depends(require_permission("audit.manage")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    acc = await db.get(Account, body.account_id)
    if acc is None:
        raise NotFound("Account not found")
    h = await retention.place_hold(db, current.tenant_id, acc.id, body.reason, _actor(current))
    return _hold(h, {acc.id: acc.name})


class LiftIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


@router.post("/audit/legal-holds/{hold_id}/lift")
async def lift_hold(
    hold_id: uuid.UUID, body: LiftIn,
    current: CurrentUser = Depends(require_permission("audit.manage")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    h = await db.get(LegalHold, hold_id)
    if h is None or h.lifted_at is not None:
        raise NotFound("Active legal hold not found")
    await retention.lift_hold(db, h, _actor(current), body.note)
    acc = await db.get(Account, h.account_id)
    return _hold(h, {h.account_id: acc.name if acc else None})


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


# Messages and where they came from (R7) ----------------------------------------------

def _message(m: Message, account: str | None, contact: str | None) -> dict[str, Any]:
    return {"id": str(m.id), "channel": m.channel, "direction": m.direction,
            "status": m.status, "purpose": m.purpose, "subject": m.subject, "body": m.body,
            "ai_generated": m.ai_generated, "disclosure": m.disclosure,
            "language": m.language, "script": m.script,
            "account_id": str(m.account_id) if m.account_id else None, "account_name": account,
            "contact_id": str(m.contact_id) if m.contact_id else None, "contact_name": contact,
            "user_id": str(m.user_id) if m.user_id else None,
            "to_address": m.to_address, "from_address": m.from_address,
            "provider": m.provider, "reason": m.reason, "send_after": m.send_after,
            "attempts": m.attempts, "checks": m.checks, "feature": m.feature,
            "in_reply_to_id": str(m.in_reply_to_id) if m.in_reply_to_id else None,
            "override_by": m.override_by,
            "run_id": str(m.run_id) if m.run_id else None, "created_by": m.created_by,
            "created_at": m.created_at, "status_at": m.status_at, "sent_at": m.sent_at,
            "sent_by": m.sent_by, "erased_at": m.erased_at,
            "enquiry_id": str(m.enquiry_id) if m.enquiry_id else None}


async def _names(db: AsyncSession, m: Message) -> tuple[str | None, str | None]:
    acc = await db.get(Account, m.account_id) if m.account_id else None
    con = await db.get(Contact, m.contact_id) if m.contact_id else None
    return (acc.name if acc else None), (con.full_name if con else None)


async def _provenance(db: AsyncSession, message_id: uuid.UUID, reveal: bool) -> dict[str, Any]:
    m = await db.get(Message, message_id)
    if m is None:
        raise NotFound("Message not found")
    account, contact = await _names(db, m)
    msg = _message(m, account, contact)
    run = await replay.replay(db, m.run_id, reveal=True) if m.run_id else None
    # Who held the account or contact back from agents, and when (the takeover trail).
    subjects = [i for i in (m.account_id, m.contact_id) if i]
    trail = await replay.audit_rows(db, list(await db.scalars(
        select(AuditRecord).where(AuditRecord.kind.startswith("takeover."),
                                  AuditRecord.record_id.in_(subjects))
        .order_by(AuditRecord.seq)))) if subjects else []
    active = list(await db.scalars(select(RecordTakeover).where(
        RecordTakeover.record_id.in_(subjects), RecordTakeover.released_at.is_(None)))) \
        if subjects else []
    rows = (run["steps"] if run else []) + trail
    extra = [msg] + (run["decisions"] if run else [])
    names = [contact] if contact else []
    orgs = [account] if account else []
    if run:
        names += [r["name"] for r in run["records"] if r["record_type"] == "contact" and r["name"]]
        orgs += [r["name"] for r in run["records"] if r["record_type"] == "account" and r["name"]]
    personal = replay.shown(rows, extra=extra, names=names, orgs=orgs, reveal=reveal)
    if run and not reveal:
        for r in run["records"]:
            if r["record_type"] == "contact" and r["name"]:
                r["name"] = "[name hidden]"
    if run:
        run["personal_data"] = personal
    return {"message": msg, "run": run, "takeovers": trail,
            "active_takeovers": [_takeover(t) for t in active], "personal_data": personal}


@router.get("/audit/messages/{message_id}")
async def message_provenance(
    message_id: uuid.UUID, _: CurrentUser = Depends(require_permission("audit.read")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Everything behind one message: the agent run that wrote it, step by step, what
    the agent was given, who approved, and the takeover trail of its account and
    contact. Personal data is hidden (R4)."""
    return await _provenance(db, message_id, reveal=False)


@router.post("/audit/messages/{message_id}/reveal")
async def reveal_message(
    message_id: uuid.UUID, body: RevealIn,
    current: CurrentUser = Depends(require_permission("audit.read")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if not has_permission(current.permissions, "audit.personal_data"):
        raise Forbidden("Seeing personal data in the audit trail needs the permission "
                        "\"See personal data in the audit trail\"")
    out = await _provenance(db, message_id, reveal=True)
    await chain.append(db, current.tenant_id, "audit.personal_data_revealed",
                       actor=_actor(current), record_type="message", record_id=message_id,
                       data={"reason": body.reason.strip()})
    return out
