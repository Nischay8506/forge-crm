"""Working hours (decision H4): each company's calendar, and deadlines counted in it.

A card's deadline is a number of working hours from when its step started. Hours
outside the working day, on days off and on the company's holidays do not count, so
a 4-hour card raised at 17:30 on a Saturday is due at 12:30 on Monday. The same
calendar is meant for F1.10's enquiry timers later.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApprovalSettings, Role

DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# Decision H4 A: Monday to Saturday, 09:30 to 18:30 IST, 4 working hours, reminder at 75%.
# Decision H9 A: an alert when someone has more than 5 cards past their deadline, or a
# card type's median wait doubles week on week.
DEFAULTS: dict[str, Any] = {
    "time_zone": "Asia/Kolkata", "work_days": [0, 1, 2, 3, 4, 5],
    "day_start": "09:30", "day_end": "18:30", "holidays": [],
    "default_sla_hours": Decimal("4"), "reminder_pct": 75, "escalation_role_id": None,
    "bottleneck_overdue": 5, "bottleneck_wait_ratio": Decimal("2"),
}
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class Calendar:
    tz: ZoneInfo
    work_days: frozenset[int]
    start: time
    end: time
    holidays: frozenset[date]

    def window(self, day: date) -> tuple[datetime, datetime] | None:
        """The working hours of one day, or None on a day off or holiday."""
        if day.weekday() not in self.work_days or day in self.holidays:
            return None
        return (datetime.combine(day, self.start, self.tz),
                datetime.combine(day, self.end, self.tz))

    def add(self, at: datetime, hours: float | Decimal) -> datetime:
        """The moment `hours` working hours after `at`."""
        remaining = timedelta(hours=float(hours))
        local = at.astimezone(self.tz)
        day = local.date()
        for _ in range(1000):  # nearly three years of days: plenty, and never endless
            w = self.window(day)
            if w is not None:
                begin, end = w
                cur = max(local, begin)
                if cur < end:
                    if remaining <= end - cur:
                        return (cur + remaining).astimezone(UTC)
                    remaining -= end - cur
            day += timedelta(days=1)
        raise ValueError("The working calendar has no working hours")

    def between(self, a: datetime, b: datetime) -> float:
        """Working seconds from a to b (0 if b is not after a)."""
        if b <= a:
            return 0.0
        total = 0.0
        day = a.astimezone(self.tz).date()
        last = b.astimezone(self.tz).date()
        while day <= last:
            w = self.window(day)
            if w is not None:
                lo, hi = max(a, w[0]), min(b, w[1])
                if hi > lo:
                    total += (hi - lo).total_seconds()
            day += timedelta(days=1)
        return total


def _hhmm(v: str) -> time:
    h, m = v.split(":")
    return time(int(h), int(m))


def calendar_of(s: dict[str, Any]) -> Calendar:
    return Calendar(tz=ZoneInfo(s["time_zone"]), work_days=frozenset(s["work_days"]),
                    start=_hhmm(s["day_start"]), end=_hhmm(s["day_end"]),
                    holidays=frozenset(date.fromisoformat(h["date"]) for h in s["holidays"]))


def _as_dict(row: ApprovalSettings | None) -> dict[str, Any]:
    if row is None:
        return {**DEFAULTS, "is_default": True, "updated_by": None, "updated_at": None}
    return {"time_zone": row.time_zone, "work_days": list(row.work_days),
            "day_start": row.day_start, "day_end": row.day_end, "holidays": list(row.holidays),
            "default_sla_hours": row.default_sla_hours, "reminder_pct": row.reminder_pct,
            "escalation_role_id": row.escalation_role_id,
            "bottleneck_overdue": row.bottleneck_overdue,
            "bottleneck_wait_ratio": row.bottleneck_wait_ratio, "is_default": False,
            "updated_by": row.updated_by, "updated_at": row.updated_at}


async def load(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    return _as_dict(await session.get(ApprovalSettings, tenant_id))


async def calendar(session: AsyncSession, tenant_id: uuid.UUID) -> tuple[Calendar, dict[str, Any]]:
    s = await load(session, tenant_id)
    return calendar_of(s), s


async def problems(session: AsyncSession, data: dict[str, Any]) -> list[str]:
    """What is wrong with settings submitted from the Deadlines screen."""
    out: list[str] = []
    try:
        ZoneInfo(str(data.get("time_zone", "")))
    except (ZoneInfoNotFoundError, ValueError):
        out.append("Unknown time zone")
    days = data.get("work_days") or []
    if not days or any(not isinstance(d, int) or d < 0 or d > 6 for d in days):
        out.append("Pick at least one working day")
    start, end = str(data.get("day_start", "")), str(data.get("day_end", ""))
    if not _HHMM.match(start) or not _HHMM.match(end):
        out.append("Working hours must look like 09:30")
    elif _hhmm(start) >= _hhmm(end):
        out.append("The working day must end after it starts")
    seen = set()
    for h in data.get("holidays") or []:
        try:
            d = date.fromisoformat(str(h.get("date")))
        except ValueError:
            out.append(f"'{h.get('date')}' is not a date")
            continue
        if d in seen:
            out.append(f"{d.isoformat()} is listed twice")
        seen.add(d)
        if not str(h.get("name", "")).strip():
            out.append(f"Give the holiday on {d.isoformat()} a name")
    if len(seen) > 200:
        out.append("At most 200 holidays")
    sla = data.get("default_sla_hours")
    if sla is None or not (Decimal("0.25") <= Decimal(str(sla)) <= Decimal("240")):
        out.append("The default deadline must be between 0.25 and 240 working hours")
    pct = data.get("reminder_pct")
    if not isinstance(pct, int) or not 10 <= pct <= 95:
        out.append("The reminder must come between 10% and 95% of the time")
    if data.get("escalation_role_id") and await session.get(
            Role, uuid.UUID(str(data["escalation_role_id"]))) is None:
        out.append("That escalation role does not exist")
    n = data.get("bottleneck_overdue")
    if not isinstance(n, int) or not 1 <= n <= 100:
        out.append("The bottleneck alert needs between 1 and 100 cards past deadline")
    ratio = data.get("bottleneck_wait_ratio")
    if ratio is None or not (Decimal("1.2") <= Decimal(str(ratio)) <= Decimal("10")):
        out.append("The waiting-time alert ratio must be between 1.2 and 10")
    return out


async def save(session: AsyncSession, tenant_id: uuid.UUID, data: dict[str, Any],
               actor: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Store the settings; returns (before, after)."""
    row = await session.get(ApprovalSettings, tenant_id)
    before = _as_dict(row)
    if row is None:
        row = ApprovalSettings(tenant_id=tenant_id, updated_by=actor)
        session.add(row)
    for k in ("time_zone", "work_days", "day_start", "day_end", "default_sla_hours",
              "reminder_pct", "bottleneck_overdue", "bottleneck_wait_ratio"):
        setattr(row, k, data[k])
    row.work_days = sorted(set(data["work_days"]))
    row.holidays = sorted(({"date": str(h["date"]), "name": str(h["name"]).strip()[:100]}
                           for h in data.get("holidays") or []), key=lambda h: h["date"])
    row.escalation_role_id = (uuid.UUID(str(data["escalation_role_id"]))
                              if data.get("escalation_role_id") else None)
    row.updated_by, row.updated_at = actor, datetime.now(UTC)
    await session.flush()
    return before, _as_dict(row)


def hours_label(h: float | Decimal | None) -> str:
    if h is None:
        return ""
    n = Decimal(str(h)).normalize()
    word = "working hour" if n == 1 else "working hours"
    return f"{n:f} {word}"
