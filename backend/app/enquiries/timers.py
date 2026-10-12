"""An enquiry's three timers (F1.10, decision N13 A).

  first_response  from the email arriving to the first message we send (the
                  acknowledgement counts), around the clock: 15 minutes
  first_look      from being given to an owner to the owner opening it: 2 working hours
  quote           from arriving to "Quote sent": 1 working day, paused while we wait
                  on the customer's answers

At the reminder share (75%) the owner is reminded; at the deadline the owner's
manager ("reports to") is told; at twice the time the head of department (the
manager's manager). Without a manager, the company's escalation role hears, else the
full-access admins. Alerts go by push and email.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals.calendar import Calendar, calendar
from app.enquiries import records
from app.enquiries import settings as settings_mod
from app.models import Enquiry, SlaTimer, User
from app.sla import engine

SUBJECT = "enquiry"
LABELS = {"first_response": "first response", "first_look": "owner's first look",
          "quote": "quote"}


async def _setup(session: AsyncSession, tenant_id: uuid.UUID
                 ) -> tuple[Calendar, dict[str, Any], dict[str, Any]]:
    cal, cfg = await calendar(session, tenant_id)
    timers = (await settings_mod.load(session, tenant_id))["timers"]
    return cal, timers, cfg


def _day_seconds(cal: Calendar) -> float:
    start = datetime.combine(datetime(2000, 1, 3).date(), cal.start)
    end = datetime.combine(datetime(2000, 1, 3).date(), cal.end)
    return max(3600.0, (end - start).total_seconds())


def _stage(timers: dict[str, Any], key: str) -> dict[str, Any]:
    return next(s for s in timers["stages"] if s["key"] == key)


async def start(session: AsyncSession, enq: Enquiry, stage: str, at: datetime, *,
                paused: bool = False) -> SlaTimer:
    cal, timers, _ = await _setup(session, enq.tenant_id)
    clock, seconds = settings_mod.stage_seconds(_stage(timers, stage), _day_seconds(cal))
    return await engine.start(session, enq.tenant_id, SUBJECT, enq.id, stage, clock=clock,
                              target_seconds=seconds, at=at, cal=cal, pcts=timers,
                              paused=paused)


async def stop(session: AsyncSession, enq: Enquiry, stage: str, at: datetime,
               outcome: str | None = None, actor: str = "system:forge") -> str | None:
    t = await engine.current(session, SUBJECT, enq.id, stage)
    if t is None:
        return None
    cal, _, _ = await _setup(session, enq.tenant_id)
    result = engine.stop(t, at, cal, outcome)
    await records.event(session, enq, "timer_stopped", actor, stage=stage, outcome=result,
                        used_seconds=t.used_seconds, target_seconds=t.target_seconds)
    return result


async def stop_all(session: AsyncSession, enq: Enquiry, at: datetime, actor: str) -> None:
    for t in await engine.of(session, SUBJECT, enq.id):
        if t.stopped_at is None:
            await stop(session, enq, t.stage, at, "cancelled", actor)


async def pause(session: AsyncSession, enq: Enquiry, stage: str, at: datetime) -> None:
    t = await engine.current(session, SUBJECT, enq.id, stage)
    if t is not None:
        cal, _, _ = await _setup(session, enq.tenant_id)
        engine.pause(t, at, cal)


async def resume(session: AsyncSession, enq: Enquiry, stage: str, at: datetime) -> None:
    t = await engine.current(session, SUBJECT, enq.id, stage)
    if t is not None:
        cal, timers, _ = await _setup(session, enq.tenant_id)
        engine.resume(t, at, cal, timers)


async def restart_first_look(session: AsyncSession, enq: Enquiry, at: datetime) -> None:
    """A new owner gets a fresh first look; the old one's is cancelled."""
    await stop(session, enq, "first_look", at, "cancelled")
    if enq.owner_id:
        await start(session, enq, "first_look", at)


async def apply_settings(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    """New targets apply to the timers still running, from now."""
    from sqlalchemy import select

    cal, timers, _ = await _setup(session, tenant_id)
    now = datetime.now(UTC)
    n = 0
    for t in await session.scalars(select(SlaTimer).where(
            SlaTimer.subject_type == SUBJECT, SlaTimer.stopped_at.is_(None))):
        clock, seconds = settings_mod.stage_seconds(_stage(timers, t.stage), _day_seconds(cal))
        engine.retarget(t, seconds, clock, cal, timers, now)
        n += 1
    return n


# Who hears --------------------------------------------------------------------------

async def _manager(session: AsyncSession, user_id: uuid.UUID | None) -> User | None:
    if not user_id:
        return None
    u = await session.get(User, user_id)
    if u is None or not u.reports_to_id:
        return None
    m = await session.get(User, u.reports_to_id)
    return m if m is not None and m.is_active else None


async def escalation_people(session: AsyncSession, enq: Enquiry) -> set[uuid.UUID]:
    """The company's escalation role, else the full-access admins."""
    from app.approvals.chain import people
    from app.core.permissions import ALL

    _, cfg = await calendar(session, enq.tenant_id)
    everyone = await people(session)
    role = cfg.get("escalation_role_id")
    if role:
        found = {uid for uid, p in everyone.items() if uuid.UUID(str(role)) in p.roles}
        if found:
            return found
    return {uid for uid, p in everyone.items() if ALL in p.permissions}


async def _hhmm(session: AsyncSession, enq: Enquiry, at: datetime | None) -> str:
    if at is None:
        return ""
    cal, _, _ = await _setup(session, enq.tenant_id)
    local = at.astimezone(cal.tz)
    today = datetime.now(UTC).astimezone(cal.tz).date()
    return f"{local:%H:%M}" if local.date() == today else f"{local:%d %b %H:%M}"


async def tick(session: AsyncSession, tenant_id: uuid.UUID, now: datetime | None = None
               ) -> dict[str, int]:
    from app.enquiries.routing import notify_people

    now = now or datetime.now(UTC)
    cal, timers, _ = await _setup(session, tenant_id)
    counts: dict[str, int] = {}
    for t in await engine.due(session, now, SUBJECT):
        enq = await session.get(Enquiry, t.subject_id)
        if enq is None:
            continue
        pct = engine.share(t, now, cal)
        what = LABELS.get(t.stage, t.stage)
        owner = await session.get(User, enq.owner_id) if enq.owner_id else None
        owner_ids = {owner.id} if owner is not None and owner.is_active else set()
        if t.reminded_at is None and pct >= timers["reminder_pct"]:
            t.reminded_at = now
            due = await _hhmm(session, enq, t.due_at)
            await notify_people(session, enq, owner_ids or await escalation_people(session, enq),
                                f"{enq.ref}: {what} due by {due}",
                                enq.summary or "Open the enquiry")
            counts["reminded"] = counts.get("reminded", 0) + 1
        if t.breached_at is None and pct >= 100:
            t.breached_at = now
            await records.event(session, enq, "timer_missed", "system:forge", stage=t.stage)
            counts["missed"] = counts.get("missed", 0) + 1
        if t.escalated_at is None and pct >= timers["manager_pct"]:
            t.escalated_at = now
            manager = await _manager(session, enq.owner_id)
            to = {manager.id} if manager else await escalation_people(session, enq)
            who = owner.full_name if owner else "nobody yet"
            await notify_people(session, enq, to, f"{enq.ref} missed its {what}",
                                f"Owner: {who}. {enq.summary or ''}".strip())
            await records.event(session, enq, "escalated", "system:forge", stage=t.stage,
                                level="manager", to=sorted(str(u) for u in to))
            counts["escalated"] = counts.get("escalated", 0) + 1
        if t.escalated2_at is None and pct >= timers["hod_pct"]:
            t.escalated2_at = now
            manager = await _manager(session, enq.owner_id)
            hod = await _manager(session, manager.id) if manager else None
            to = {hod.id} if hod else await escalation_people(session, enq)
            await notify_people(session, enq, to,
                                f"{enq.ref} is at twice the time for its {what}",
                                enq.summary or "Open the enquiry")
            await records.event(session, enq, "escalated", "system:forge", stage=t.stage,
                                level="head_of_department", to=sorted(str(u) for u in to))
            counts["escalated_hod"] = counts.get("escalated_hod", 0) + 1
        # The next moment worth a look (the column holds it while the timer runs).
        left = [p for p, done in ((timers["reminder_pct"], t.reminded_at),
                                  (100, t.breached_at), (timers["manager_pct"], t.escalated_at),
                                  (timers["hod_pct"], t.escalated2_at)) if done is None]
        t.remind_at = engine.moment(t, min(left), cal) if left else None
    return counts


def first_response_start(received_at: datetime | None, now: datetime) -> datetime:
    """The first-response clock starts when the email arrived, unless it is old mail
    being read for the first time (then from now)."""
    if received_at is None or received_at > now or now - received_at > timedelta(days=1):
        return now
    return received_at
