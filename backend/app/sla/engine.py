"""The timer engine (F1.10). A timer belongs to a record and a stage (an enquiry's
"first look"), has a target in seconds and runs on one of two clocks: "always"
(around the clock, for the 15-minute first response, decision N13 A) or "working"
(the company's working hours and holidays, app/approvals/calendar.py).

A timer can pause (while we wait on the customer) and resume; the time already used
is kept. While it runs it carries three moments: remind (the reminder share of the
target, 75% by default), due (100%) and double (the head of department's share, 200%
by default). The caller decides what each moment means and who hears about it.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals.calendar import Calendar
from app.models import SlaTimer

ALWAYS, WORKING = "always", "working"


def _after(cal: Calendar, clock: str, at: datetime, seconds: float) -> datetime:
    if seconds <= 0:
        return at
    if clock == ALWAYS:
        return at + timedelta(seconds=seconds)
    return cal.add(at, seconds / 3600)


def elapsed(t: SlaTimer, now: datetime, cal: Calendar) -> float:
    """Clock seconds used so far."""
    used = float(t.used_seconds or 0)
    if t.running_since is not None and now > t.running_since:
        used += ((now - t.running_since).total_seconds() if t.clock == ALWAYS
                 else cal.between(t.running_since, now))
    return used


def share(t: SlaTimer, now: datetime, cal: Calendar) -> float:
    """How much of the target is used, as a percentage."""
    return 100.0 * elapsed(t, now, cal) / max(1, t.target_seconds)


def _points(t: SlaTimer, cal: Calendar, pcts: dict[str, int]) -> None:
    start, used = t.running_since, float(t.used_seconds or 0)
    if start is None:
        t.remind_at = t.due_at = t.double_at = None
        return

    def at(pct: float) -> datetime:
        return _after(cal, t.clock, start, t.target_seconds * pct / 100 - used)

    t.remind_at = at(pcts["reminder_pct"])
    t.due_at = at(100)
    t.double_at = at(pcts["hod_pct"])


def moment(t: SlaTimer, pct: float, cal: Calendar) -> datetime | None:
    """When the timer reaches pct% (for a running timer)."""
    if t.running_since is None:
        return None
    return _after(cal, t.clock, t.running_since,
                  t.target_seconds * pct / 100 - float(t.used_seconds or 0))


async def start(session: AsyncSession, tenant_id: uuid.UUID, subject_type: str,
                subject_id: uuid.UUID, stage: str, *, clock: str, target_seconds: int,
                at: datetime, cal: Calendar, pcts: dict[str, int],
                paused: bool = False) -> SlaTimer:
    """Start a stage's timer (a running one for the same stage is returned as is)."""
    existing = await current(session, subject_type, subject_id, stage)
    if existing is not None:
        return existing
    t = SlaTimer(tenant_id=tenant_id, subject_type=subject_type, subject_id=subject_id,
                 stage=stage, clock=clock, target_seconds=max(1, int(target_seconds)),
                 started_at=at, used_seconds=0, running_since=None if paused else at,
                 paused_at=at if paused else None)
    _points(t, cal, pcts)
    session.add(t)
    await session.flush()
    return t


async def current(session: AsyncSession, subject_type: str, subject_id: uuid.UUID,
                  stage: str) -> SlaTimer | None:
    return await session.scalar(select(SlaTimer).where(
        SlaTimer.subject_type == subject_type, SlaTimer.subject_id == subject_id,
        SlaTimer.stage == stage, SlaTimer.stopped_at.is_(None)))


async def of(session: AsyncSession, subject_type: str, subject_id: uuid.UUID
             ) -> list[SlaTimer]:
    return list(await session.scalars(select(SlaTimer).where(
        SlaTimer.subject_type == subject_type, SlaTimer.subject_id == subject_id)
        .order_by(SlaTimer.started_at, SlaTimer.stage)))


def stop(t: SlaTimer, at: datetime, cal: Calendar, outcome: str | None = None) -> str:
    """Stop the timer: met or missed by the clock, or cancelled."""
    used = elapsed(t, at, cal)
    t.used_seconds = int(used)
    t.running_since = None
    t.remind_at = t.due_at = t.double_at = None
    t.stopped_at = at
    t.outcome = outcome or ("met" if used <= t.target_seconds else "missed")
    if t.outcome == "missed" and t.breached_at is None:
        t.breached_at = at
    return t.outcome


def pause(t: SlaTimer, at: datetime, cal: Calendar) -> None:
    if t.running_since is None or t.stopped_at is not None:
        return
    t.used_seconds = int(elapsed(t, at, cal))
    t.running_since = None
    t.paused_at = at
    t.remind_at = t.due_at = t.double_at = None


def resume(t: SlaTimer, at: datetime, cal: Calendar, pcts: dict[str, int]) -> None:
    if t.running_since is not None or t.stopped_at is not None:
        return
    t.running_since, t.paused_at = at, None
    _points(t, cal, pcts)


def retarget(t: SlaTimer, target_seconds: int, clock: str, cal: Calendar,
             pcts: dict[str, int], now: datetime) -> None:
    """New settings apply to running timers from now on."""
    if t.stopped_at is not None:
        return
    if t.running_since is not None:
        t.used_seconds = int(elapsed(t, now, cal))
        t.running_since = now
    t.target_seconds, t.clock = max(1, int(target_seconds)), clock
    _points(t, cal, pcts)


async def due(session: AsyncSession, now: datetime, subject_type: str | None = None
              ) -> list[SlaTimer]:
    """Running timers past their reminder moment (everything else is quiet)."""
    stmt = select(SlaTimer).where(SlaTimer.stopped_at.is_(None),
                                  SlaTimer.running_since.is_not(None),
                                  SlaTimer.remind_at <= now)
    if subject_type:
        stmt = stmt.where(SlaTimer.subject_type == subject_type)
    return list(await session.scalars(stmt.order_by(SlaTimer.remind_at)))


def out(t: SlaTimer, now: datetime, cal: Calendar) -> dict[str, Any]:
    used = elapsed(t, now, cal) if t.stopped_at is None else float(t.used_seconds)
    return {"stage": t.stage, "clock": t.clock, "target_seconds": t.target_seconds,
            "used_seconds": int(used), "pct": round(100 * used / max(1, t.target_seconds)),
            "running": t.running_since is not None, "paused": t.paused_at is not None
            and t.stopped_at is None, "started_at": t.started_at, "due_at": t.due_at,
            "stopped_at": t.stopped_at, "outcome": t.outcome, "breached_at": t.breached_at,
            "reminded_at": t.reminded_at, "escalated_at": t.escalated_at,
            "escalated2_at": t.escalated2_at}


def now_utc() -> datetime:
    return datetime.now(UTC)
