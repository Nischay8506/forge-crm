"""A company's enquiry settings: routing (F1.9, N12 A), timers (F1.10, N13 A) and
intake (F1.1, F1.8, N5 A, N7 A). Without a row, these defaults apply."""

import copy
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EnquirySettings, Role, User

ROUTING_STEPS = ("account_owner", "product_line", "region", "round_robin")
STEP_LABELS = {
    "account_owner": "The account's owner",
    "product_line": "Someone who looks after the product family",
    "region": "Someone who looks after the customer's state or city",
    "round_robin": "Next in the team, by fewest open enquiries",
}
STAGES = ("first_response", "first_look", "quote")
UNITS = {"minutes": ("always", 60), "hours": ("always", 3600),
         "working_hours": ("working", 3600), "working_days": ("working", None)}

DEFAULTS: dict[str, dict[str, Any]] = {
    # N12 A: owner, product line, region, round-robin; the dealer step waits (Q12).
    "routing": {"steps": list(ROUTING_STEPS), "max_open": 25, "fallback_user_id": None},
    # N13 A: first response 15 minutes (the acknowledgement counts, around the clock),
    # the owner's first look within 2 working hours, the quote within 1 working day.
    # Owner reminded at 75%, manager at the deadline, head of department at twice it.
    "timers": {
        "stages": [
            {"key": "first_response", "label": "First response", "target": 15,
             "unit": "minutes"},
            {"key": "first_look", "label": "Owner's first look", "target": 2,
             "unit": "working_hours"},
            {"key": "quote", "label": "Quote sent", "target": 1, "unit": "working_days"},
        ],
        "reminder_pct": 75, "manager_pct": 100, "hod_pct": 200,
    },
    # N7 A: acknowledge automatically (strategic accounts and tenders are drafted for a
    # person); F1.8: nudge on day 2 and day 5, then park. N5 A: complaints notify a role.
    "intake": {"auto_acknowledge": True, "nudge_days": [2, 5], "park_after_days": 7,
               "complaints_role_id": None, "unsure_below": 0.7},
}


def _merge(base: dict[str, Any], row: dict[str, Any] | None) -> dict[str, Any]:
    out = copy.deepcopy(base)
    out.update(row or {})
    return out


async def load(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    row = await session.get(EnquirySettings, tenant_id)
    return {
        "routing": _merge(DEFAULTS["routing"], row.routing if row else None),
        "timers": _merge(DEFAULTS["timers"], row.timers if row else None),
        "intake": _merge(DEFAULTS["intake"], row.intake if row else None),
        "is_default": row is None,
        "updated_by": row.updated_by if row else None,
        "updated_at": row.updated_at if row else None,
    }


def stage_seconds(stage: dict[str, Any], day_seconds: float) -> tuple[str, int]:
    clock, per = UNITS[stage["unit"]]
    return clock, int(float(stage["target"]) * (per if per else day_seconds))


async def problems(session: AsyncSession, part: str, data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if part == "routing":
        steps = data.get("steps") or []
        if any(s not in ROUTING_STEPS for s in steps) or len(set(steps)) != len(steps):
            out.append("Unknown or repeated routing step")
        mo = data.get("max_open")
        if not isinstance(mo, int) or not 1 <= mo <= 500:
            out.append("The most open enquiries per person must be between 1 and 500")
        fb = data.get("fallback_user_id")
        if fb:
            u = await session.get(User, uuid.UUID(str(fb)))
            if u is None or not u.is_active:
                out.append("The fallback person is not an active user")
    elif part == "timers":
        stages = data.get("stages") or []
        if [s.get("key") for s in stages] != list(STAGES):
            out.append("The three stages must all be there, in order")
        for s in stages:
            if s.get("unit") not in UNITS:
                out.append(f"Unknown unit for {s.get('label')}")
                continue
            try:
                t = float(s.get("target"))
            except (TypeError, ValueError):
                t = 0
            if not 0 < t <= 1000:
                out.append(f"Give {s.get('label') or s.get('key')} a target above 0")
            if not str(s.get("label") or "").strip():
                out.append("Every stage needs a name")
        rp, mp, hp = data.get("reminder_pct"), data.get("manager_pct"), data.get("hod_pct")
        if not all(isinstance(x, int) for x in (rp, mp, hp)) or not (
                10 <= rp < 100 <= mp <= hp <= 1000):
            out.append("The reminder comes before the deadline, and the manager and head of "
                       "department at or after it, in that order (for example 75%, 100%, "
                       "200%)")
    elif part == "intake":
        nd = data.get("nudge_days") or []
        if not isinstance(nd, list) or len(nd) > 3 or any(
                not isinstance(d, int) or d < 1 or d > 30 for d in nd) or nd != sorted(set(nd)):
            out.append("Nudges are up to three different days, from 1 to 30, in order")
        pd = data.get("park_after_days")
        if not isinstance(pd, int) or pd < 1 or pd > 60 or (nd and pd <= nd[-1]):
            out.append("Parking must come after the last nudge, within 60 days")
        role = data.get("complaints_role_id")
        if role and await session.get(Role, uuid.UUID(str(role))) is None:
            out.append("That role does not exist")
        ub = data.get("unsure_below")
        if not isinstance(ub, int | float) or not 0.3 <= float(ub) <= 0.95:
            out.append("The 'unsure' level must be between 0.3 and 0.95")
    else:
        out.append("Unknown settings part")
    return out


async def save(session: AsyncSession, tenant_id: uuid.UUID, part: str, data: dict[str, Any],
               actor: str) -> tuple[dict[str, Any], dict[str, Any]]:
    row = await session.get(EnquirySettings, tenant_id)
    before = await load(session, tenant_id)
    if row is None:
        row = EnquirySettings(tenant_id=tenant_id, routing={}, timers={}, intake={},
                              updated_by=actor)
        session.add(row)
    clean = {k: data[k] for k in DEFAULTS[part] if k in data}
    setattr(row, part, _merge(DEFAULTS[part], clean))
    row.updated_by, row.updated_at = actor, datetime.now(UTC)
    await session.flush()
    return before, await load(session, tenant_id)
