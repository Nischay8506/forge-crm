"""Cost models (F3.2, decisions C3 A, K3-K8 A): how one product family's cost is built
up from its configuration. Pure code: reading, checking and working out the quantities;
app/cpq/costing.py puts rates on them.

A cost model (stored as a version in cost_models.model) has:

    tables       small lookup tables, e.g. weights by size and class (K8 A)
    values       named formulas worked out in order, e.g. finished_kg
    materials    what goes in per unit: the material's code (a formula such as
                 `material`, or a fixed code such as F316) and its quantity per unit,
                 in the unit given (kg by default); yield and scrap come with the
                 material's rate (K4 A)
    operations   each one's machine and minutes per unit, costed at the machine-hour
                 rate (K6 A)
    tooling      one-time amounts (K3 A), or spread over the quantity when
                 tooling_spread is on
    overhead_pct the factory overhead, a % of material + conversion (K6 A)
    packing      a % of material + conversion + overhead, or an amount per unit
    packed_kg, packed_m3   one packed unit's weight and volume, for freight
    rate_card    the family's simpler cost when the full model is not set up: a cost
                 per kg (with the weight) or per unit (C3 A; used third, K7 A)

Formulas can use the configuration's attributes, `qty` (the quote line's quantity) and
the values named before them. A model is checked before it is saved: every formula must
read, name only what exists, and work out for every valid configuration.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.cpq import units
from app.cpq.configurator import Model as ProductModel
from app.cpq.configurator import Region
from app.cpq.formula import NAME, Formula, FormulaError, Table, Value, num

MAX_ITEMS = 30  # values, materials, operations, tooling: each up to this many
CHECK_LIMIT = 5000  # configurations tried when a model is checked
CHECK_QTYS = (Decimal(1), Decimal(100))
PACKING_BASES = ("pct", "per_unit")
RATE_CARD_BASES = ("kg", "unit")


class CostModelProblems(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Ref:
    """A material or machine named in a model: a fixed code, or a formula giving one."""

    fixed: str | None
    formula: Formula | None

    @staticmethod
    def parse(text: Any, what: str, problems: list[str]) -> Ref | None:
        t = str(text or "").strip()
        if not t:
            problems.append(f"{what}: say which one")
            return None
        try:
            return Ref(None, Formula(t))
        except FormulaError:
            if len(t) <= 140 and t[0].isalnum():
                return Ref(t.strip("\"'"), None)
            problems.append(f"{what}: {t!r} is neither a code nor a formula")
            return None

    @property
    def names(self) -> set[str]:
        return self.formula.names if self.formula else set()

    def value(self, env: dict[str, Value], tables: dict[str, Table]) -> str:
        if self.fixed is not None:
            return self.fixed
        assert self.formula is not None
        v = self.formula.eval(env, tables)
        if isinstance(v, bool):
            raise FormulaError("It gives yes/no, not a code")
        return num(v) if isinstance(v, Decimal) else v.strip()

    def text(self) -> str:
        return self.fixed if self.fixed is not None else self.formula.text  # type: ignore


@dataclass(frozen=True)
class Item:
    label: str
    formula: Formula  # quantity, minutes or amount
    ref: Ref | None = None  # material or machine
    unit: str | None = None


@dataclass
class CostModel:
    tables: dict[str, Table]
    values: list[tuple[str, str, Formula]]  # (name, label, formula)
    materials: list[Item]
    operations: list[Item]
    tooling: list[Item]
    tooling_spread: bool
    overhead_pct: Formula | None
    packing: tuple[str, Formula] | None
    packed_kg: Formula | None
    packed_m3: Formula | None
    rate_card: tuple[str, Formula, Formula | None] | None  # (basis, rate, weight)

    @property
    def full(self) -> bool:
        """The full model is set up (used first, K7 A); else only the rate card."""
        return bool(self.materials or self.operations)

    def formulas(self) -> list[tuple[str, Formula]]:
        out = [(f"Value {n}", f) for n, _, f in self.values]
        out += [(f"Material {m.label}", m.formula) for m in self.materials]
        out += [(f"Material {m.label}", m.ref.formula) for m in self.materials
                if m.ref and m.ref.formula]
        out += [(f"Operation {o.label}", o.formula) for o in self.operations]
        out += [(f"Operation {o.label}", o.ref.formula) for o in self.operations
                if o.ref and o.ref.formula]
        out += [(f"Tooling {t.label}", t.formula) for t in self.tooling]
        for what, f in (("Factory overhead", self.overhead_pct),
                        ("Packing", self.packing[1] if self.packing else None),
                        ("Packed weight", self.packed_kg), ("Packed volume", self.packed_m3)):
            if f is not None:
                out.append((what, f))
        if self.rate_card:
            out.append(("Rate card", self.rate_card[1]))
            if self.rate_card[2] is not None:
                out.append(("Rate card weight", self.rate_card[2]))
        return out


def _formula(raw: Any, what: str, problems: list[str], required: bool = True
             ) -> Formula | None:
    text = str(raw if raw is not None else "").strip()
    if not text:
        if required:
            problems.append(f"{what}: the formula is empty")
        return None
    try:
        return Formula(text)
    except FormulaError as e:
        problems.append(f"{what}: {e}")
        return None


def _label(raw: Any, what: str, n: int, problems: list[str]) -> str:
    t = str(raw or "").strip()[:100]
    if not t:
        problems.append(f"{what} {n}: give it a name")
    return t or f"{what} {n}"


def parse(data: dict[str, Any]) -> CostModel:
    """Read a model; CostModelProblems lists everything that is not well formed."""
    p: list[str] = []
    if not isinstance(data, dict):
        raise CostModelProblems(["The cost model is not readable"])
    tables: dict[str, Table] = {}
    raw_tables = data.get("tables") or {}
    if not isinstance(raw_tables, dict) or len(raw_tables) > 20:
        p.append("Up to 20 tables")
        raw_tables = {}
    for name, raw in raw_tables.items():
        t = Table.parse(str(name), raw, p)
        if t is not None:
            tables[t.name] = t

    values: list[tuple[str, str, Formula]] = []
    for i, raw in enumerate((data.get("values") or [])[:MAX_ITEMS + 1], 1):
        raw = raw if isinstance(raw, dict) else {}
        name = str(raw.get("name") or "").strip()
        if not NAME.match(name):
            p.append(f"Value {i}: the name must start with a letter and use only small "
                     "letters, digits and _")
            continue
        if name == "qty" or any(v[0] == name for v in values):
            p.append(f"Value {name}: the name is used twice")
            continue
        f = _formula(raw.get("formula"), f"Value {name}", p)
        if f is not None:
            values.append((name, str(raw.get("label") or name).strip()[:100], f))

    def items(key: str, what: str, ref_key: str | None, amount_key: str) -> list[Item]:
        out: list[Item] = []
        raw_list = data.get(key) or []
        if len(raw_list) > MAX_ITEMS:
            p.append(f"Up to {MAX_ITEMS} {key}")
        for i, raw in enumerate(raw_list[:MAX_ITEMS], 1):
            raw = raw if isinstance(raw, dict) else {}
            label = _label(raw.get("label"), what, i, p)
            ref = Ref.parse(raw.get(ref_key), f"{what} {label}", p) if ref_key else None
            f = _formula(raw.get(amount_key), f"{what} {label}", p)
            unit = str(raw.get("unit") or "").strip() or None
            if unit is not None and units.parse(unit) is None:
                p.append(f"{what} {label}: Forge doesn't know the unit {unit!r}")
            if f is not None and (ref is not None or ref_key is None):
                out.append(Item(label, f, ref, unit))
        return out

    materials = items("materials", "Material", "code", "qty")
    operations = items("operations", "Operation", "machine", "minutes")
    tooling = items("tooling", "Tooling", None, "amount")
    overhead = _formula(data.get("overhead_pct"), "Factory overhead", p, required=False)
    packing = None
    raw_packing = data.get("packing")
    if isinstance(raw_packing, dict) and str(raw_packing.get("value") or "").strip():
        basis = raw_packing.get("basis") or "pct"
        if basis not in PACKING_BASES:
            p.append("Packing is either a % or an amount per unit")
        f = _formula(raw_packing.get("value"), "Packing", p)
        if f is not None and basis in PACKING_BASES:
            packing = (basis, f)
    packed_kg = _formula(data.get("packed_kg"), "Packed weight", p, required=False)
    packed_m3 = _formula(data.get("packed_m3"), "Packed volume", p, required=False)
    rate_card = None
    raw_card = data.get("rate_card")
    if isinstance(raw_card, dict) and str(raw_card.get("rate") or "").strip():
        basis = raw_card.get("basis") or "unit"
        if basis not in RATE_CARD_BASES:
            p.append("The rate card is per kg or per unit")
        rate = _formula(raw_card.get("rate"), "Rate card", p)
        weight = _formula(raw_card.get("weight"), "Rate card weight", p,
                          required=basis == "kg")
        if rate is not None and basis in RATE_CARD_BASES:
            rate_card = (basis, rate, weight)
    m = CostModel(tables, values, materials, operations, tooling,
                  bool(data.get("tooling_spread")), overhead, packing, packed_kg, packed_m3,
                  rate_card)
    if not (m.full or m.rate_card) and not p:
        p.append("Add the materials and operations, or a rate card")
    if p:
        raise CostModelProblems(p)
    return m


# Working out --------------------------------------------------------------------------

@dataclass
class Quantities:
    """What one unit of a configuration needs, before rates (costing.py adds them)."""

    values: dict[str, Decimal] = field(default_factory=dict)
    materials: list[tuple[str, str, Decimal, str | None]] = field(default_factory=list)
    operations: list[tuple[str, str, Decimal]] = field(default_factory=list)  # minutes
    tooling: list[tuple[str, Decimal]] = field(default_factory=list)
    overhead_pct: Decimal | None = None
    packing: tuple[str, Decimal] | None = None
    packed_kg: Decimal | None = None
    packed_m3: Decimal | None = None
    rate_card: tuple[str, Decimal, Decimal | None] | None = None
    problems: list[str] = field(default_factory=list)


def evaluate(m: CostModel, attrs: dict[str, Any], qty: Decimal | None,
             only_rate_card: bool = False, with_rate_card: bool = True) -> Quantities:
    """Work out every formula for one configuration. A formula that fails is a problem
    (the cost is then incomplete, K10 A), and the rest is still worked out. The rate
    card is worked out only when it is used: alone (`only_rate_card`), or when the full
    model is not set up and `with_rate_card` (a BOM costs the line instead)."""
    q = Quantities()
    env: dict[str, Value] = {k: v if isinstance(v, Decimal) else str(v)
                             for k, v in attrs.items() if v is not None}
    if qty is not None:
        env["qty"] = qty
    failed: set[str] = set()

    def calc(what: str, f: Formula, number: bool = True) -> Any:
        gone = f.names & failed
        if gone:
            q.problems.append(f"{what}: needs {', '.join(sorted(gone))}, which could not be "
                              "worked out")
            return None
        if "qty" in f.names and "qty" not in env:
            q.problems.append(f"{what}: the line has no quantity")
            return None
        try:
            v = f.number(env, m.tables) if number else f.eval(env, m.tables)
        except FormulaError as e:
            q.problems.append(f"{what}: {e}")
            return None
        if number and v < 0:
            q.problems.append(f"{what}: it comes to {num(v)}, below zero")
            return None
        return v

    for name, _, f in m.values:
        v = calc(f"Value {name}", f)
        if v is None:
            failed.add(name)
        else:
            q.values[name] = v
            env[name] = v
    if not only_rate_card:
        for it in m.materials:
            amount = calc(f"Material {it.label}", it.formula)
            code = _ref(q, it, env, m, failed, "Material")
            if amount is not None and code is not None:
                q.materials.append((it.label, code, amount, it.unit))
        for it in m.operations:
            minutes = calc(f"Operation {it.label}", it.formula)
            machine = _ref(q, it, env, m, failed, "Operation")
            if minutes is not None and machine is not None:
                q.operations.append((it.label, machine, minutes))
        for it in m.tooling:
            amount = calc(f"Tooling {it.label}", it.formula)
            if amount is not None:
                q.tooling.append((it.label, amount))
        if m.overhead_pct is not None:
            q.overhead_pct = calc("Factory overhead", m.overhead_pct)
        if m.packing is not None:
            v = calc("Packing", m.packing[1])
            if v is not None:
                q.packing = (m.packing[0], v)
    if m.packed_kg is not None:
        q.packed_kg = calc("Packed weight", m.packed_kg)
    if m.packed_m3 is not None:
        q.packed_m3 = calc("Packed volume", m.packed_m3)
    if m.rate_card is not None and (only_rate_card or (with_rate_card and not m.full)):
        basis, rf, wf = m.rate_card
        rate = calc("Rate card", rf)
        weight = calc("Rate card weight", wf) if wf is not None else None
        if rate is not None and (basis == "unit" or weight is not None):
            q.rate_card = (basis, rate, weight)
    return q


def _ref(q: Quantities, it: Item, env: dict[str, Value], m: CostModel, failed: set[str],
         what: str) -> str | None:
    assert it.ref is not None
    gone = it.ref.names & failed
    if gone:
        return None
    try:
        code = it.ref.value(env, m.tables)
    except FormulaError as e:
        q.problems.append(f"{what} {it.label}: {e}")
        return None
    if not code:
        q.problems.append(f"{what} {it.label}: the code is empty")
        return None
    return code


# Checking a model before it is saved ---------------------------------------------------

def _examples(pm: ProductModel | None) -> list[dict[str, Any]]:
    """Valid configurations of the family to try the model on (every one, up to
    CHECK_LIMIT; a number attribute is tried at both ends of each stretch)."""
    if pm is None:
        return [{}]
    domains = []
    for a in pm.attributes:
        toks = pm.domains[a.key]
        vals: list[Any] = []
        for t in toks:
            if isinstance(t, Region):
                vals.extend({t.lo, t.hi})
            else:
                vals.append(t)
        domains.append((a, toks, vals))
    out: list[dict[str, Any]] = []
    for combo in itertools.product(*[range(len(v)) for _, _, v in domains]):
        values: dict[str, Any] = {}
        assign: dict[str, Any] = {}
        for (a, _toks, vals), i in zip(domains, combo, strict=True):
            v = vals[i]
            if v is None:
                assign[a.key] = None
                continue
            if isinstance(v, Decimal):
                assign[a.key] = pm.token(a, v)
                values[a.key] = num(v)
            else:
                assign[a.key] = v
                values[a.key] = v
        if pm._broken(assign):  # noqa: SLF001 - the configurator's own rule test
            continue
        out.append(values)
        if len(out) >= CHECK_LIMIT:
            break
    return out


@dataclass
class CheckResult:
    problems: list[str]
    warnings: list[str]
    tried: int
    materials: set[str]
    machines: set[str]


def check(data: dict[str, Any], pm: ProductModel | None, have_materials: set[str],
          have_machines: set[str]) -> CheckResult:
    """Problems block saving: formulas that don't read, name something that doesn't
    exist, or fail for a valid configuration. Warnings don't: a material or machine
    with no rate yet."""
    try:
        m = parse(data)
    except CostModelProblems as e:
        return CheckResult(e.problems, [], 0, set(), set())
    problems: list[str] = []
    known = set(pm.by_key) if pm is not None else set()
    known.add("qty")
    seen = set(known)
    for name, _, f in m.values:
        if name in known:
            problems.append(f"Value {name}: the name is already an attribute")
        for x in sorted(f.names - seen):
            problems.append(f"Value {name}: {x} is not an attribute or a value named before it")
        seen.add(name)
    for what, f in m.formulas():
        if what.startswith("Value "):
            continue
        for x in sorted(f.names - seen):
            problems.append(f"{what}: {x} is not an attribute or a named value")
    for what, f in m.formulas():
        for t, n_keys in f.tables.items():
            table = m.tables.get(t)
            if table is None:
                problems.append(f"{what}: there is no table called {t}")
            elif len(table.keys) != n_keys:
                problems.append(f"{what}: lookup({t}, ...) needs {len(table.keys)} keys "
                                f"({', '.join(table.keys)})")
    if problems:
        return CheckResult(sorted(set(problems)), [], 0, set(), set())

    examples = _examples(pm)
    found: dict[str, str] = {}  # problem -> one configuration it happens for
    mats: set[str] = set()
    machines: set[str] = set()
    for values in examples:
        for qty in CHECK_QTYS:
            q = evaluate(m, values, qty)
            if m.rate_card and m.full:
                q.problems += evaluate(m, values, qty, only_rate_card=True).problems
            for prob in q.problems:
                if prob not in found:
                    found[prob] = ", ".join(f"{k} {v}" for k, v in values.items())
            mats.update(code.upper() for _, code, _, _ in q.materials)
            machines.update(code.upper() for _, code, _ in q.operations)
        if len(found) >= 20:
            break
    for prob, where in found.items():
        problems.append(f"{prob} (for {where})" if where else prob)
    warnings = [f"Material {c} has no rate yet" for c in sorted(mats - have_materials)]
    warnings += [f"Machine {c} has no hour rate yet" for c in sorted(machines - have_machines)]
    return CheckResult(problems, warnings, len(examples), mats, machines)


def dump(data: dict[str, Any]) -> dict[str, Any]:
    """The model as stored: the parts Forge reads, tables tidied, nothing else kept."""
    m = parse(data)
    keep = {k: data.get(k) for k in ("values", "materials", "operations", "tooling",
                                     "tooling_spread", "overhead_pct", "packing",
                                     "packed_kg", "packed_m3", "rate_card")
            if data.get(k) not in (None, "", [], {})}
    keep["tables"] = {n: t.dump() for n, t in m.tables.items()}
    return keep
