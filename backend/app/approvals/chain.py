"""Approver chains (decision H1) and who may decide a card (H3, safeguards 1 and 3).

A card has one or more steps, each decided by a role (or by anyone who can decide,
when the rule names no role). Step 1 decides first; once approved, step 2 starts,
and so on. The action runs only after the last step approves; a rejection at any
step ends the card. Each step is decided by a different person, and the person who
asked for the action never decides any step, not even on someone else's behalf.

Who may decide the current step:
  - a person holding its role, or any full-access admin (decision U3 A), who also
    holds the tool's own deciding permission where it has one (e.g. erasure);
  - a stand-in acting for such a person (H3): through a card handed to them, or
    through an out-of-office whose scope (card types, and the most it covers for
    cards with an amount) includes this card. The stand-in decides "for" that
    person, with no more authority than theirs.
Escalation never adds anyone: a card past its deadline is shown and notified to the
next role up, but only the people above may decide it (safeguard 4).
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import TOOLS
from app.approvals.calendar import Calendar
from app.core.permissions import ALL, has_permission
from app.models import Decision, DecisionStep, Delegation, Role, User, UserRole

NOT_WAITING = "This is not waiting for a decision"
OWN_REQUEST = "A second person must decide this: you asked for it"
EARLIER_STEP = "You approved an earlier step: a different person decides each step"
ROLE = "role"  # the person lacks the step's role; the API names the role


# --- steps ---------------------------------------------------------------------------

def start_step(step: DecisionStep, sla_hours: Decimal, cal: Calendar, reminder_pct: int,
               now: datetime) -> None:
    step.status, step.started_at = "pending", now
    step.due_at = cal.add(now, sla_hours)
    step.remind_at = cal.add(now, Decimal(sla_hours) * reminder_pct / 100)


def open_steps(d: Decision, roles: list[uuid.UUID | None], sla_hours: Decimal, cal: Calendar,
               reminder_pct: int, now: datetime | None = None) -> None:
    """Give a new card its chain; the first step starts now."""
    now = now or datetime.now(UTC)
    d.sla_hours = sla_hours
    d.step_index = 0
    d.steps = [DecisionStep(tenant_id=d.tenant_id, position=i, role_id=r, status="waiting")
               for i, r in enumerate(roles or [None])]
    start_step(d.steps[0], sla_hours, cal, reminder_pct, now)
    d.approver_role_id = d.steps[0].role_id


async def open_chain(session: AsyncSession, d: Decision,
                     roles: list[uuid.UUID | None] | None = None,
                     sla_hours: Decimal | None = None) -> None:
    """open_steps with the company's calendar, for cards raised outside call_tool."""
    from app.approvals.calendar import calendar

    cal, cfg = await calendar(session, d.tenant_id)
    open_steps(d, roles or [], sla_hours or cfg["default_sla_hours"], cal, cfg["reminder_pct"])


def approvers_so_far(d: Decision) -> set[str]:
    """Everyone whose approval already counts on this card, and on whose behalf."""
    out: set[str] = set()
    for s in d.steps:
        if s.status == "approved":
            out |= {a for a in (s.decided_by, s.on_behalf_of) if a}
    return out


def close_steps(d: Decision, status: str) -> None:
    """End the steps that never got to decide (cancelled after a rejection, or
    superseded with their card)."""
    for s in d.steps:
        if s.status in ("waiting", "pending"):
            s.status = status


# --- people ----------------------------------------------------------------------------

@dataclass
class Person:
    user_id: uuid.UUID
    permissions: set[str]
    roles: set[uuid.UUID]
    name: str = ""

    @property
    def actor(self) -> str:
        return f"user:{self.user_id}"


async def people(session: AsyncSession, user_ids: set[uuid.UUID] | None = None
                 ) -> dict[uuid.UUID, Person]:
    """Active people with their roles and permissions (all, or the ones asked for)."""
    stmt = select(User).where(User.is_active.is_(True))
    if user_ids is not None:
        if not user_ids:
            return {}
        stmt = stmt.where(User.id.in_(user_ids))
    users = {u.id: u for u in await session.scalars(stmt)}
    if not users:
        return {}
    perms = dict((await session.execute(select(Role.id, Role.permissions))).all())
    out = {uid: Person(uid, set(), set(), u.full_name) for uid, u in users.items()}
    for uid, rid in (await session.execute(
            select(UserRole.user_id, UserRole.role_id).where(UserRole.user_id.in_(users)))).all():
        out[uid].roles.add(rid)
        out[uid].permissions |= set(perms.get(rid) or [])
    return out


def _amount(d: Decision) -> Decimal | None:
    try:
        v = d.facts.get("amount")
        return Decimal(str(v)) if v is not None else None
    except (InvalidOperation, ValueError):
        return None


def has_amount(tool_key: str) -> bool:
    tool = TOOLS.get(tool_key)
    return bool(tool and "amount" in tool.facts)


def in_scope(dl: Delegation, d: Decision, now: datetime) -> bool:
    """Does this delegation cover this card right now (safeguard 3)?"""
    if dl.revoked_at is not None or dl.starts_at > now:
        return False
    if dl.kind == "card":
        return dl.decision_id == d.id
    if dl.ends_at is None or dl.ends_at <= now or d.tool_key not in (dl.tool_keys or []):
        return False
    if has_amount(d.tool_key):
        amount = _amount(d)
        return dl.max_amount is not None and amount is not None and amount <= dl.max_amount
    return True


def direct(d: Decision, step: DecisionStep, p: Person) -> str | None:
    """Why this person cannot decide this step on their own authority (None: they can)."""
    tool = TOOLS.get(d.tool_key)
    if tool and tool.decider_permission and not has_permission(p.permissions,
                                                               tool.decider_permission):
        return ("Only people allowed to erase personal data can decide this"
                if tool.decider_permission == "privacy.erase"
                else f"This needs the permission {tool.decider_permission}")
    if not has_permission(p.permissions, "decisions.decide"):
        return "This needs the permission decisions.decide"
    if step.role_id is None or ALL in p.permissions or step.role_id in p.roles:
        return None
    return ROLE


def conflict(d: Decision, actor: str) -> str | None:
    """Four-eyes (H3, safeguard 1): never your own request, never two steps."""
    if d.requested_by and d.requested_by == actor:
        return OWN_REQUEST
    if actor in approvers_so_far(d):
        return EARLIER_STEP
    return None


@dataclass
class Authority:
    ok: bool
    reason: str | None = None
    on_behalf_of: str | None = None  # "user:<id>" of the person they stand in for
    delegation_id: uuid.UUID | None = None


@dataclass
class Inbox:
    """One person's view of the inbox: what they may decide, and as whose stand-in.
    Loads their delegations once, so a page of cards costs a few queries."""

    me: Person
    delegations: list[Delegation] = field(default_factory=list)
    delegators: dict[uuid.UUID, Person] = field(default_factory=dict)
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    async def load(cls, session: AsyncSession, me: Person) -> "Inbox":
        now = datetime.now(UTC)
        dls = list(await session.scalars(select(Delegation).where(
            Delegation.to_user_id == me.user_id, Delegation.revoked_at.is_(None),
            Delegation.starts_at <= now,
            or_(Delegation.kind == "card", Delegation.ends_at > now))))
        found = await people(session, {dl.from_user_id for dl in dls})
        return cls(me=me, delegations=dls, delegators=found, now=now)

    def authority(self, d: Decision) -> Authority:
        if d.kind != "approval" or d.status != "pending" or d.current_step is None:
            return Authority(False, NOT_WAITING)
        step = d.current_step
        why = conflict(d, self.me.actor)
        if why:
            return Authority(False, why)
        why = direct(d, step, self.me)
        if why is None:
            return Authority(True)
        for dl in self.delegations:
            boss = self.delegators.get(dl.from_user_id)
            if boss is None or not in_scope(dl, d, self.now):
                continue
            if conflict(d, boss.actor) or direct(d, step, boss) is not None:
                continue
            return Authority(True, on_behalf_of=boss.actor, delegation_id=dl.id)
        return Authority(False, why)


async def deciders(session: AsyncSession, d: Decision, *, with_stand_ins: bool = True
                   ) -> set[uuid.UUID]:
    """Everyone who may decide the card's current step now (to notify them)."""
    step = d.current_step
    if d.status != "pending" or step is None:
        return set()
    everyone = await people(session)
    now = datetime.now(UTC)
    out = {uid for uid, p in everyone.items()
           if conflict(d, p.actor) is None and direct(d, step, p) is None}
    if with_stand_ins and out:
        dls = await session.scalars(select(Delegation).where(
            Delegation.from_user_id.in_(out), Delegation.revoked_at.is_(None)))
        for dl in dls:
            stand_in = everyone.get(dl.to_user_id)
            if stand_in and in_scope(dl, d, now) and conflict(d, stand_in.actor) is None \
                    and has_permission(stand_in.permissions, "decisions.decide"):
                out.add(dl.to_user_id)
    return out


async def escalation_targets(session: AsyncSession, d: Decision,
                             escalation_role_id: uuid.UUID | None) -> set[uuid.UUID]:
    """Who hears about a card past its deadline (H4): the chain's next role, else the
    company's escalation role, else full-access admins. They are told; it does not
    let them decide a step that is not theirs."""
    nxt = d.steps[d.step_index + 1] if d.step_index + 1 < len(d.steps) else None
    role = nxt.role_id if nxt and nxt.role_id else escalation_role_id
    everyone = await people(session)
    if role is not None:
        found = {uid for uid, p in everyone.items() if role in p.roles}
        if found:
            return found
    return {uid for uid, p in everyone.items() if ALL in p.permissions}


def escalated_to(d: Decision, me: Person, escalation_role_id: uuid.UUID | None) -> bool:
    step = d.current_step
    if d.status != "pending" or step is None or step.overdue_at is None:
        return False
    nxt = d.steps[d.step_index + 1] if d.step_index + 1 < len(d.steps) else None
    role = nxt.role_id if nxt and nxt.role_id else escalation_role_id
    if role is not None and role in me.roles:
        return True
    return role is None and ALL in me.permissions


def step_summary(d: Decision, role_names: dict[uuid.UUID, str]) -> list[dict[str, Any]]:
    return [{"position": s.position + 1, "role_id": str(s.role_id) if s.role_id else None,
             "role": role_names.get(s.role_id) if s.role_id else None, "status": s.status,
             "started_at": s.started_at, "due_at": s.due_at, "overdue_at": s.overdue_at,
             "reminded_at": s.reminded_at, "decided_by": s.decided_by,
             "on_behalf_of": s.on_behalf_of, "decided_at": s.decided_at, "note": s.note,
             "decision_ms": s.decision_ms} for s in d.steps]
