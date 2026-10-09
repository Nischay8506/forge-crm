"""Handing a card to a colleague, and out of office (decision H3, safeguard 3).

The person handing over sets the scope ("Delegator sets scope", Nischay's card,
2026-10-09): for out of office, the card types it covers and, for cards with an
amount, the most it covers. A stand-in never gets more than the person they stand
in for: at decision time Forge checks that the absent person could decide the card
themselves (role, deciding permission, four-eyes), then that the card is inside the
scope. It takes effect at once; creating and ending one is in the audit trail.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import TOOLS
from app.approvals.chain import Inbox, Person, has_amount, people
from app.approvals.notify import notify
from app.audit import chain
from app.core.errors import AppError, Forbidden, NotFound
from app.core.permissions import ALL, has_permission
from app.models import Decision, Delegation

MAX_AWAY_DAYS = 90


def out(dl: Delegation, names: dict[uuid.UUID, str]) -> dict[str, Any]:
    now = datetime.now(UTC)
    active = dl.revoked_at is None and dl.starts_at <= now and (
        dl.kind == "card" or (dl.ends_at is not None and dl.ends_at > now))
    return {"id": str(dl.id), "kind": dl.kind,
            "from_user_id": str(dl.from_user_id), "from_name": names.get(dl.from_user_id),
            "to_user_id": str(dl.to_user_id), "to_name": names.get(dl.to_user_id),
            "decision_id": str(dl.decision_id) if dl.decision_id else None,
            "starts_at": dl.starts_at, "ends_at": dl.ends_at,
            "tool_keys": list(dl.tool_keys or []),
            "tools": [TOOLS[t].title for t in dl.tool_keys or [] if t in TOOLS],
            "max_amount": str(dl.max_amount) if dl.max_amount is not None else None,
            "note": dl.note, "created_by": dl.created_by, "created_at": dl.created_at,
            "revoked_at": dl.revoked_at, "revoked_by": dl.revoked_by, "active": active}


async def _colleague(session: AsyncSession, me: Person, to_user_id: uuid.UUID) -> Person:
    if to_user_id == me.user_id:
        raise AppError("Pick someone other than yourself")
    found = (await people(session, {to_user_id})).get(to_user_id)
    if found is None:
        raise NotFound("That person is not an active user")
    if not has_permission(found.permissions, "decisions.decide"):
        raise AppError(f"{found.name} cannot decide cards (no decisions.decide permission)")
    return found


async def hand_card(session: AsyncSession, me: Person, decision_id: uuid.UUID,
                    to_user_id: uuid.UUID, note: str | None) -> Delegation:
    """Hand one card to a colleague, who then decides it for me. Only someone who could
    decide the card on their own authority can hand it on."""
    d = await session.get(Decision, decision_id)
    if d is None or d.kind != "approval":
        raise NotFound("Decision not found")
    if d.status != "pending" or d.current_step is None:
        raise AppError(f"This decision is already {d.status}")
    inbox = Inbox(me=me)
    auth = inbox.authority(d)
    if not auth.ok or auth.on_behalf_of:
        raise Forbidden("You can only hand on a card you could decide yourself")
    to = await _colleague(session, me, to_user_id)
    if d.requested_by == to.actor:
        raise AppError(f"{to.name} asked for this, so they cannot decide it")
    dl = Delegation(tenant_id=d.tenant_id, kind="card", from_user_id=me.user_id,
                    to_user_id=to.user_id, decision_id=d.id, note=(note or "").strip() or None,
                    created_by=me.actor)
    session.add(dl)
    await session.flush()
    await chain.append(session, d.tenant_id, "delegation.created", actor=me.actor,
                       record_type=d.record_type, record_id=d.record_id, data={
                           "delegation_id": dl.id, "kind": "card", "decision_id": d.id,
                           "from_user_id": me.user_id, "to_user_id": to.user_id,
                           "note": dl.note})
    await notify(session, d, "handed", to_user_id=to.user_id)
    return dl


async def go_away(session: AsyncSession, tenant_id: uuid.UUID, me: Person,
                  to_user_id: uuid.UUID, starts_at: datetime, ends_at: datetime,
                  tool_keys: list[str], max_amount: Decimal | None,
                  note: str | None) -> Delegation:
    """Out of office: a stand-in decides my cards of these types (and, for cards with
    an amount, up to max_amount) between two dates, for me."""
    now = datetime.now(UTC)
    problems = []
    if ends_at <= starts_at:
        problems.append("The end must be after the start")
    if ends_at <= now:
        problems.append("The end is already past")
    if ends_at - max(starts_at, now) > timedelta(days=MAX_AWAY_DAYS):
        problems.append(f"At most {MAX_AWAY_DAYS} days at a time")
    keys = sorted(set(tool_keys))
    if not keys:
        problems.append("Pick the card types the stand-in may decide")
    unknown = [k for k in keys if k not in TOOLS or not TOOLS[k].side_effect]
    if unknown:
        problems.append(f"Unknown card type: {', '.join(unknown)}")
    money = [TOOLS[k].title for k in keys if k in TOOLS and has_amount(k)]
    if money and max_amount is None:
        problems.append(f"Set the most the stand-in may approve for: {', '.join(money)}")
    if max_amount is not None and max_amount <= 0:
        problems.append("The maximum amount must be more than zero")
    if problems:
        raise AppError("; ".join(problems))
    to = await _colleague(session, me, to_user_id)
    clash = await session.scalar(select(Delegation.id).where(
        Delegation.kind == "away", Delegation.from_user_id == me.user_id,
        Delegation.revoked_at.is_(None), Delegation.starts_at < ends_at,
        Delegation.ends_at > starts_at))
    if clash:
        raise AppError("You already have an out-of-office for some of these dates; end it "
                       "first")
    dl = Delegation(tenant_id=tenant_id, kind="away", from_user_id=me.user_id,
                    to_user_id=to.user_id, starts_at=starts_at, ends_at=ends_at,
                    tool_keys=keys, max_amount=max_amount if money else None,
                    note=(note or "").strip() or None, created_by=me.actor)
    session.add(dl)
    await session.flush()
    await chain.append(session, tenant_id, "delegation.created", actor=me.actor, data={
        "delegation_id": dl.id, "kind": "away", "from_user_id": me.user_id,
        "to_user_id": to.user_id, "starts_at": starts_at, "ends_at": ends_at,
        "tool_keys": keys, "max_amount": dl.max_amount, "note": dl.note})
    return dl


async def end(session: AsyncSession, me: Person, delegation_id: uuid.UUID) -> Delegation:
    """End a delegation early: the person who made it, or a full-access admin."""
    dl = await session.get(Delegation, delegation_id)
    if dl is None:
        raise NotFound("Not found")
    if dl.from_user_id != me.user_id and ALL not in me.permissions:
        raise Forbidden("Only the person who handed over can end this")
    if dl.revoked_at is None:
        dl.revoked_at, dl.revoked_by = datetime.now(UTC), me.actor
        await chain.append(session, dl.tenant_id, "delegation.ended", actor=me.actor,
                           data={"delegation_id": dl.id, "kind": dl.kind})
    return dl


async def mine(session: AsyncSession, me: Person) -> list[Delegation]:
    """Delegations I made or received that have not ended (plus the last 20 ended)."""
    now = datetime.now(UTC)
    rows = list(await session.scalars(select(Delegation).where(
        or_(Delegation.from_user_id == me.user_id, Delegation.to_user_id == me.user_id))
        .order_by(Delegation.created_at.desc()).limit(100)))
    live = [r for r in rows if r.revoked_at is None and (
        r.kind == "card" or (r.ends_at is not None and r.ends_at > now))]
    ended = [r for r in rows if r not in live][:20]
    return live + ended

