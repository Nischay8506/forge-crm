"""Material and machine-hour rates with the date they apply from (F3.2, decision C3 A).

A rate is never edited: a change is a new row from a date, so the rate on any day can be
shown later, and a quote's cost names the rate and date it used. On the same date the
later row wins (a correction). Machine-hour rates come from ERPNext's workstations where
the company keeps them there, else from Forge's own table.

A material rate can be typed in any unit Forge knows (₹ per tonne, per kg, per metre);
it is kept as typed and as a rate per the canonical unit (K12 A), with the material's
yield % and scrap rate (K4 A). ERPNext's valuation and last purchase rates are shown next
to it for reference, never used on their own (K5 A).
"""

import re
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import chain
from app.core.errors import ValidationFailed
from app.cpq import units
from app.models import ErpItem, ErpWorkstation, MachineRate, MaterialRate

CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/+-]{0,39}$")
# Material rates are per one of these canonical units, typed in any unit that converts.
MATERIAL_UNITS = ("kg", "m", "m2", "m3", "nos")
MAX_RATE = Decimal("100000000")


@dataclass(frozen=True)
class Rate:
    """One rate as a cost uses it: what, how much, from when, and where it came from."""

    kind: str  # material | machine
    code: str
    name: str
    rate: Decimal
    unit: str
    effective_from: date | None
    source: str  # forge | erpnext
    ref: str  # the row id, for the cost's record
    yield_pct: Decimal | None = None
    scrap_rate: Decimal | None = None

    def out(self) -> dict[str, Any]:
        out = {"kind": self.kind, "code": self.code, "name": self.name,
               "rate": format(self.rate.normalize(), "f"), "unit": self.unit,
               "from": self.effective_from.isoformat() if self.effective_from else None,
               "source": self.source, "ref": self.ref}
        if self.yield_pct is not None:
            out["yield_pct"] = format(self.yield_pct.normalize(), "f")
        if self.scrap_rate is not None:
            out["scrap_rate"] = format(self.scrap_rate.normalize(), "f")
        return out


class RateBook:
    """The rates in force on one day, for working out costs (pure lookups)."""

    def __init__(self, at: date, materials: dict[str, Rate], machines: dict[str, Rate]):
        self.at, self.materials, self.machines = at, materials, machines

    def material(self, code: str) -> Rate | None:
        return self.materials.get(code.strip().upper())

    def machine(self, code: str) -> Rate | None:
        return self.machines.get(code.strip().upper())

    @staticmethod
    async def load(session: AsyncSession, at: date) -> "RateBook":
        mats: dict[str, Rate] = {}
        for r in await session.scalars(select(MaterialRate).where(
                MaterialRate.effective_from <= at)
                .order_by(MaterialRate.effective_from, MaterialRate.created_at)):
            mats[r.code.upper()] = Rate("material", r.code, r.name, r.rate, r.unit,
                                        r.effective_from, "forge", str(r.id), r.yield_pct,
                                        r.scrap_rate)
        machines: dict[str, Rate] = {}
        for m in await session.scalars(select(MachineRate).where(
                MachineRate.effective_from <= at)
                .order_by(MachineRate.effective_from, MachineRate.created_at)):
            machines[m.code.upper()] = Rate("machine", m.code, m.name, m.hour_rate, "hour",
                                            m.effective_from, "forge", str(m.id))
        # ERPNext's workstations win where the company keeps them there.
        for w in await session.scalars(select(ErpWorkstation).where(
                ErpWorkstation.gone_at.is_(None), ErpWorkstation.disabled.is_(False))):
            machines[w.code.upper()] = Rate("machine", w.code, w.name, w.hour_rate, "hour",
                                            None, "erpnext", str(w.id))
        return RateBook(at, mats, machines)


def _dec(v: Any) -> Decimal | None:
    try:
        d = Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _check(data: dict[str, Any], what: str, today: date, rate_key: str) -> list[str]:
    problems = []
    code = str(data.get("code") or "").strip()
    if not CODE.match(code):
        problems.append(f"The {what} code must be 1 to 40 letters, digits, spaces or . _ / + -")
    if not str(data.get("name") or "").strip():
        problems.append(f"Give the {what} a name")
    rate = _dec(data.get(rate_key))
    if rate is None or rate < 0 or rate >= MAX_RATE:
        problems.append("The rate must be a number from 0 up")
    try:
        frm = date.fromisoformat(str(data.get("effective_from")))
    except ValueError:
        problems.append("Say from which date the rate applies")
    else:
        if frm > today + timedelta(days=366):
            problems.append("The date is more than a year ahead")
    return problems


async def add_material(session: AsyncSession, tenant_id: uuid.UUID, data: dict[str, Any],
                       actor: str, today: date) -> MaterialRate:
    problems = _check(data, "material", today, "rate")
    typed = str(data.get("unit") or "kg").strip()
    u = units.parse(typed)
    if u is None or u.canonical not in MATERIAL_UNITS:
        problems.append("The unit must be one Forge can convert: a weight (kg, tonne, g), a "
                        "length (m, mm, ft), an area (m2, sq ft), a volume (m3, litre) or "
                        "nos")
    yield_pct = _dec(data.get("yield_pct")) if data.get("yield_pct") not in (None, "") \
        else None
    if data.get("yield_pct") not in (None, "") and (
            yield_pct is None or not 0 < yield_pct <= 100):
        problems.append("The yield must be a % above 0 and up to 100")
    scrap_rate = _dec(data.get("scrap_rate")) if data.get("scrap_rate") not in (None, "") \
        else None
    if data.get("scrap_rate") not in (None, "") and (
            scrap_rate is None or scrap_rate < 0 or scrap_rate >= MAX_RATE):
        problems.append("The scrap rate must be a number from 0 up")
    if problems:
        raise ValidationFailed(problems)
    assert u is not None
    typed_rate = Decimal(str(data["rate"]).strip())
    rate, canon = units.rate_to_canonical(typed_rate, typed)
    scrap = units.rate_to_canonical(scrap_rate, typed)[0] if scrap_rate is not None else None
    if scrap is not None and scrap > rate:
        raise ValidationFailed(["The scrap rate can't be above the material's own rate"])
    same = u.factor == 1 and typed.lower() == canon
    row = MaterialRate(tenant_id=tenant_id, code=str(data["code"]).strip().upper(),
                       name=str(data["name"]).strip()[:120], unit=canon,
                       rate=rate.quantize(Decimal("0.0001")),
                       source_unit=None if same else typed[:20],
                       source_rate=None if same else typed_rate,
                       yield_pct=yield_pct, scrap_rate=scrap.quantize(Decimal("0.0001"))
                       if scrap is not None else None,
                       effective_from=date.fromisoformat(str(data["effective_from"])),
                       note=(str(data.get("note") or "").strip()[:300] or None),
                       created_by=actor)
    session.add(row)
    await session.flush()
    await chain.append(session, tenant_id, "cost.material_rate.added", actor=actor,
                       record_type="material_rate", record_id=row.id,
                       data={"code": row.code, "rate": row.rate, "unit": row.unit,
                             "typed": f"{typed_rate} per {typed}" if not same else None,
                             "yield_pct": row.yield_pct, "scrap_rate": row.scrap_rate,
                             "from": row.effective_from, "note": row.note})
    return row


async def add_machine(session: AsyncSession, tenant_id: uuid.UUID, data: dict[str, Any],
                      actor: str, today: date) -> MachineRate:
    problems = _check(data, "machine", today, "hour_rate")
    if problems:
        raise ValidationFailed(problems)
    row = MachineRate(tenant_id=tenant_id, code=str(data["code"]).strip(),
                      name=str(data["name"]).strip()[:140],
                      hour_rate=Decimal(str(data["hour_rate"]).strip()),
                      effective_from=date.fromisoformat(str(data["effective_from"])),
                      note=(str(data.get("note") or "").strip()[:300] or None),
                      created_by=actor)
    session.add(row)
    await session.flush()
    await chain.append(session, tenant_id, "cost.machine_rate.added", actor=actor,
                       record_type="machine_rate", record_id=row.id,
                       data={"code": row.code, "hour_rate": row.hour_rate,
                             "from": row.effective_from, "note": row.note})
    return row


def _plain(v: Decimal | None) -> str | None:
    return None if v is None else format(v.normalize(), "f")


def material_out(r: MaterialRate) -> dict[str, Any]:
    return {"id": str(r.id), "code": r.code, "name": r.name, "unit": r.unit,
            "rate": _plain(r.rate), "source_unit": r.source_unit,
            "source_rate": _plain(r.source_rate), "yield_pct": _plain(r.yield_pct),
            "scrap_rate": _plain(r.scrap_rate), "effective_from": r.effective_from,
            "note": r.note, "created_by": r.created_by, "created_at": r.created_at}


def machine_out(r: MachineRate) -> dict[str, Any]:
    return {"id": str(r.id), "code": r.code, "name": r.name,
            "hour_rate": format(r.hour_rate.normalize(), "f"),
            "effective_from": r.effective_from, "note": r.note, "created_by": r.created_by,
            "created_at": r.created_at}


async def material_list(session: AsyncSession, at: date) -> list[dict[str, Any]]:
    """Each material: the rate in force on `at`, the next one if a later one is set, and
    how many rates it has had."""
    rows = list(await session.scalars(select(MaterialRate).order_by(
        MaterialRate.code, MaterialRate.effective_from, MaterialRate.created_at)))
    by: dict[str, list[MaterialRate]] = {}
    for r in rows:
        by.setdefault(r.code, []).append(r)
    erp = {i.code.upper(): i for i in await session.scalars(select(ErpItem).where(
        ErpItem.gone_at.is_(None), func.upper(ErpItem.code).in_([c.upper() for c in by])))} \
        if by else {}
    out = []
    for code, rs in by.items():
        now = [r for r in rs if r.effective_from <= at]
        later = [r for r in rs if r.effective_from > at]
        cur = now[-1] if now else None
        nxt = [r for r in later if r.effective_from == later[0].effective_from][-1] \
            if later else None
        item = erp.get(code.upper())
        out.append({"code": code, "name": (cur or rs[-1]).name,
                    "current": material_out(cur) if cur else None,
                    "next": material_out(nxt) if nxt else None,
                    "history": [material_out(r) for r in reversed(rs)][:50],
                    # K5 A: for reference only, per the item's stock unit in ERPNext.
                    "erpnext": {"item": item.code, "uom": item.uom,
                                "valuation_rate": _plain(item.valuation_rate),
                                "last_purchase_rate": _plain(item.last_purchase_rate),
                                "synced_at": item.synced_at} if item else None})
    return out


async def machine_list(session: AsyncSession, at: date) -> list[dict[str, Any]]:
    """Forge's machine-hour rates (current, next, history) and ERPNext's workstations."""
    rows = list(await session.scalars(select(MachineRate).order_by(
        MachineRate.code, MachineRate.effective_from, MachineRate.created_at)))
    by: dict[str, list[MachineRate]] = {}
    for r in rows:
        by.setdefault(r.code, []).append(r)
    erp = {w.code.upper(): w for w in await session.scalars(select(ErpWorkstation).where(
        ErpWorkstation.gone_at.is_(None), ErpWorkstation.disabled.is_(False)))}
    out = []
    for code, rs in by.items():
        now = [r for r in rs if r.effective_from <= at]
        later = [r for r in rs if r.effective_from > at]
        cur = now[-1] if now else None
        out.append({"code": code, "name": (cur or rs[-1]).name, "source": "forge",
                    "current": machine_out(cur) if cur else None,
                    "next": machine_out([r for r in later if r.effective_from
                                         == later[0].effective_from][-1]) if later else None,
                    "history": [machine_out(r) for r in reversed(rs)][:50],
                    "erpnext_wins": code.upper() in erp})
    for w in erp.values():
        out.append({"code": w.code, "name": w.name, "source": "erpnext",
                    "current": {"hour_rate": format(w.hour_rate.normalize(), "f"),
                                "synced_at": w.synced_at},
                    "next": None, "history": [], "erpnext_wins": True})
    return sorted(out, key=lambda x: (x["code"].upper(), x["source"]))


async def affected_quotes(session: AsyncSession, kind: str, code: str
                          ) -> list[dict[str, Any]]:
    """Open quotes whose current revision has a line costed with this rate, or whose
    cost was incomplete for want of it."""
    from sqlalchemy import or_

    from app.models import Quote, QuoteLine, QuoteRevision
    from app.models.quotes import OPEN_QUOTE_STATUSES

    rows = await session.execute(
        select(Quote.id, Quote.number, Quote.status, QuoteRevision.number)
        .join(QuoteRevision, (QuoteRevision.quote_id == Quote.id)
              & (QuoteRevision.number == Quote.active_revision))
        .join(QuoteLine, QuoteLine.revision_id == QuoteRevision.id)
        .where(Quote.status.in_(OPEN_QUOTE_STATUSES),
               or_(QuoteLine.cost.contains({"rates": [{"kind": kind, "code": code}]}),
                   QuoteLine.cost.contains({"missing": [{"kind": kind,
                                                         "code": code.strip().upper()}]})))
        .distinct().order_by(Quote.number))
    return [{"id": str(i), "number": n, "status": s, "revision": r} for i, n, s, r in rows]
