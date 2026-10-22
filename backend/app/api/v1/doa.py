"""Who has to approve a discount, and in what order (F3.4; Z1-Z10 A).

A company writes its delegation of authority as a matrix (app/cpq/doa.py) and this is the
screen behind it: the version in force today, every version before it, and what a quote's
own numbers would need. A version is never edited (Z4 A); saving writes a new one from a
date, and the audit chain records who wrote it.

Roles are named by id here, not by name, so renaming a role later cannot quietly change
who approves. The setup sample names roles in words instead, and the screen turns each
name into one of this company's role ids.
"""

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_permission
from app.core.errors import NotFound, ValidationFailed
from app.cpq import doa, doa_agent, quotes
from app.db import get_db
from app.models import Quote, Role, UserRole

router = APIRouter(prefix="/doa", tags=["approvals"])

VIEW = require_permission("quotes.read")
WORK = require_permission("quotes.work")
MANAGE = require_permission("approvals.manage")


def _actor(u: CurrentUser) -> str:
    return f"user:{u.user.id}"


class RowIn(BaseModel):
    when: dict[str, Any] = Field(default_factory=dict)
    approvers: list[str] = Field(default_factory=list)
    separate_stages: bool = False
    note: str | None = Field(default=None, max_length=300)


class MatrixIn(BaseModel):
    rows: list[RowIn] = Field(default_factory=list)
    effective_from: str
    note: str | None = Field(default=None, max_length=300)


class TryIn(BaseModel):
    discount_pct: str = "0"
    value: str = "0"
    margin_pct: str | None = None
    floor_margin_pct: str | None = None
    at: date | None = None


def _version_out(r: Any) -> dict[str, Any]:
    return {"id": str(r.id), "effective_from": r.effective_from, "matrix": r.matrix,
            "note": r.note, "created_by": r.created_by, "created_at": r.created_at}


async def _roles(db: AsyncSession) -> dict[str, dict[str, Any]]:
    """Every role of this company, with how many people are in it (Z2 A)."""
    counts = dict((await db.execute(
        select(UserRole.role_id, func.count()).group_by(UserRole.role_id))).all())
    rows = list(await db.scalars(select(Role).order_by(Role.name)))
    return {str(r.id): {"id": str(r.id), "name": r.name, "people": counts.get(r.id, 0)}
            for r in rows}


def _label(data: dict[str, Any], roles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The stored matrix with each role id's name beside it, for reading."""
    out = {"rows": []}
    for r in data.get("rows") or []:
        named = [roles.get(a, {"id": a, "name": "a role that no longer exists",
                               "people": 0}) for a in r.get("approvers") or []]
        out["rows"].append({**r, "approver_roles": named})
    return out


@router.get("/matrix")
async def matrix(at: date | None = Query(None), current: CurrentUser = Depends(VIEW),
                 db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """The rulebook a quote on `at` is judged by, and every version there has been."""
    day = at or await quotes.today(db, current.tenant_id)
    roles = await _roles(db)
    row = await doa.in_force(db, current.tenant_id, day)
    return {
        "at": day,
        "in_force": ({**_version_out(row), "matrix": _label(row.matrix, roles)}
                     if row else None),
        "versions": [_version_out(v) for v in await doa.versions(db, current.tenant_id)],
        "roles": list(roles.values()),
        "sample": doa.SAMPLE_MATRIX,
        "clock": {"sla_hours": str(doa.SLA_HOURS), "reminder_at": str(doa.REMINDER_AT),
                  "target_median_hours": str(doa.TARGET_MEDIAN_HOURS)},
    }


@router.put("/matrix")
async def save_matrix(body: MatrixIn, current: CurrentUser = Depends(MANAGE),
                      db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """A new dated version. Nothing is edited: a quote approved last month can still be
    explained with last month's rulebook."""
    try:
        day = date.fromisoformat(body.effective_from)
    except ValueError:
        raise ValidationFailed(["effective from: a date like 2026-10-21 is needed"]) from None

    roles = await _roles(db)
    problems: list[str] = []
    empty: list[str] = []
    for i, r in enumerate(body.rows, start=1):
        for a in r.approvers:
            if a not in roles:
                problems.append(f"row {i}: {a} is not a role of this company")
            elif roles[a]["people"] == 0 and roles[a]["name"] not in empty:
                empty.append(roles[a]["name"])
    if problems:
        raise ValidationFailed(problems)

    data = {"rows": [r.model_dump(exclude_none=True) for r in body.rows]}
    row = await doa.save(db, current.tenant_id, data, _actor(current),
                         effective_from=day, note=body.note)
    # Not a refusal: a role can be filled after the rulebook is written. It is said out
    # loud because a stage nobody can decide would stall every quote that reaches it.
    return {**_version_out(row),
            "matrix": _label(row.matrix, roles),
            "warnings": [f"nobody is in {n} yet, so a quote that needs it would wait"
                         for n in empty]}


def _dec(v: str | None, word: str) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise ValidationFailed([f"{word}: a number is needed"]) from None


@router.post("/matrix/try")
async def try_matrix(body: TryIn, current: CurrentUser = Depends(VIEW),
                     db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """What a quote with these numbers would need, against the matrix of its day.

    The setup screen's way of checking a rulebook before a real quote meets it.
    """
    day = body.at or await quotes.today(db, current.tenant_id)
    f = doa.Facts(
        discount_pct=_dec(body.discount_pct, "discount") or Decimal(0),
        value=_dec(body.value, "quote worth") or Decimal(0),
        margin_pct=_dec(body.margin_pct, "margin"),
        floor_margin_pct=_dec(body.floor_margin_pct, "floor"),
    )
    chain = await doa.quote_chain(db, current.tenant_id, day, f)
    roles = await _roles(db)
    out = chain.out()
    for s in out["stages"]:
        s["role_name"] = roles.get(s["role"], {}).get("name", "a role that no longer exists")
    return {"at": day, "needed": chain.needed, "below_floor": f.below_floor, **out}


# One quote's discount ----------------------------------------------------------------------

class AskIn(BaseModel):
    """What the rep says, in their own words (Z7 A). Both are shown to the approver as the
    rep's claim: Forge does not check either of them."""

    why: str | None = Field(default=None, max_length=1000)
    competitor: str | None = Field(default=None, max_length=300)
    revision: int | None = None


async def _quote(db: AsyncSession, quote_id: uuid.UUID) -> Quote:
    q = await db.get(Quote, quote_id)
    if q is None:
        raise NotFound("Quote not found")
    return q


async def _state(db: AsyncSession, q: Quote, revision: int | None,
                 roles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rev = await quotes.revision(db, q, revision)
    got = await doa_agent.look(db, q, rev)
    chain = got["chain"].out()
    for s in chain["stages"]:
        r = roles.get(s["role"])
        s["role_name"] = r["name"] if r else "a role that no longer exists"
        s["people"] = r["people"] if r else 0
    card = got["card"]
    return {
        "quote_id": str(q.id), "number": q.number, "revision": rev.number,
        "at": got["at"], "fingerprint": got["fingerprint"],
        "matrix_id": got["matrix_id"], "below_floor": got["below_floor"],
        "needed": got["chain"].needed or bool(got["chain"].problems),
        "settled": got["settled"], **got["facts"], **chain,
        "no_matrix": any(p.startswith(doa.NO_MATRIX) for p in chain["problems"]),
        "recommendation": doa_agent.recommendation(got),
        "card": None if card is None else {
            "id": str(card.id), "status": card.status,
            "step": card.step_index + 1, "of": len(card.steps),
            "requested_by": card.requested_by, "created_at": card.created_at,
            "decided_by": card.decided_by, "decided_at": card.decided_at,
            "note": card.note, "why": card.args.get("why"),
            "competitor": card.args.get("competitor"),
            "allowed_pct": card.args.get("discount_pct_allowed"),
            # A card raised against numbers the quote no longer has is not its approval.
            "stale": card.args.get("fingerprint") != got["fingerprint"],
        },
    }


@router.get("/quote/{quote_id}")
async def quote_discount(quote_id: uuid.UUID, revision: int | None = Query(None),
                         current: CurrentUser = Depends(VIEW),
                         db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """This quote's discount, and what the rulebook asks for before it can go out.

    Nothing is raised or changed by looking: the rep can see who will have to sign a
    discount off before they ask anyone.
    """
    return await _state(db, await _quote(db, quote_id), revision, await _roles(db))


@router.post("/quote/{quote_id}/approval")
async def ask_discount_approval(quote_id: uuid.UUID, body: AskIn,
                                current: CurrentUser = Depends(WORK),
                                db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Send this quote's discount for approval (Z5 A: one card for the quote).

    Asking twice for the same quote does not raise a second card; asking again after the
    quote changed supersedes the old card, because an approver should never be deciding
    about numbers the quote has moved on from.
    """
    q = await _quote(db, quote_id)
    rev = await quotes.revision(db, q, body.revision)
    quotes.must_be_draft(q, rev)
    await doa_agent.ask_approval(db, q, rev, _actor(current),
                                 why=body.why, competitor=body.competitor)
    return await _state(db, q, rev.number, await _roles(db))
