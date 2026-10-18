"""Working out what a quote line costs (F3.2; decisions C3 A, K2 to K13 A).

A line's cost comes from the first of these that covers it (K7 A):

  model      the family's cost model, when its materials and operations are set up
  bom        the line's item's default BOM in ERPNext, with its operations. An item in
             it that has its own default BOM is costed from that BOM, as ERPNext does
  rate_card  the family's rate card: an all-in cost per kg or per unit
  unknown    none of them. The line can still be priced by hand, and goes for approval

Per unit (K4, K6 A):

  material    bought = net quantity / yield; bought x rate - (bought - net) x scrap rate
  conversion  minutes / 60 x the machine-hour rate (ERPNext's workstation first)
  overhead    the family's factory overhead, a % of material + conversion
  packing     a % of material + conversion + overhead, or an amount per unit
  tooling     a one-time amount shown apart, or spread over the quantity (K3 A)

Quantities are converted to their rate's unit with app/cpq/units.py, and every
conversion is kept (K12 A). Anything that can't be worked out (no rate, a formula that
fails, a unit that doesn't convert, BOMs that loop) leaves the cost incomplete (K10 A),
and so does a BOM cost with no family cost model behind it, because its factory overhead
and packing are then missing (J3 C). The rest is still worked out, and a person with
costs.manage reviews the line on a Decisions card (app/cpq/agent.py). The line keeps the
whole breakdown (inputs, rates with their dates, BOM versions, conversions and problems),
so it always shows why it cost what it did.

Each breakdown is sealed: seal_of() fingerprints it and cost_revision() writes every
line's seal into the audit chain, whose hashes and hourly signatures protect it (R3).
seals() works the fingerprints out again from the kept breakdowns and says, line by line,
whether each is still the cost Forge worked out, so a cost changed in the database
without Forge cannot pass as one Forge produced.

refresh() finds open quotes whose costs are out of date: a rate changed or took effect
(K9 A), an ERPNext BOM changed (K11 A), the cost model changed, or something that was
missing is there now. Drafts are worked out again. A sent quote is marked "cost
changed: reprice recommended", and its owner hears about it in one email a day.
"""

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import chain
from app.cpq import costmodel, quotes, units
from app.cpq.formula import num
from app.cpq.rates import Rate, RateBook
from app.models import (
    AuditRecord,
    Configuration,
    CostAlert,
    CostModel,
    ErpBom,
    ErpBomVersion,
    ErpConnection,
    ProductFamily,
    Quote,
    QuoteLine,
    QuoteRevision,
)
from app.models.quotes import OPEN_QUOTE_STATUSES

log = logging.getLogger(__name__)

ACTOR = "system:costs"
MAX_BOM_DEPTH = 5
CENT = Decimal("0.01")
SOURCE_LABELS = {"model": "the cost model", "bom": "the ERPNext BOM",
                 "rate_card": "the rate card", "unknown": "no cost source"}
KIND_LABELS = {"material": "Material", "machine": "Machine"}
NEEDS_REVIEW = ("incomplete", "unknown")


# Numbers in words -----------------------------------------------------------------------

def n(v: Decimal, places: int | None = None) -> str:
    """A quantity as people write it: 12.5, 0.875."""
    if places is not None:
        v = v.quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP)
    return num(v)


def nq(v: Decimal) -> str:
    """A quantity with the digits that matter: 19.1, 22.471, 0.1588, 0.00005."""
    if abs(v) >= 1 or v == 0:
        return n(v, 3)
    return num(v.quantize(Decimal(1).scaleb(v.adjusted() - 3), ROUND_HALF_UP))


def rs(v: Decimal) -> str:
    """Rupees with Indian grouping: ₹1,23,456.50, ₹410."""
    v = v.quantize(CENT, ROUND_HALF_UP)
    whole, _, frac = format(abs(v), "f").partition(".")
    if len(whole) > 3:
        head, groups = whole[:-3], [whole[-3:]]
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join(([head] if head else []) + groups)
    frac = frac.rstrip("0")
    return f"{'-' if v < 0 else ''}₹{whole}" + (f".{frac:0<2}" if frac else "")


def money(v: Decimal | None) -> str | None:
    return None if v is None else format(v.quantize(CENT, ROUND_HALF_UP), "f")


def _dec(v: Any) -> Decimal:
    try:
        d = Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        return Decimal(0)
    return d if d.is_finite() else Decimal(0)


# What costing reads ---------------------------------------------------------------------

_models: dict[uuid.UUID, costmodel.CostModel] = {}


def model_of(row: CostModel) -> costmodel.CostModel:
    """A saved cost model version, read once (versions never change)."""
    m = _models.get(row.id)
    if m is None:
        m = costmodel.parse(row.model)
        if len(_models) > 500:
            _models.clear()
        _models[row.id] = m
    return m


BOM_KEYS = ("code", "item_code", "quantity", "uom", "is_default", "items", "operations")


def bom_fingerprint(b: ErpBom) -> str:
    """What the BOM says, not when it was read: the same content, the same fingerprint."""
    content = {k: (b.data or {}).get(k) for k in BOM_KEYS}
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class Book:
    """What costing reads, loaded once per run: the rates on the day, each family's
    current cost model, the configurations, and the default BOMs of the items."""

    at: date
    rates: RateBook
    models: dict[uuid.UUID, CostModel] = field(default_factory=dict)  # family -> current
    configs: dict[uuid.UUID, Configuration] = field(default_factory=dict)
    families: dict[uuid.UUID, str] = field(default_factory=dict)
    boms: dict[str, ErpBom] = field(default_factory=dict)  # item code (upper) -> default
    # Connections whose BOM costs already carry the overhead or the packing: Forge adds
    # none on top of a cost it took from their BOMs, so neither is counted twice.
    erps: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)  # id -> name, included
    _looked: set[str] = field(default_factory=set)

    @staticmethod
    async def load(session: AsyncSession, at: date, lines: list[QuoteLine]) -> "Book":
        book = Book(at, await RateBook.load(session, at))
        fams = {ln.family_id for ln in lines if ln.family_id}
        if fams:
            book.models = {r.family_id: r for r in await session.scalars(select(CostModel).where(
                CostModel.current.is_(True), CostModel.family_id.in_(fams)))}
            book.families = dict((await session.execute(select(
                ProductFamily.id, ProductFamily.name).where(ProductFamily.id.in_(fams)))).all())
        cfgs = {ln.configuration_id for ln in lines if ln.configuration_id}
        if cfgs:
            book.configs = {c.id: c for c in await session.scalars(
                select(Configuration).where(Configuration.id.in_(cfgs)))}
        codes = {ln.item_code for ln in lines if ln.item_code}
        codes |= {str(b.get("item_code")) for ln in lines
                  for b in (ln.cost or {}).get("boms") or []}
        await book.load_boms(session, codes)
        return book

    async def load_boms(self, session: AsyncSession, codes: set[str]) -> None:
        """The default BOMs of these items and of the items inside them, a few levels
        down. Of two default BOMs for one item, the one changed last is used."""
        want = {c.strip().upper() for c in codes if c and c.strip()} - self._looked
        for _ in range(MAX_BOM_DEPTH + 1):
            if not want:
                return
            self._looked |= want
            for b in await session.scalars(select(ErpBom).where(
                    func.upper(ErpBom.item_code).in_(want), ErpBom.is_default.is_(True),
                    ErpBom.gone_at.is_(None), ErpBom.disabled.is_(False))
                    .order_by(ErpBom.modified_at)):
                self.boms[b.item_code.upper()] = b
            inner = {str(x.get("item_code") or "").strip().upper()
                     for c in want if c in self.boms
                     for x in (self.boms[c].data or {}).get("items") or []}
            want = inner - self._looked - {""}
        await self.load_erps(session)

    async def load_erps(self, session: AsyncSession) -> None:
        """What each ERP connection behind a BOM says its costs already include."""
        ids = {b.connection_id for b in self.boms.values()} - set(self.erps)
        if not ids:
            return
        for c in await session.scalars(select(ErpConnection).where(ErpConnection.id.in_(ids))):
            s = c.settings or {}
            self.erps[c.id] = {
                "name": c.name,
                "overhead": str(s.get("bom_includes_overhead", "")).lower() == "yes",
                "packing": str(s.get("bom_includes_packing", "")).lower() == "yes"}

    def included_by(self, bom: ErpBom) -> dict[str, Any]:
        return self.erps.get(bom.connection_id, {"name": "the ERP", "overhead": False,
                                                 "packing": False})

    def family(self, family_id: uuid.UUID | None) -> str:
        return self.families.get(family_id, "the family") if family_id else "the family"

    def rate(self, kind: str, code: str) -> Rate | None:
        return self.rates.material(code) if kind == "material" else self.rates.machine(code)


# One line ---------------------------------------------------------------------------------

@dataclass
class Work:
    """One line's cost while it is worked out."""

    source: str = "unknown"
    why: str = ""
    parts: list[dict[str, Any]] = field(default_factory=list)
    one_time: list[dict[str, Any]] = field(default_factory=list)
    rates: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    missing: set[tuple[str, str]] = field(default_factory=set)
    boms: list[dict[str, Any]] = field(default_factory=list)
    conversions: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    values: dict[str, str] = field(default_factory=dict)
    packed_kg: Decimal | None = None
    packed_m3: Decimal | None = None
    model: dict[str, Any] | None = None

    def part(self, group: str, label: str, detail: str, amount: Decimal) -> None:
        self.parts.append({"group": group, "label": label, "detail": detail,
                           "amount": money(amount)})

    def total(self, *groups: str) -> Decimal:
        return sum((Decimal(p["amount"]) for p in self.parts
                    if not groups or p["group"] in groups), Decimal(0))

    def problem(self, text: str) -> None:
        if text not in self.problems:
            self.problems.append(text)

    def note(self, text: str) -> None:
        """Something worth saying about the cost that is not a problem."""
        if text not in self.notes:
            self.notes.append(text)

    def convert(self, where: str, qty: Decimal, frm: str | None, to: str | None) -> Decimal:
        f = units.factor(frm or to, to or frm)
        if f != 1:
            self.conversions.append({"what": where, "from": frm, "to": to, "factor": n(f),
                                     "qty": n(qty, 6), "result": n(qty * f, 6)})
        return qty * f


def _material(w: Work, book: Book, label: str, code: str, net: Decimal, unit: str | None,
              *, where: str, use_yield: bool) -> None:
    if net <= 0:
        return
    rate = book.rates.material(code)
    if rate is None:
        w.problem(f"{where}: material {code} has no rate on {book.at:%d %b %Y}")
        w.missing.add(("material", code.strip().upper()))
        return
    w.rates[("material", rate.code)] = rate.out()
    try:
        q = w.convert(where, net, unit or rate.unit, rate.unit)
    except units.UnitError as e:
        w.problem(f"{where}: {e}")
        return
    u = units.LABEL.get(rate.unit, rate.unit)
    y = rate.yield_pct if use_yield and rate.yield_pct else Decimal(100)
    if y == 100:
        w.part("material", label, f"{nq(q)} {u} of {rate.code} at {rs(rate.rate)} per {u}",
               q * rate.rate)
        return
    bought = q * 100 / y
    cost = bought * rate.rate
    detail = (f"{nq(q)} {u} in the part, {nq(bought)} {u} of {rate.code} bought at "
              f"{n(y)}% yield, at {rs(rate.rate)} per {u}")
    if rate.scrap_rate:
        cost -= (bought - q) * rate.scrap_rate
        detail += f", less {nq(bought - q)} {u} scrap at {rs(rate.scrap_rate)}"
    w.part("material", label, detail, cost)


def _operation(w: Work, book: Book, label: str, machine: str, minutes: Decimal, *,
               where: str) -> None:
    if minutes <= 0:
        return
    rate = book.rates.machine(machine)
    if rate is None:
        w.problem(f"{where}: machine {machine} has no hour rate")
        w.missing.add(("machine", machine.strip().upper()))
        return
    w.rates[("machine", rate.code)] = rate.out()
    w.part("conversion", label, f"{n(minutes, 2)} min on {rate.name} at {rs(rate.rate)} per "
           "hour", minutes / 60 * rate.rate)


def _extras(w: Work, m: costmodel.CostModel, q: costmodel.Quantities,
            qty: Decimal | None, already: dict[str, Any] | None = None) -> None:
    """Overhead, packing and tooling from the family's model, on what is costed so far.
    `already` is what the ERP behind a BOM cost says it carries itself: that part is left
    out here rather than counted twice, and the line says so."""
    already = already or {}
    base = w.total("material", "conversion")
    if already.get("overhead"):
        w.note(f"The BOM cost from {already['name']} already includes the factory overhead, "
               "so Forge adds none.")
    elif q.overhead_pct:
        w.part("overhead", "Factory overhead",
               f"{n(q.overhead_pct)}% of material and conversion ({rs(base)})",
               base * q.overhead_pct / 100)
    if already.get("packing"):
        w.note(f"The BOM cost from {already['name']} already includes the packing, so Forge "
               "adds none.")
    elif q.packing is not None and q.packing[1] > 0:
        basis, v = q.packing
        if basis == "pct":
            b2 = w.total("material", "conversion", "overhead")
            w.part("packing", "Packing",
                   f"{n(v)}% of material, conversion and overhead ({rs(b2)})", b2 * v / 100)
        else:
            w.part("packing", "Packing", f"{rs(v)} per unit", v)
    for label, amount in q.tooling:
        if amount <= 0:
            continue
        if not m.tooling_spread:
            w.one_time.append({"label": label, "detail": "One-time, charged once on the quote",
                               "amount": money(amount)})
        elif qty is None or qty <= 0:
            w.problem(f"Tooling {label}: it is spread over the quantity, but the line has no "
                      "quantity")
        else:
            w.part("tooling", label, f"{rs(amount)} spread over {n(qty)}", amount / qty)
    w.packed_kg, w.packed_m3 = q.packed_kg, q.packed_m3


def _keep(w: Work, q: costmodel.Quantities) -> None:
    for p in q.problems:
        w.problem(p)
    w.values.update({k: n(v, 6) for k, v in q.values.items()})


def _from_model(w: Work, book: Book, row: CostModel, m: costmodel.CostModel,
                attrs: dict[str, Any], qty: Decimal | None, fam: str) -> None:
    w.source, w.why = "model", f"The cost model of {fam}, version {row.version}"
    q = costmodel.evaluate(m, attrs, qty)
    _keep(w, q)
    for label, code, amount, unit in q.materials:
        _material(w, book, label, code, amount, unit, where=f"Material {label}",
                  use_yield=True)
    for label, machine, minutes in q.operations:
        _operation(w, book, label, machine, minutes, where=f"Operation {label}")
    _extras(w, m, q, qty)


def _from_bom(w: Work, book: Book, bom: ErpBom, unit: str | None) -> None:
    w.source, w.why = "bom", f"ERPNext BOM {bom.code} of {bom.item_code}"
    per = Decimal(1)
    if unit and bom.uom:
        try:
            per = w.convert("The line's unit", Decimal(1), unit, bom.uom)
        except units.UnitError as e:
            w.problem(f"The line's unit: {e}")
    _explode(w, book, bom, per, 0, (bom.item_code.upper(),))


def _explode(w: Work, book: Book, bom: ErpBom, per: Decimal, depth: int,
             path: tuple[str, ...]) -> None:
    """`per`: how many of the BOM's unit one unit of the line needs."""
    data = bom.data or {}
    w.boms.append({"code": bom.code, "item_code": bom.item_code, "level": depth,
                   "fingerprint": bom_fingerprint(bom), "connection_id": str(bom.connection_id),
                   "modified_at": bom.modified_at.isoformat(), "quantity": n(bom.quantity),
                   "uom": bom.uom})
    if bom.quantity <= 0:
        w.problem(f"BOM {bom.code}: its quantity is {n(bom.quantity)}")
        return
    scale = per / bom.quantity
    inside = f" ({bom.item_code})" if depth else ""
    for it in data.get("items") or []:
        code = str(it.get("item_code") or "").strip()
        need, uom = _dec(it.get("qty")) * scale, it.get("uom")
        where = f"BOM {bom.code}, {code}"
        sub = book.boms.get(code.upper())
        if sub is None:
            # ERPNext's own rate on the BOM is for reference only (K5 A).
            _material(w, book, code + inside, code, need, uom, where=where, use_yield=False)
            continue
        if code.upper() in path:
            w.problem(f"{where}: the BOMs go round in a circle "
                      f"({' → '.join((*path, code.upper()))})")
            continue
        if depth + 1 >= MAX_BOM_DEPTH:
            w.problem(f"{where}: more than {MAX_BOM_DEPTH} levels of BOMs")
            continue
        try:
            sub_per = w.convert(where, need, uom, sub.uom)
        except units.UnitError as e:
            w.problem(f"{where}: {e}")
            continue
        _explode(w, book, sub, sub_per, depth + 1, (*path, code.upper()))
    for op in data.get("operations") or []:
        label = str(op.get("operation") or "Operation")
        ws = str(op.get("workstation") or "").strip()
        if not ws:
            w.problem(f"BOM {bom.code}, {label}: it has no workstation, so no hour rate")
            continue
        _operation(w, book, label + inside, ws, _dec(op.get("minutes")) * scale,
                   where=f"BOM {bom.code}, {label}")


def _from_rate_card(w: Work, row: CostModel, m: costmodel.CostModel, attrs: dict[str, Any],
                    qty: Decimal | None, fam: str) -> None:
    w.source, w.why = "rate_card", f"The rate card of {fam}, cost model version {row.version}"
    q = costmodel.evaluate(m, attrs, qty, only_rate_card=True)
    _keep(w, q)
    if q.rate_card is not None:
        basis, rate, weight = q.rate_card
        if basis == "kg" and weight is not None:
            w.part("rate_card", "Rate card", f"{nq(weight)} kg at {rs(rate)} per kg",
                   rate * weight)
        else:
            w.part("rate_card", "Rate card", f"{rs(rate)} per unit", rate)
    w.packed_kg, w.packed_m3 = q.packed_kg, q.packed_m3


@dataclass
class Pick:
    source: str
    row: CostModel | None
    model: costmodel.CostModel | None
    bom: ErpBom | None
    problem: str | None = None


def pick(book: Book, family_id: uuid.UUID | None, item_code: str | None) -> Pick:
    """Which source covers the line (K7 A): model, BOM, rate card, else unknown."""
    row = book.models.get(family_id) if family_id else None
    m, problem = None, None
    if row is not None:
        try:
            m = model_of(row)
        except costmodel.CostModelProblems as e:
            problem = (f"The cost model of {book.family(family_id)} (version {row.version}) "
                       f"can't be read: {'; '.join(e.problems[:3])}")
    bom = book.boms.get(item_code.strip().upper()) if item_code and item_code.strip() else None
    if m is not None and m.full:
        return Pick("model", row, m, bom, problem)
    if bom is not None:
        return Pick("bom", row, m, bom, problem)
    if m is not None and m.rate_card is not None:
        return Pick("rate_card", row, m, bom, problem)
    return Pick("unknown", row, m, None, problem)


def work_out(book: Book, *, family_id: uuid.UUID | None, configuration_id: uuid.UUID | None,
             item_code: str | None, unit: str | None, qty: Decimal | None) -> dict[str, Any]:
    """One line's cost breakdown, as stored on the line (QuoteLine.cost)."""
    w = Work()
    cfg = book.configs.get(configuration_id) if configuration_id else None
    attrs: dict[str, Any] = dict(cfg.values) if cfg else {}
    fam = book.family(family_id)
    p = pick(book, family_id, item_code)
    if p.problem:
        w.problem(p.problem)
    if p.row is not None and p.model is not None:
        w.model = {"id": str(p.row.id), "version": p.row.version}
    if p.source == "model":
        _from_model(w, book, p.row, p.model, attrs, qty, fam)  # type: ignore[arg-type]
    elif p.source == "bom":
        _from_bom(w, book, p.bom, unit)  # type: ignore[arg-type]
        already = book.included_by(p.bom)  # type: ignore[arg-type]
        if p.model is not None:  # the family's overhead, packing and tooling still apply
            q = costmodel.evaluate(p.model, attrs, qty, with_rate_card=False)
            _keep(w, q)
            _extras(w, p.model, q, qty, already)
        elif p.problem is None:
            # J3 C: overhead and packing come from the family's cost model (K6 A), so with
            # no model they are missing parts: the cost is incomplete and a person reviews
            # it on a card (K10), rather than a BOM-only cost passing as the whole cost.
            # What this ERP says its own BOM costs already carry is not missing.
            missing = []
            for word, key in (("factory overhead", "overhead"), ("packing", "packing")):
                if already.get(key):
                    w.note(f"The BOM cost from {already['name']} already includes the {word}.")
                else:
                    missing.append(word)
            if missing:
                w.problem(f"BOM {p.bom.code}: "  # type: ignore[union-attr]
                          + ("the line has no product family" if family_id is None
                             else f"{fam} has no cost model yet")
                          + f", so its {' and '.join(missing)} "
                          + ("are" if len(missing) > 1 else "is") + " missing")
    elif p.source == "rate_card":
        _from_rate_card(w, p.row, p.model, attrs, qty, fam)  # type: ignore[arg-type]
    else:
        why = []
        if family_id is None:
            why.append("the line has no product family")
        elif p.row is None:
            why.append(f"{fam} has no cost model yet")
        elif p.model is not None:
            why.append(f"the cost model of {fam} has no materials, operations or rate card")
        why.append(f"ERPNext has no default BOM for {item_code}" if item_code
                   else "the line has no item code to find an ERPNext BOM")
        w.why = "No cost source: " + "; ".join(why)
    status = ("unknown" if w.source == "unknown"
              else "incomplete" if w.problems else "complete")
    out: dict[str, Any] = {
        "source": w.source, "status": status, "why": w.why, "parts": w.parts,
        "per_unit": money(w.total()) if w.source != "unknown" else None,
        "one_time": w.one_time,
        "one_time_total": money(sum((Decimal(x["amount"]) for x in w.one_time), Decimal(0)))
        if w.one_time else None,
        "rates": sorted(w.rates.values(), key=lambda r: (r["kind"], r["code"])),
        "missing": [{"kind": k, "code": c} for k, c in sorted(w.missing)],
        "boms": w.boms, "conversions": w.conversions, "problems": w.problems,
        "notes": w.notes,
        "values": w.values, "model": w.model,
        "packed_kg": n(w.packed_kg, 4) if w.packed_kg is not None else None,
        "packed_m3": n(w.packed_m3, 6) if w.packed_m3 is not None else None,
        "inputs": {"family_id": str(family_id) if family_id else None,
                   "configuration_id": str(configuration_id) if configuration_id else None,
                   "values": attrs, "item_code": item_code, "unit": unit,
                   "qty": n(qty) if qty is not None else None},
        "at": book.at.isoformat(), "costed_at": datetime.now(UTC).isoformat(),
    }
    out["review_key"] = review_key(out)
    out["seal"] = seal_of(out)
    return out


def seal_of(out: dict[str, Any]) -> str:
    """The fingerprint of a worked-out cost: every part, rate, BOM version, conversion
    and input that made it. cost_revision() writes it into the audit chain, so a stored
    breakdown that no longer matches its seal was changed outside Forge (seals()).
    The seal itself and the review a person later added are left out."""
    body = {k: v for k, v in out.items() if k not in ("seal", "review")}
    return hashlib.sha256(chain.canonical(json.loads(json.dumps(body, default=str)))
                          .encode()).hexdigest()


def review_key(out: dict[str, Any]) -> str:
    """What a person reviews on a K10 card: why the cost is incomplete or unknown, and
    for what. The quantity and the rates' amounts are left out, so changing them does
    not ask again; a different problem, model, BOM or product does."""
    content = {"source": out["source"], "problems": sorted(out["problems"]),
               "missing": out["missing"], "model": (out.get("model") or {}).get("id"),
               "boms": sorted((b["code"], b["fingerprint"]) for b in out["boms"]),
               "configuration": out["inputs"]["configuration_id"],
               "item_code": out["inputs"]["item_code"], "unit": out["inputs"]["unit"]}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:32]


# A revision --------------------------------------------------------------------------------

async def record_boms(session: AsyncSession, boms: list[ErpBom]) -> int:
    """Keep every version of a BOM Forge has used or read (K11 A). Returns how many
    versions are new."""
    new = 0
    for b in {b.id: b for b in boms}.values():
        stmt = pg_insert(ErpBomVersion).values(
            id=uuid.uuid4(), tenant_id=b.tenant_id, connection_id=b.connection_id,
            bom_code=b.code, item_code=b.item_code, fingerprint=bom_fingerprint(b),
            data=b.data, modified_at=b.modified_at,
        ).on_conflict_do_nothing(index_elements=["connection_id", "bom_code", "fingerprint"])
        new += (await session.execute(stmt)).rowcount or 0
    return new


async def cost_revision(session: AsyncSession, q: Quote, rev: QuoteRevision, actor: str,
                        why: str = "worked out") -> dict[str, Any]:
    """Work out every line of a draft revision and keep each breakdown on its line.
    Incomplete or unknown lines go to a person with costs.manage (K10 A), unless the
    same thing was already reviewed. The revision is priced again straight after
    (app/cpq/pricing.py), because a cost that changed changes the price it carries."""
    from app.agents import runtime
    from app.cpq import agent as cost_agent
    from app.cpq import pricing

    quotes.must_be_draft(q, rev)
    lines = await quotes.lines_of(session, rev)
    book = await Book.load(session, await quotes.today(session, q.tenant_id), lines)
    review: list[QuoteLine] = []
    summary: list[dict[str, Any]] = []
    line_seals: dict[str, str] = {}
    for ln in lines:
        out = work_out(book, family_id=ln.family_id, configuration_id=ln.configuration_id,
                       item_code=ln.item_code, unit=ln.unit, qty=ln.qty)
        status = out["status"]
        unit_cost = Decimal(out["per_unit"]) if out["per_unit"] is not None else None
        before = (ln.cost or {}).get("review")
        if status in NEEDS_REVIEW and before and before.get("key") == out["review_key"]:
            out["review"], status = before, "reviewed"
            if before.get("unit_cost") is not None:
                unit_cost = Decimal(before["unit_cost"])
        ln.cost, ln.cost_source, ln.cost_status = out, out["source"], status
        ln.unit_cost, ln.cost_flag = unit_cost, None
        ln.updated_at = datetime.now(UTC)
        if status in NEEDS_REVIEW:
            review.append(ln)
        elif ln.cost_review_id is not None and status == "complete":
            await runtime.supersede(session, f"cost:{ln.id}", actor,
                                    "The cost is complete now")
            ln.cost_review_id = None
        summary.append({"line": ln.position, "source": out["source"], "status": status,
                        "unit_cost": money(unit_cost), "problems": out["problems"][:5]})
        line_seals[str(ln.position)] = out["seal"]
    await record_boms(session, [book.boms[b["item_code"].upper()] for ln in lines
                                for b in ln.cost["boms"]
                                if b["item_code"].upper() in book.boms])
    rev.costed_at = datetime.now(UTC)
    rev.reprice_reason = rev.reprice_at = None
    await session.flush()
    await chain.append(session, q.tenant_id, "quote.costed", actor=actor, record_type="quote",
                       record_id=q.id, account_id=q.account_id,
                       evidence_class="customer_money",
                       data={"number": q.number, "revision": rev.number, "why": why,
                             "at": book.at, "lines": summary,
                             # Self-contained, and in the chain's plain skeleton: the
                             # seals are still readable once the rest is trimmed (R5).
                             "cost_seals": {"revision": rev.number,
                                            "lines": line_seals}})
    if review:
        await cost_agent.ask_review(session, q, rev, review)
    priced = await pricing.price_revision(session, q, rev, actor, why=why)
    return {"lines": summary, "prices": priced}


async def seals(session: AsyncSession, q: Quote, rev: QuoteRevision) -> dict[str, Any]:
    """Check each costed line against the fingerprint the audit chain holds for it.

    A cost is worked out by Forge and then kept on the line. This says whether what is
    kept is still the cost Forge worked out: the audit record of the costing holds each
    line's seal, the chain's own hashes and hourly signatures protect that record (R3),
    so a breakdown edited straight in the database no longer matches. "checked" is the
    audit record it was checked against; a quote costed before seals existed, or never
    costed, says so instead."""
    rec = await session.scalar(
        select(AuditRecord).where(AuditRecord.tenant_id == q.tenant_id,
                                  AuditRecord.kind == "quote.costed",
                                  AuditRecord.record_type == "quote",
                                  AuditRecord.record_id == q.id)
        .order_by(AuditRecord.seq.desc()).limit(1))
    sealed = ((rec.data or {}).get("cost_seals") or {}) if rec is not None else {}
    held = sealed.get("lines") or {}
    same_rev = sealed.get("revision") == rev.number
    lines = []
    for ln in await quotes.lines_of(session, rev):
        cost = ln.cost or {}
        if not cost:
            lines.append({"line": ln.position, "state": "not_costed"})
            continue
        want = held.get(str(ln.position)) if same_rev else None
        if want is None:
            lines.append({"line": ln.position, "state": "not_sealed"})
            continue
        # Worked out again from the kept breakdown, never read from its own "seal"
        # key: the point is to catch a breakdown changed outside Forge, and an
        # editor who leaves that key alone would otherwise pass.
        now = seal_of(cost)
        lines.append({"line": ln.position, "state": "sealed" if now == want else "changed"})
    return {"revision": rev.number,
            "checked": {"seq": rec.seq, "at": rec.created_at.isoformat()}
            if rec is not None and same_rev else None,
            "ok": all(x["state"] != "changed" for x in lines), "lines": lines}


# Out of date ----------------------------------------------------------------------------

def stale(book: Book, ln: QuoteLine) -> list[str]:
    """Why a costed line's cost is out of date (empty: it isn't)."""
    c = ln.cost or {}
    out: list[str] = []
    for r in c.get("rates") or []:
        now, word = book.rate(r["kind"], r["code"]), KIND_LABELS.get(r["kind"], r["kind"])
        unit = units.LABEL.get(r.get("unit"), r.get("unit"))
        if now is None:
            out.append(f"{word} {r['code']} has no rate any more")
        elif now.ref != r.get("ref") or now.rate != _dec(r.get("rate")):
            out.append(f"{word} {r['code']}: {rs(_dec(r.get('rate')))} → {rs(now.rate)} per "
                       f"{unit}")
    for x in c.get("missing") or []:
        now = book.rate(x["kind"], x["code"])
        if now is not None:
            out.append(f"{KIND_LABELS.get(x['kind'], x['kind'])} {x['code']} has a rate now "
                       f"({rs(now.rate)} per {units.LABEL.get(now.unit, now.unit)})")
    for b in c.get("boms") or []:
        cur = book.boms.get(str(b.get("item_code")).upper())
        if cur is None:
            out.append(f"ERPNext has no default BOM for {b.get('item_code')} any more")
        elif cur.code != b.get("code") or bom_fingerprint(cur) != b.get("fingerprint"):
            out.append(f"The ERPNext BOM of {b.get('item_code')} changed ({cur.code})")
    row = book.models.get(ln.family_id) if ln.family_id else None
    used = (c.get("model") or {}).get("id")
    if used and (row is None or str(row.id) != used):
        out.append(f"The cost model of {book.family(ln.family_id)} changed"
                   + (f" (version {row.version})" if row else ""))
    source = pick(book, ln.family_id, ln.item_code).source
    if c.get("source") and source != c["source"]:
        out.append(f"It can now be costed from {SOURCE_LABELS[source]}"
                   if source != "unknown" else f"{SOURCE_LABELS[c['source']]} is gone")
    return list(dict.fromkeys(out))


async def refresh(session: AsyncSession, tenant_id: uuid.UUID, actor: str, *,
                  rate: tuple[str, str] | None = None, item_codes: set[str] | None = None,
                  family_id: uuid.UUID | None = None) -> dict[str, list[dict[str, Any]]]:
    """Open quotes whose cost is out of date (K9 A, K11 A): drafts are worked out
    again; a sent or pending revision is marked "reprice recommended" and its owner
    gets it in the daily email. Narrowed to the lines a rate, BOM items or a family
    touch when given."""
    stmt = (select(Quote, QuoteRevision)
            .join(QuoteRevision, (QuoteRevision.quote_id == Quote.id)
                  & (QuoteRevision.number == Quote.active_revision))
            .where(Quote.status.in_(OPEN_QUOTE_STATUSES)))
    narrow = []
    if rate is not None:
        kind, code = rate
        narrow += [QuoteLine.cost.contains({"rates": [{"kind": kind, "code": code}]}),
                   QuoteLine.cost.contains({"missing": [{"kind": kind,
                                                         "code": code.strip().upper()}]})]
    for code in item_codes or ():
        narrow += [QuoteLine.cost.contains({"boms": [{"item_code": code}]}),
                   func.upper(QuoteLine.item_code) == code.upper()]
    if family_id is not None:
        narrow.append(QuoteLine.family_id == family_id)
    if narrow:
        stmt = stmt.where(QuoteRevision.id.in_(select(QuoteLine.revision_id).where(or_(*narrow))))
    pairs = list((await session.execute(stmt.order_by(Quote.number))).all())
    done: dict[str, list[dict[str, Any]]] = {"recosted": [], "flagged": []}
    if not pairs:
        return done
    by_rev: dict[uuid.UUID, list[QuoteLine]] = {}
    for ln in await session.scalars(select(QuoteLine).where(
            QuoteLine.revision_id.in_([r.id for _, r in pairs])).order_by(QuoteLine.position)):
        by_rev.setdefault(ln.revision_id, []).append(ln)
    book = await Book.load(session, await quotes.today(session, tenant_id),
                           [ln for lines in by_rev.values() for ln in lines])
    for q, rev in pairs:
        lines = by_rev.get(rev.id, [])
        reasons = list(dict.fromkeys(
            f"Line {ln.position}: {r}" for ln in lines if ln.cost for r in stale(book, ln)))
        row = {"id": str(q.id), "number": q.number, "revision": rev.number,
               "status": q.status, "reasons": reasons}
        if rev.status == "draft":
            if reasons or (rev.costed_at is None and lines):
                await cost_revision(session, q, rev, actor, why="; ".join(reasons)[:300]
                                    or "not worked out yet")
                done["recosted"].append(row)
        elif reasons and await flag(session, q, rev, reasons):
            done["flagged"].append(row)
    return done


async def flag(session: AsyncSession, q: Quote, rev: QuoteRevision, reasons: list[str]) -> bool:
    """Mark a sent revision "cost changed: reprice recommended" and queue the owner's
    alert (K9 A). Reasons already noted are not noted again."""
    have = list(await session.scalars(select(CostAlert.reason).where(
        CostAlert.quote_id == q.id, CostAlert.revision == rev.number)
        .order_by(CostAlert.created_at)))
    new = list(dict.fromkeys(r[:500] for r in reasons if r[:500] not in have))
    if not new:
        return False
    rev.reprice_reason = "; ".join(have + new)[:500]
    rev.reprice_at = datetime.now(UTC)
    for r in new:
        session.add(CostAlert(tenant_id=q.tenant_id, quote_id=q.id, revision=rev.number,
                              owner_id=q.owner_id, reason=r))
    await chain.append(session, q.tenant_id, "quote.reprice_recommended", actor=ACTOR,
                       record_type="quote", record_id=q.id, account_id=q.account_id,
                       evidence_class="customer_money",
                       data={"number": q.number, "revision": rev.number, "reasons": new})
    await session.flush()
    return True


async def boms_changed(session: AsyncSession, tenant_id: uuid.UUID,
                       bom_ids: list[uuid.UUID]) -> dict[str, Any]:
    """After an ERPNext sync (K11 A): keep each new BOM version, then work out the
    drafts that use the item again and flag the sent quotes."""
    rows = list(await session.scalars(select(ErpBom).where(ErpBom.id.in_(bom_ids))))
    new = await record_boms(session, rows)
    if not new:
        return {"bom_versions": 0}
    done = await refresh(session, tenant_id, ACTOR, item_codes={b.item_code for b in rows})
    return {"bom_versions": new, "recosted": len(done["recosted"]),
            "flagged": len(done["flagged"])}


async def daily_refresh_all() -> dict[str, int]:
    """Every company's open quotes against today's rates and BOMs (rates dated ahead
    take effect, ERPNext workstation rates change). Hourly; only what changed moves."""
    from app.db import get_sessionmaker, tenant_session
    from app.models import Tenant

    async with get_sessionmaker()() as s:
        tenant_ids = list(await s.scalars(select(Tenant.id)))
    total = {"recosted": 0, "flagged": 0}
    for tid in tenant_ids:
        try:
            async with tenant_session(tid) as s:
                done = await refresh(s, tid, ACTOR)
                for k in total:
                    total[k] += len(done[k])
        except Exception:
            log.exception("cost refresh for tenant %s failed", tid)
    return total


async def email_alerts(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    """One email a day to each owner, listing their sent quotes whose cost changed."""
    from app.channels import staff

    rows = list((await session.execute(
        select(CostAlert, Quote.number).join(Quote, Quote.id == CostAlert.quote_id)
        .where(CostAlert.emailed_at.is_(None), CostAlert.owner_id.is_not(None))
        .order_by(Quote.number, CostAlert.created_at))).all())
    by_owner: dict[uuid.UUID, list[tuple[CostAlert, str]]] = {}
    for a, number in rows:
        by_owner.setdefault(a.owner_id, []).append((a, number))
    day = await quotes.today(session, tenant_id)
    now = datetime.now(UTC)
    sent = 0
    for owner, items in by_owner.items():
        count = len({a.quote_id for a, _ in items})
        lines = [f"- {number} R{a.revision}: {a.reason}" for a, number in items[:60]]
        if len(items) > 60:
            lines.append(f"- ...and {len(items) - 60} more")
        title = (f"Costs changed on {count} of your sent quote{'s' if count > 1 else ''}: "
                 "reprice recommended")
        body = ("The costs behind these quotes changed after they were sent. Look at each one "
                "and decide whether to send a new revision.\n\n" + "\n".join(lines))
        sent += await staff.email(session, tenant_id, {owner}, title=title, body=body,
                                  url="/quotes?reprice=1", key=f"cost-alerts:{owner}:{day}")
        for a, _ in items:
            a.emailed_at = now
    await session.flush()
    return sent


async def email_alerts_all() -> int:
    from app.db import get_sessionmaker, tenant_session
    from app.models import Tenant

    async with get_sessionmaker()() as s:
        tenant_ids = list(await s.scalars(select(Tenant.id)))
    total = 0
    for tid in tenant_ids:
        try:
            async with tenant_session(tid) as s:
                total += await email_alerts(s, tid)
        except Exception:
            log.exception("cost alert emails for tenant %s failed", tid)
    return total


# Cost models in the database (K8, K13 A) -------------------------------------------------

async def have_codes(session: AsyncSession) -> tuple[set[str], set[str]]:
    """Material and machine codes that have a rate today, for a model's check."""
    book = await RateBook.load(session, date.max)
    return set(book.materials), set(book.machines)


async def check_model(session: AsyncSession, family: ProductFamily,
                      data: dict[str, Any]) -> costmodel.CheckResult:
    from app.cpq import products

    pm_row = await products.current(session, family.id)
    pm = products.model_of(pm_row) if pm_row is not None else None
    mats, machines = await have_codes(session)
    return costmodel.check(data, pm, mats, machines)


async def save_model(session: AsyncSession, family: ProductFamily, data: dict[str, Any],
                     actor: str, note: str | None = None) -> CostModel:
    """A new version of the family's cost model, only if it checks out (problems
    block it; warnings don't). Open quotes that used the old version are worked out
    again or marked for repricing."""
    result = await check_model(session, family, data)
    if result.problems:
        raise costmodel.CostModelProblems(result.problems)
    clean = costmodel.dump(data)
    before = await session.scalar(select(CostModel).where(
        CostModel.family_id == family.id, CostModel.current.is_(True)).with_for_update())
    version = (await session.scalar(select(func.max(CostModel.version)).where(
        CostModel.family_id == family.id)) or 0) + 1
    if before is not None:
        before.current = False
        await session.flush()
    row = CostModel(tenant_id=family.tenant_id, family_id=family.id, version=version,
                    current=True, model=clean, note=(note or "").strip()[:300] or None,
                    created_by=actor)
    session.add(row)
    await session.flush()
    await chain.append(session, family.tenant_id, "cost.model.saved", actor=actor,
                       record_type="cost_model", record_id=row.id,
                       data={"family_id": family.id, "family": family.name,
                             "version": version, "note": row.note,
                             "previous": before.version if before else None,
                             "full": bool(clean.get("materials") or clean.get("operations")),
                             "rate_card": bool(clean.get("rate_card")),
                             "warnings": result.warnings})
    await refresh(session, family.tenant_id, actor, family_id=family.id)
    return row


async def add_sample(session: AsyncSession, family: ProductFamily, actor: str
                     ) -> dict[str, Any]:
    """The flanges sample cost model (C18 A), with sample rates for the materials and
    machines it uses where the company has no rate yet."""
    from app.cpq import rates
    from app.cpq.samples import FLANGES_COST, SAMPLE_MACHINES, SAMPLE_MATERIALS, SAMPLE_NOTE

    day = await quotes.today(session, family.tenant_id)
    mats, machines = await have_codes(session)
    added = []
    for m in SAMPLE_MATERIALS:
        if m["code"].upper() not in mats:
            await rates.add_material(session, family.tenant_id, {
                **m, "effective_from": day.isoformat(), "note": SAMPLE_NOTE}, actor, day)
            added.append(m["code"])
    for m in SAMPLE_MACHINES:
        if m["code"].upper() not in machines:
            await rates.add_machine(session, family.tenant_id, {
                **m, "effective_from": day.isoformat(), "note": SAMPLE_NOTE}, actor, day)
            added.append(m["code"])
    row = await save_model(session, family, FLANGES_COST, actor,
                           note="Started from the flanges sample")
    return {"model": row, "sample_rates": added}
