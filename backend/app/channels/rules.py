"""Each company's sending rules (decisions L8 A, L11 A).

Per contact, across channels: promotional and follow-up messages at most 1 a day
(calendar day in the company's time zone) and 3 in any 7 days; transactional
messages are counted but not limited. Quiet hours 21:00-09:00 for everything except
a reply to a message the customer sent in the last 24 hours. Promotional messages
also wait on Sundays and the company's holidays (the holidays and time zone are the
approval inbox's working calendar, Deadlines screen). A held message goes at the
next allowed time unless a person sends it anyway.

Email stays in the simulator (the Test inbox) until someone with "channels.manage"
switches it to live, which needs a working email account.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals.calendar import load as load_calendar
from app.models import ChannelSettings

DEFAULTS: dict[str, Any] = {
    "promo_per_day": 1, "promo_per_week": 3, "quiet_start": "21:00", "quiet_end": "09:00",
    "promo_rest_days": [6], "promo_skip_holidays": True, "reply_window_hours": 24,
    "email_mode": "simulator",
    # Language reviews (L11): every AI-written message until this many per language
    # have been reviewed, then one in `review_every`.
    "review_first": 10, "review_every": 20,
    # Approval cards also go by email to the people who decide them (H5).
    "staff_email": True,
}
FIELDS = tuple(k for k in DEFAULTS if k != "email_mode")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _hhmm(v: str) -> time:
    h, m = v.split(":")
    return time(int(h), int(m))


@dataclass(frozen=True)
class Rules:
    tz: ZoneInfo
    quiet_start: time
    quiet_end: time
    rest_days: frozenset[int]
    holidays: frozenset[date]
    per_day: int
    per_week: int
    reply_window: timedelta

    @property
    def has_quiet(self) -> bool:
        return self.quiet_start != self.quiet_end

    def in_quiet(self, at: datetime) -> bool:
        if not self.has_quiet:
            return False
        t = at.astimezone(self.tz).time()
        if self.quiet_start > self.quiet_end:  # crosses midnight, e.g. 21:00-09:00
            return t >= self.quiet_start or t < self.quiet_end
        return self.quiet_start <= t < self.quiet_end

    def quiet_ends(self, at: datetime) -> datetime:
        """When the quiet hours `at` falls in end."""
        local = at.astimezone(self.tz)
        day = local.date()
        if self.quiet_start > self.quiet_end and local.time() >= self.quiet_start:
            day += timedelta(days=1)
        return datetime.combine(day, self.quiet_end, self.tz)

    def promo_day(self, d: date) -> bool:
        return d.weekday() not in self.rest_days and d not in self.holidays

    def next_day_start(self, at: datetime) -> datetime:
        """The start of the next calendar day's sending (after the quiet hours)."""
        day = at.astimezone(self.tz).date() + timedelta(days=1)
        return datetime.combine(day, time(0), self.tz)

    def next_allowed(self, at: datetime, *, promotional: bool, quiet: bool = True
                     ) -> tuple[datetime, str | None]:
        """The first moment at or after `at` this message may go, and why it waits
        ("quiet_hours" or "rest_day"), or (at, None)."""
        why = None
        for _ in range(400):
            if quiet and self.in_quiet(at):
                at, why = self.quiet_ends(at), why or "quiet_hours"
                continue
            if promotional and not self.promo_day(at.astimezone(self.tz).date()):
                at, why = self.next_day_start(at), why or "rest_day"
                continue
            return at.astimezone(UTC), why
        raise ValueError("No day is allowed for promotional messages")

    def day_start(self, at: datetime) -> datetime:
        return datetime.combine(at.astimezone(self.tz).date(), time(0), self.tz)


def _as_dict(row: ChannelSettings | None) -> dict[str, Any]:
    if row is None:
        return {**DEFAULTS, "is_default": True, "updated_by": None, "updated_at": None}
    return {**{k: getattr(row, k) for k in DEFAULTS}, "is_default": False,
            "updated_by": row.updated_by, "updated_at": row.updated_at}


async def load(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    return _as_dict(await session.get(ChannelSettings, tenant_id))


async def rules(session: AsyncSession, tenant_id: uuid.UUID) -> tuple[Rules, dict[str, Any]]:
    s = await load(session, tenant_id)
    cal = await load_calendar(session, tenant_id)
    holidays = frozenset(date.fromisoformat(h["date"]) for h in cal["holidays"]) \
        if s["promo_skip_holidays"] else frozenset()
    return Rules(tz=ZoneInfo(cal["time_zone"]), quiet_start=_hhmm(s["quiet_start"]),
                 quiet_end=_hhmm(s["quiet_end"]), rest_days=frozenset(s["promo_rest_days"]),
                 holidays=holidays, per_day=s["promo_per_day"], per_week=s["promo_per_week"],
                 reply_window=timedelta(hours=s["reply_window_hours"])), s


def problems(data: dict[str, Any]) -> list[str]:
    """What is wrong with settings sent from the Channel settings screen."""
    out: list[str] = []
    for k in ("quiet_start", "quiet_end"):
        if not _HHMM.match(str(data.get(k, ""))):
            out.append(f"{k.replace('_', ' ').capitalize()} must look like 21:00")
    for k, lo, hi in (("promo_per_day", 0, 20), ("promo_per_week", 0, 50),
                      ("reply_window_hours", 1, 168), ("review_first", 0, 1000),
                      ("review_every", 1, 1000)):
        v = data.get(k)
        if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
            out.append(f"{k.replace('_', ' ').capitalize()} must be a whole number from "
                       f"{lo} to {hi}")
    days = data.get("promo_rest_days")
    if not isinstance(days, list) or any(not isinstance(d, int) or not 0 <= d <= 6
                                         for d in days) or len(set(days or [])) > 6:
        out.append("Rest days must be days of the week, and at least one day must stay open")
    if not out and data["promo_per_week"] < data["promo_per_day"]:
        out.append("The weekly limit cannot be lower than the daily limit")
    return out


async def save(session: AsyncSession, tenant_id: uuid.UUID, data: dict[str, Any],
               actor: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Store the settings (not the mode: see set_mode). Returns (before, after)."""
    before = await load(session, tenant_id)
    row = await session.get(ChannelSettings, tenant_id)
    if row is None:
        row = ChannelSettings(tenant_id=tenant_id, email_mode=before["email_mode"],
                              updated_by=actor)
        session.add(row)
    for k in FIELDS:
        setattr(row, k, sorted(set(data[k])) if k == "promo_rest_days" else data[k])
    row.updated_by, row.updated_at = actor, datetime.now(UTC)
    await session.flush()
    return before, _as_dict(row)


async def set_mode(session: AsyncSession, tenant_id: uuid.UUID, mode: str, actor: str) -> None:
    row = await session.get(ChannelSettings, tenant_id)
    if row is None:
        row = ChannelSettings(tenant_id=tenant_id, **{k: DEFAULTS[k] for k in FIELDS},
                              email_mode=mode, updated_by=actor)
        session.add(row)
    row.email_mode, row.updated_by, row.updated_at = mode, actor, datetime.now(UTC)
    await session.flush()


def describe_wait(why: str, at: datetime, tz: ZoneInfo) -> str:
    """A sentence for a held message, e.g. "Quiet hours: goes at 09:00 on Sat 11 Oct"."""
    local = at.astimezone(tz)
    when = f"{local:%H:%M} on {local:%a} {local.day} {local:%b}"
    return {
        "quiet_hours": f"Quiet hours: goes at {when}",
        "rest_day": f"Promotional messages wait on Sundays and holidays: goes at {when}",
        "day_cap": f"Already had today's promotional message: goes at {when}",
        "week_cap": f"Already had this week's promotional messages: goes at {when}",
        "account": f"The email account is not working: tries again at {when}",
        "retry": f"The mail server could not be reached: tries again at {when}",
    }.get(why, f"Goes at {when}")
