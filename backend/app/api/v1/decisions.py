"""The approval inbox (F12.3, decision T2 and Step 5's H1-H9).

"Waiting for you" holds the cards a person may decide (as themselves or as someone's
stand-in), cards escalated to them, and cards they asked for; "Everyone's" shows
every card waiting. People with the integrations permission also see integration
alerts and tasks (E7); people who manage the inbox see its bottleneck alerts (H9).

Deciding: approve, reject, counter (H2) or hand the card to a colleague (H3). Every
decision carries the fingerprint of the card the person saw; if the card changed in
the meantime the decision is refused (H7). Approving the last step of a chain runs
the action as proposed, through the gate again (kill-switches, takeovers and block
rules still apply).
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import inbox as next_steps
from app.agents import runtime
from app.agents.registry import AGENTS, FACTS, TOOLS, load_definitions
from app.approvals import analytics, calendar, card, delegation, push
from app.approvals.chain import (
    NOT_WAITING,
    ROLE,
    Authority,
    Inbox,
    Person,
    escalated_to,
    has_amount,
    people,
    step_summary,
)
from app.approvals.counter import CounterRefused, counter
from app.approvals.sla import ALERT_TOOL as INBOX_ALERT
from app.audit import chain
from app.audit.replay import audit_rows, record_name, shown
from app.core.deps import CurrentUser, require_permission
from app.core.errors import AppError, Conflict, Forbidden, NotFound, ValidationFailed
from app.core.permissions import has_permission
from app.db import get_db
from app.integrations.alerts import CARD_TITLES, acknowledge_alert
from app.models import Decision, Delegation, PushSubscription, Role, User, UserRole

router = APIRouter(prefix="/decisions", tags=["decisions"])
load_definitions()

TITLES = {**CARD_TITLES, INBOX_ALERT: "Approvals piling up"}
AGENT_NAMES = {"approval_inbox": "Approval inbox"}
CHANGED = ("This card changed while you were looking, so your decision was not saved. "
           "It has been reloaded: check it and decide again.")


class CardChanged(Conflict):
    code = "card_changed"


def _actor(u: CurrentUser) -> str:
    return f"user:{u.user.id}"


async def _me(db: AsyncSession, current: CurrentUser) -> Person:
    roles = set(await db.scalars(select(UserRole.role_id)
                                 .where(UserRole.user_id == current.user.id)))
    return Person(current.user.id, set(current.permissions), roles, current.user.full_name)


async def _inbox(db: AsyncSession, current: CurrentUser) -> Inbox:
    return await Inbox.load(db, await _me(db, current))


def _sees(d: Decision, current: CurrentUser) -> bool:
    """Alerts and tasks: integration ones for the integrations permission (E7), the
    inbox's own bottleneck alerts for people who manage the inbox (H9)."""
    if d.kind not in ("alert", "task"):
        return True
    if d.tool_key == INBOX_ALERT:
        return has_permission(current.permissions, "decisions.manage")
    return has_permission(current.permissions, "integrations.read")


class Ctx:
    """What rendering a page of cards needs, loaded once."""

    def __init__(self, inbox: Inbox, roles: dict[uuid.UUID, str], names: dict[str, str],
                 escalation_role_id: uuid.UUID | None):
        self.inbox, self.roles, self.names = inbox, roles, names
        self.escalation_role_id = escalation_role_id

    @classmethod
    async def load(cls, db: AsyncSession, current: CurrentUser) -> "Ctx":
        roles = dict((await db.execute(select(Role.id, Role.name))).all())
        names = {f"user:{i}": n for i, n in (await db.execute(select(User.id, User.full_name)))}
        cfg = await calendar.load(db, current.tenant_id)
        return cls(await _inbox(db, current), roles, names, cfg["escalation_role_id"])

    def name(self, actor: str | None) -> str | None:
        if not actor:
            return None
        return self.names.get(actor) or ("Forge" if actor.startswith("system:") else None)


def _why(auth: Authority, d: Decision, ctx: Ctx) -> str | None:
    if auth.ok or auth.reason in (None, NOT_WAITING):
        return None
    if auth.reason == ROLE:
        step = d.current_step
        role = ctx.roles.get(step.role_id) if step and step.role_id else None
        return f"Waiting for {role or 'someone who can decide'}"
    return auth.reason


def _counter_fields(d: Decision) -> list[dict[str, Any]]:
    tool = TOOLS.get(d.tool_key)
    out = []
    for f in tool.counter_fields if tool else ():
        fact = FACTS.get(f)
        out.append({"key": f, "label": fact.label if fact else f.replace("_", " ").capitalize(),
                    "type": fact.type if fact else "text", "value": d.args.get(f)})
    return out


async def _out(db: AsyncSession, d: Decision, current: CurrentUser, ctx: Ctx,
               *, detail: bool = False) -> dict[str, Any]:
    agent, tool = AGENTS.get(d.agent_key), TOOLS.get(d.tool_key)
    role = ctx.roles.get(d.approver_role_id) if d.approver_role_id else None
    auth = ctx.inbox.authority(d) if d.kind == "approval" else Authority(False, NOT_WAITING)
    step = d.current_step if d.kind == "approval" else None
    pending = d.kind == "approval" and d.status == "pending"
    out: dict[str, Any] = {
        "id": str(d.id), "kind": d.kind, "status": d.status, "title": d.title,
        "agent_key": d.agent_key,
        "agent_name": agent.name if agent else AGENT_NAMES.get(d.agent_key, d.agent_key),
        "tool_key": d.tool_key,
        "tool_title": tool.title if tool else TITLES.get(d.tool_key, d.tool_key),
        "recommendation": d.recommendation, "recommended_option": d.recommended_option,
        "reasons": d.reasons, "args": d.args,
        "facts": card._facts(d.facts), "checks": d.checks,
        "approver_role": {"id": str(d.approver_role_id), "name": role} if role else None,
        "record_type": d.record_type, "record_id": str(d.record_id) if d.record_id else None,
        "record_name": await record_name(db, d.record_type, d.record_id),
        "run_id": str(d.run_id) if d.run_id else None,
        "created_at": d.created_at, "decided_by": d.decided_by,
        "decided_by_name": ctx.name(d.decided_by), "decided_at": d.decided_at,
        "note": d.note, "result": d.result, "can_decide": auth.ok,
        "as_stand_in_for": ctx.name(auth.on_behalf_of) if auth.on_behalf_of else None,
        "first_shown_at": d.first_shown_at, "decision_ms": d.decision_ms,
        "why_not": _why(auth, d, ctx) if pending else None,
        "requested_by": d.requested_by, "requested_by_name": ctx.name(d.requested_by),
        "counter_of": str(d.counter_of) if d.counter_of else None,
        "steps": [{**s, "decided_by_name": ctx.name(s["decided_by"]),
                   "on_behalf_of_name": ctx.name(s["on_behalf_of"])}
                  for s in step_summary(d, ctx.roles)] if d.kind == "approval" else [],
        "step": d.step_index + 1 if d.kind == "approval" else None,
        "due_at": step.due_at if step and pending else None,
        "past_deadline": bool(step and pending and step.overdue_at),
        "escalated_to_me": pending and not auth.ok and escalated_to(
            d, ctx.inbox.me, ctx.escalation_role_id),
        "counter_fields": _counter_fields(d) if pending and auth.ok else [],
        "card_hash": None, "history": None, "counter": None,
    }
    if d.kind == "approval" and (pending or detail):
        built = await card.build(db, d, ctx.roles)
        out["history"], out["counter"] = built["history"], built["counter"]
        if pending:
            out["card_hash"] = built["hash"]
    return out


async def _waiting_ids(db: AsyncSession, current: CurrentUser, ctx: Ctx) -> list[Decision]:
    """Cards for this person: those they may decide, escalated to them, or asked for."""
    me = ctx.inbox.me
    handed = set(await db.scalars(select(Delegation.decision_id).where(
        Delegation.kind == "card", Delegation.from_user_id == me.user_id,
        Delegation.revoked_at.is_(None))))
    cards = await db.scalars(select(Decision).where(
        Decision.kind == "approval", Decision.status == "pending")
        .order_by(Decision.created_at))
    return [d for d in cards if ctx.inbox.authority(d).ok
            or escalated_to(d, me, ctx.escalation_role_id)
            or d.requested_by == me.actor or d.id in handed]


@router.get("")
async def list_decisions(
    view: Literal["waiting", "notices", "done"] = "waiting",
    scope: Literal["mine", "all"] = "mine", limit: int = Query(50, le=200), offset: int = 0,
    current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """waiting: cards still open (mine: for this person; all: everyone's); notices:
    unread notices; done: everything already decided or read, newest first."""
    ctx = await Ctx.load(db, current)
    if view == "waiting":
        if scope == "mine":
            rows = await _waiting_ids(db, current, ctx)
        else:
            rows = list(await db.scalars(select(Decision).where(
                Decision.kind == "approval", Decision.status == "pending")
                .order_by(Decision.created_at)))
        rows += [d for d in await db.scalars(select(Decision).where(
            Decision.kind.in_(("alert", "task")), Decision.status == "open")
            .order_by(Decision.created_at)) if _sees(d, current)]
        # Mine first, then the most urgent (past deadline, then nearest deadline).
        far = datetime.max.replace(tzinfo=UTC)
        rows.sort(key=lambda d: (not ctx.inbox.authority(d).ok if d.kind == "approval" else 0,
                                 not bool(d.current_step and d.current_step.overdue_at),
                                 (d.current_step.due_at if d.current_step else None) or far))
        rows = rows[offset:offset + limit]
    elif view == "notices":
        rows = list(await db.scalars(select(Decision).where(
            Decision.kind == "notice", Decision.status == "open")
            .order_by(Decision.created_at.desc()).limit(limit).offset(offset)))
    else:
        rows = [d for d in await db.scalars(select(Decision).where(
            Decision.status.not_in(("pending", "open")))
            .order_by(Decision.decided_at.desc().nulls_last()).limit(limit).offset(offset))
            if _sees(d, current)]
    return [await _out(db, d, current, ctx) for d in rows]


@router.get("/count")
async def count_decisions(
    current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """For the menu badge and the inbox header: cards this person may decide (and how
    many are near or past their deadline), alerts, tasks and unread notices."""
    inbox = await _inbox(db, current)
    cfg = await calendar.load(db, current.tenant_id)
    now = datetime.now(UTC)
    mine = near = late = 0
    for d in await db.scalars(select(Decision).where(Decision.kind == "approval",
                                                     Decision.status == "pending")):
        if not inbox.authority(d).ok:
            continue
        mine += 1
        step = d.current_step
        if step and step.overdue_at:
            late += 1
        elif step and step.due_at and step.started_at and (
                step.due_at - now <= (step.due_at - step.started_at) * (
                    100 - cfg["reminder_pct"]) / 100):
            near += 1
    notices = len(list(await db.scalars(select(Decision.id).where(
        Decision.kind == "notice", Decision.status == "open"))))
    alerts = tasks = 0
    for d in await db.scalars(select(Decision).where(Decision.kind.in_(("alert", "task")),
                                                     Decision.status == "open")):
        if _sees(d, current):
            alerts += d.kind == "alert"
            tasks += d.kind == "task"
    return {"waiting": mine + alerts + tasks, "approvals": mine, "near_deadline": near,
            "past_deadline": late, "alerts": alerts, "tasks": tasks, "notices": notices}


@router.get("/me")
async def my_numbers(
    days: int = Query(30, ge=1, le=365),
    current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """The inbox header (H9): my median decision time and override rate."""
    return await analytics.mine(db, _actor(current), days)


@router.get("/analytics")
async def approval_analytics(
    days: int = Query(30, ge=1, le=365),
    current: CurrentUser = Depends(require_permission("decisions.manage")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Per card type and per approver: counts, waits, decision times, overrides and
    missed deadlines (H9, safeguard 8)."""
    return await analytics.report(db, current.tenant_id, days)


# --- the working calendar and deadlines (H4) ----------------------------------------------

class Holiday(BaseModel):
    date: str
    name: str = Field(max_length=100)


class SettingsIn(BaseModel):
    time_zone: str = "Asia/Kolkata"
    work_days: list[int]
    day_start: str
    day_end: str
    holidays: list[Holiday] = []
    default_sla_hours: Decimal
    reminder_pct: int
    escalation_role_id: uuid.UUID | None = None
    bottleneck_overdue: int
    bottleneck_wait_ratio: Decimal


def _settings_out(s: dict[str, Any], roles: dict[uuid.UUID, str]) -> dict[str, Any]:
    return {**s, "default_sla_hours": str(s["default_sla_hours"]),
            "bottleneck_wait_ratio": str(s["bottleneck_wait_ratio"]),
            "escalation_role_id": str(s["escalation_role_id"]) if s["escalation_role_id"]
            else None,
            "escalation_role": roles.get(s["escalation_role_id"]) if s["escalation_role_id"]
            else None, "day_names": list(calendar.DAY_NAMES)}


@router.get("/settings")
async def get_settings_(
    current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    roles = dict((await db.execute(select(Role.id, Role.name))).all())
    return _settings_out(await calendar.load(db, current.tenant_id), roles)


@router.put("/settings")
async def put_settings(
    body: SettingsIn, current: CurrentUser = Depends(require_permission("decisions.manage")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Change the working calendar, default deadline, reminder, escalation and alert
    limits. Applies to steps that start from now on; running deadlines stay as set."""
    data = body.model_dump(mode="json")
    problems = await calendar.problems(db, data)
    if problems:
        raise ValidationFailed(problems)
    data["default_sla_hours"] = body.default_sla_hours
    data["bottleneck_wait_ratio"] = body.bottleneck_wait_ratio
    before, after = await calendar.save(db, current.tenant_id, data, _actor(current))
    keys = ("time_zone", "work_days", "day_start", "day_end", "holidays", "default_sla_hours",
            "reminder_pct", "escalation_role_id", "bottleneck_overdue", "bottleneck_wait_ratio")
    await chain.append(db, current.tenant_id, "inbox.settings.changed", actor=_actor(current),
                       data={"from": {k: before[k] for k in keys},
                             "to": {k: after[k] for k in keys}})
    roles = dict((await db.execute(select(Role.id, Role.name))).all())
    return _settings_out(after, roles)


# --- delegation and out of office (H3, safeguard 3) -------------------------------------

@router.get("/colleagues")
async def colleagues(
    current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, str]]:
    """People a card can be handed to: active, and allowed to decide cards."""
    found = await people(db)
    return sorted(({"id": str(p.user_id), "name": p.name} for p in found.values()
                   if p.user_id != current.user.id
                   and has_permission(p.permissions, "decisions.decide")),
                  key=lambda x: x["name"])


@router.get("/card-types")
async def card_types(
    _: CurrentUser = Depends(require_permission("decisions.decide")),
) -> list[dict[str, Any]]:
    """Card types an out-of-office can cover (actions that change something)."""
    return [{"key": t.key, "title": t.title, "has_amount": has_amount(t.key),
             "agent": next((a.name for a in AGENTS.values() if t.key in a.tools), None)}
            for t in TOOLS.values() if t.side_effect]


async def _names(db: AsyncSession) -> dict[uuid.UUID, str]:
    return dict((await db.execute(select(User.id, User.full_name))).all())


@router.get("/delegations")
async def my_delegations(
    current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = await delegation.mine(db, await _me(db, current))
    names = await _names(db)
    return [delegation.out(r, names) for r in rows]


class AwayIn(BaseModel):
    to_user_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    tool_keys: list[str] = Field(min_length=1)
    max_amount: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    note: str | None = Field(default=None, max_length=300)


@router.post("/away", status_code=201)
async def set_away(
    body: AwayIn, current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Out of office: a stand-in decides my cards of these types between two dates."""
    dl = await delegation.go_away(db, current.tenant_id, await _me(db, current),
                                  body.to_user_id, body.starts_at, body.ends_at,
                                  body.tool_keys, body.max_amount, body.note)
    return delegation.out(dl, await _names(db))


@router.post("/delegations/{delegation_id}/end")
async def end_delegation(
    delegation_id: uuid.UUID,
    current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    dl = await delegation.end(db, await _me(db, current), delegation_id)
    return delegation.out(dl, await _names(db))


# --- push notifications (H5) ---------------------------------------------------------------

class PushKeys(BaseModel):
    p256dh: str = Field(min_length=10, max_length=200)
    auth: str = Field(min_length=8, max_length=100)


class PushIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=1000, pattern=r"^https://")
    keys: PushKeys


@router.get("/push/key")
async def push_key(
    current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """The public key browsers subscribe with, or null while push is off."""
    from app.config import get_settings

    n = len(list(await db.scalars(select(PushSubscription.id).where(
        PushSubscription.user_id == current.user.id))))
    return {"public_key": get_settings().vapid_public_key if push.configured() else None,
            "subscriptions": n}


@router.post("/push/subscribe", status_code=201)
async def push_subscribe(
    body: PushIn, current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if not push.configured():
        raise AppError("Push notifications are not set up on this server yet")
    sub = await db.scalar(select(PushSubscription).where(
        PushSubscription.endpoint == body.endpoint))
    if sub is None:
        sub = PushSubscription(tenant_id=current.tenant_id, user_id=current.user.id,
                               endpoint=body.endpoint, p256dh=body.keys.p256dh,
                               auth=body.keys.auth)
        db.add(sub)
    else:
        sub.user_id, sub.p256dh, sub.auth, sub.failures = (
            current.user.id, body.keys.p256dh, body.keys.auth, 0)
    await db.flush()
    return {"ok": True}


@router.post("/push/unsubscribe", status_code=204)
async def push_unsubscribe(
    body: PushIn, current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> None:
    sub = await db.scalar(select(PushSubscription).where(
        PushSubscription.endpoint == body.endpoint,
        PushSubscription.user_id == current.user.id))
    if sub is not None:
        await db.delete(sub)


@router.post("/push/test")
async def push_test(
    current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    sent = await push.send(db, {current.user.id}, {
        "title": "Forge notifications are on", "body": "New cards will show up here.",
        "url": "/decisions", "tag": "forge-test"})
    return {"sent": sent}


# --- one card --------------------------------------------------------------------------

async def _card(db: AsyncSession, decision_id: uuid.UUID, current: CurrentUser) -> Decision:
    d = await db.get(Decision, decision_id)
    if d is None or not _sees(d, current):
        raise NotFound("Decision not found")
    return d


@router.get("/{decision_id}")
async def get_decision(
    decision_id: uuid.UUID,
    current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    d = await _card(db, decision_id, current)
    out = await _out(db, d, current, await Ctx.load(db, current), detail=True)
    # What the decision led to (F12.3: every decision auditable with its effect).
    nxt = await audit_rows(db, await next_steps.what_happened_next(db, d))
    shown(nxt)
    out["next"] = nxt
    if d.counter_of is None:
        later = await db.scalar(select(Decision.id).where(Decision.counter_of == d.id))
        out["countered_by"] = str(later) if later else None
    return out


@router.post("/{decision_id}/shown", status_code=204)
async def card_shown(
    decision_id: uuid.UUID,
    current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> None:
    """The browser says the card is on screen. The first time someone who can decide
    sees it (the card, and each step) is kept, for the approval analytics."""
    d = await _card(db, decision_id, current)
    if d.status not in ("pending", "open"):
        return
    if d.kind == "approval" and not (await _inbox(db, current)).authority(d).ok:
        return
    now = datetime.now(UTC)
    if d.first_shown_at is None:
        d.first_shown_at = now
    step = d.current_step if d.kind == "approval" else None
    if step is not None and step.first_shown_at is None:
        step.first_shown_at = now


class DecideIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)
    # How long the card was in front of the person before they decided, measured by
    # the browser (F12.3 "under 60 seconds"). At most a day; longer means left open.
    shown_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    # The fingerprint of the card as it was shown (H7); required.
    card_hash: str = Field(min_length=64, max_length=64)


async def _checked(db: AsyncSession, current: CurrentUser, decision_id: uuid.UUID,
                   body: DecideIn) -> tuple[Decision, Authority, dict[str, Any], Ctx]:
    """The card, the person's authority over it, and the card content they saw."""
    d = await db.get(Decision, decision_id, with_for_update=True, populate_existing=True)
    if d is None or d.kind != "approval":
        raise NotFound("Decision not found")
    if d.status != "pending":
        raise AppError(f"This decision is already {d.status}")
    ctx = await Ctx.load(db, current)
    auth = ctx.inbox.authority(d)
    if not auth.ok:
        raise Forbidden(_why(auth, d, ctx) or "You cannot decide this")
    built = await card.build(db, d, ctx.roles)
    if built["hash"] != body.card_hash:
        raise CardChanged(CHANGED)
    return d, auth, built, ctx


async def _decide(db: AsyncSession, current: CurrentUser, decision_id: uuid.UUID,
                  approve: bool, body: DecideIn) -> dict[str, Any]:
    d, auth, built, ctx = await _checked(db, current, decision_id, body)
    d = await runtime.decide(db, decision_id, approve=approve, actor=_actor(current),
                             note=body.note, decision_ms=body.shown_ms,
                             on_behalf_of=auth.on_behalf_of, card=built["content"],
                             card_hash=built["hash"])
    await db.flush()
    return await _out(db, d, current, await Ctx.load(db, current))


@router.post("/{decision_id}/approve")
async def approve(
    decision_id: uuid.UUID, body: DecideIn,
    current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await _decide(db, current, decision_id, True, body)


@router.post("/{decision_id}/reject")
async def reject(
    decision_id: uuid.UUID, body: DecideIn,
    current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await _decide(db, current, decision_id, False, body)


class CounterIn(DecideIn):
    values: dict[str, Any] = Field(min_length=1)


@router.post("/{decision_id}/counter")
async def counter_card(
    decision_id: uuid.UUID, body: CounterIn,
    current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Approve a changed version (H2): the change is checked by the gate first; the
    original card ends as countered, and the changed one continues on its own chain."""
    d, auth, built, ctx = await _checked(db, current, decision_id, body)
    me = ctx.inbox.me
    acting_as, dl = me, None
    if auth.on_behalf_of:
        dl = await db.get(Delegation, auth.delegation_id)
        acting_as = (await people(db, {dl.from_user_id}))[dl.from_user_id]
    try:
        d, outcome = await counter(db, decision_id, me=me, acting_as=acting_as, delegation=dl,
                                   values=body.values, note=body.note,
                                   decision_ms=body.shown_ms, card=built["content"],
                                   card_hash=built["hash"])
    except CounterRefused as exc:
        raise ValidationFailed([exc.message]) from exc
    await db.flush()
    ctx = await Ctx.load(db, current)
    out = await _out(db, d, current, ctx)
    out["counter_result"] = {"status": outcome.status, "summary": outcome.summary,
                             "reasons": outcome.reasons, "error": outcome.error,
                             "decision_id": str(outcome.decision_id)
                             if outcome.decision_id else None}
    if outcome.decision_id:
        out["countered_by"] = await _out(db, await db.get(Decision, outcome.decision_id),
                                         current, ctx)
    return out


class HandIn(BaseModel):
    to_user_id: uuid.UUID
    note: str | None = Field(default=None, max_length=300)


@router.post("/{decision_id}/hand", status_code=201)
async def hand_card(
    decision_id: uuid.UUID, body: HandIn,
    current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Hand this card to a colleague, who decides it for me (H3)."""
    dl = await delegation.hand_card(db, await _me(db, current), decision_id, body.to_user_id,
                                    body.note)
    return delegation.out(dl, await _names(db))


@router.post("/{decision_id}/acknowledge")
async def acknowledge(
    decision_id: uuid.UUID,
    current: CurrentUser = Depends(require_permission("decisions.decide")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    d = await _card(db, decision_id, current)
    if d.kind == "alert" and d.tool_key != INBOX_ALERT:
        d = await acknowledge_alert(db, decision_id, _actor(current))
    elif d.kind == "alert":
        if d.status == "open":
            d.status, d.decided_by, d.decided_at = "acknowledged", _actor(current), \
                datetime.now(UTC)
    else:
        d = await runtime.acknowledge(db, decision_id, _actor(current))
    await db.flush()
    return await _out(db, d, current, await Ctx.load(db, current))

