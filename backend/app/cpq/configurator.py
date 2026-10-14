"""The product configurator's rule checker (F3.1, decision C4 A). Pure code, no AI and no
database: the same checker serves the guided questions, the API and the setup screen.

A product family's options model is a list of attributes and a list of rules:

- An attribute is a choice (options, each with a value, a label and a code for the
  variant code) or a number (a range with a step, for example a length in mm).
- A rule is one of
    requires   when <conditions> then <attribute> must be one of <values / in a range>
    excludes   when <conditions> then <attribute> must not be one of ...
    only_when  <attribute's option or range> is available only when <conditions>
  each with the plain reason shown to whoever picks a blocked option.

Every rule is an implication ("if these hold, that must (not) hold"), so the checker is a
small constraint solver: numbers are cut into ranges at every limit a rule names, so each
rule is either true or false for a whole range, and a backtracking search with forward
checking answers "is there still a complete, valid product with these choices?". An
option is offered only when the answer is yes, so the guided questions never reach a dead
end, and the API refuses anything else with the rule's reason.

The result of a configuration is its canonical values, a variant code from the family's
pattern (for example FL-{size}-{class}-{material}-{facing}) and a fingerprint of the
values, the same for the same product every time, for price history.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any

KEY = re.compile(r"^[a-z][a-z0-9_]{0,29}$")
CODE = re.compile(r"^[A-Za-z0-9.+/]{1,20}$")
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
MAX_ATTRIBUTES, MAX_OPTIONS, MAX_RULES = 30, 100, 300
MAX_REGIONS = 10_000  # (max - min) / step for a number: a guard against absurd ranges
NODE_BUDGET = 200_000  # combinations tried per question before giving up
KINDS = ("requires", "excludes", "only_when")


class ModelProblems(Exception):
    """The model cannot be used; `problems` says why, in words."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


class TooComplex(Exception):
    pass


@dataclass(frozen=True)
class Option:
    value: str
    label: str
    code: str
    erp_item: str | None = None


@dataclass(frozen=True)
class Attribute:
    key: str
    label: str
    kind: str  # choice | number
    required: bool = True
    options: tuple[Option, ...] = ()
    unit: str | None = None
    lo: Decimal | None = None
    hi: Decimal | None = None
    step: Decimal | None = None

    def option(self, value: str) -> Option | None:
        return next((o for o in self.options if o.value == value), None)


@dataclass(frozen=True)
class Cond:
    attr: str
    values: frozenset[str] | None = None  # a choice: one of these
    lo: Decimal | None = None  # a number: at least / at most
    hi: Decimal | None = None


@dataclass(frozen=True)
class Rule:
    id: str
    kind: str
    when: tuple[Cond, ...]
    then: Cond
    message: str


@dataclass(frozen=True)
class Region:
    """A stretch of a number attribute (inclusive, on its step grid) inside which every
    rule gives the same answer."""

    lo: Decimal
    hi: Decimal


@dataclass(frozen=True)
class _Imp:
    """`ante` all true implies `cons` (or, if neg, implies not `cons`)."""

    ante: tuple[Cond, ...]
    cons: Cond
    neg: bool
    rule: Rule
    attrs: frozenset[str]


Token = str | Region | None  # an option's value, a number's region, or "left empty"


@dataclass
class Result:
    values: dict[str, str]
    labels: list[dict[str, str]]
    variant_code: str
    fingerprint: str


@dataclass
class State:
    """Where a guided configuration stands: per attribute what may be picked and why not."""

    attributes: list[dict[str, Any]]
    complete: bool
    valid: bool
    problems: list[str] = field(default_factory=list)
    result: Result | None = None


def num(v: Decimal) -> str:
    """40.000 -> "40", 12.50 -> "12.5"."""
    s = format(v.normalize(), "f")
    return s if s not in ("-0",) else "0"


def _dec(v: Any) -> Decimal | None:
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        d = Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


# Reading a model ------------------------------------------------------------------------

def _text(v: Any, limit: int) -> str:
    return str(v or "").strip()[:limit]


def _attribute(raw: Any, n: int, out: list[str]) -> Attribute | None:
    if not isinstance(raw, dict):
        out.append(f"Attribute {n} is not readable")
        return None
    key, label = _text(raw.get("key"), 40), _text(raw.get("label"), 100)
    name = label or key or f"Attribute {n}"
    if not KEY.match(key):
        out.append(f"{name}: the key must start with a small letter and use only small "
                   "letters, digits and _ (up to 30)")
    if not label:
        out.append(f"Attribute {n} needs a name")
    kind = raw.get("kind")
    required = raw.get("required", True) is not False
    if kind == "choice":
        options, seen, codes = [], set(), set()
        for i, o in enumerate(raw.get("options") or [], 1):
            if not isinstance(o, dict):
                out.append(f"{name}: option {i} is not readable")
                continue
            value = _text(o.get("value"), 60)
            olabel = _text(o.get("label"), 100) or value
            code = _text(o.get("code"), 30) or value
            if not value:
                out.append(f"{name}: option {i} needs a value")
                continue
            if value in seen:
                out.append(f"{name}: the option {value!r} is there twice")
            if code in codes:
                out.append(f"{name}: two options have the code {code!r}, so two products "
                           "would get the same variant code")
            if not CODE.match(code):
                out.append(f"{name}: the code {code!r} of {olabel!r} may use only letters, "
                           "digits and . + / (up to 20)")
            seen.add(value)
            codes.add(code)
            options.append(Option(value, olabel, code, _text(o.get("erp_item"), 140) or None))
        if not options:
            out.append(f"{name} needs at least one option")
        if len(options) > MAX_OPTIONS:
            out.append(f"{name} has more than {MAX_OPTIONS} options")
        return Attribute(key, label or key, "choice", required, tuple(options))
    if kind == "number":
        lo, hi, step = _dec(raw.get("min")), _dec(raw.get("max")), _dec(raw.get("step") or 1)
        if lo is None or hi is None:
            out.append(f"{name}: give the smallest and the largest value")
            return None
        if step is None or step <= 0:
            out.append(f"{name}: the step must be more than 0")
            return None
        if hi <= lo:
            out.append(f"{name}: the largest value must be more than the smallest")
            return None
        if (hi - lo) / step > MAX_REGIONS * 100:
            out.append(f"{name}: the range has too many steps; use a larger step")
            return None
        return Attribute(key, label or key, "number", required, unit=_text(raw.get("unit"), 20)
                         or None, lo=lo, hi=hi, step=step)
    out.append(f"{name}: say whether it is a choice or a number")
    return None


def _cond(raw: Any, attrs: dict[str, Attribute], where: str, out: list[str]) -> Cond | None:
    if not isinstance(raw, dict):
        out.append(f"{where}: a condition is not readable")
        return None
    a = attrs.get(str(raw.get("attr") or ""))
    if a is None:
        out.append(f"{where}: {raw.get('attr')!r} is not one of the attributes")
        return None
    if a.kind == "choice":
        values = [str(v) for v in (raw.get("in") or [])]
        if not values:
            out.append(f"{where}: pick at least one option of {a.label}")
            return None
        unknown = [v for v in values if a.option(v) is None]
        if unknown:
            out.append(f"{where}: {a.label} has no option {unknown[0]!r}")
            return None
        return Cond(a.key, values=frozenset(values))
    lo, hi = _dec(raw.get("min")), _dec(raw.get("max"))
    if lo is None and hi is None:
        out.append(f"{where}: give a smallest or a largest {a.label}")
        return None
    if lo is not None and hi is not None and hi < lo:
        out.append(f"{where}: the largest {a.label} is less than the smallest")
        return None
    return Cond(a.key, lo=lo, hi=hi)


def _rule(raw: Any, n: int, attrs: dict[str, Attribute], out: list[str]) -> Rule | None:
    if not isinstance(raw, dict):
        out.append(f"Rule {n} is not readable")
        return None
    rid = _text(raw.get("id"), 20) or f"R-{n}"
    kind = raw.get("kind")
    where = f"Rule {rid}"
    if kind not in KINDS:
        out.append(f"{where}: the kind must be requires, excludes or only when")
        return None
    message = _text(raw.get("message"), 300)
    if not message:
        out.append(f"{where} needs the reason a person sees when it blocks an option")
    when_raw = raw.get("when") or []
    if not when_raw:
        out.append(f"{where} needs at least one condition")
    when = [_cond(c, attrs, where, out) for c in when_raw]
    then = _cond(raw.get("then"), attrs, where, out)
    if any(c is None for c in when) or then is None:
        return None
    keys = [c.attr for c in when]
    if len(set(keys)) != len(keys):
        out.append(f"{where}: name each attribute once in its conditions")
        return None
    if then.attr in keys:
        out.append(f"{where}: the attribute it limits cannot also be in its conditions")
        return None
    return Rule(rid, kind, tuple(c for c in when if c is not None), then, message)


@dataclass
class Model:
    attributes: list[Attribute]
    rules: list[Rule]
    code_pattern: str

    @classmethod
    def parse(cls, data: dict[str, Any]) -> Model:
        """Read a model; ModelProblems if it is not well formed (see check() for conflicts)."""
        out: list[str] = []
        raw_attrs = data.get("attributes") or []
        if not raw_attrs:
            out.append("Add at least one attribute")
        if len(raw_attrs) > MAX_ATTRIBUTES:
            out.append(f"Up to {MAX_ATTRIBUTES} attributes")
        attrs: list[Attribute] = []
        for i, raw in enumerate(raw_attrs, 1):
            a = _attribute(raw, i, out)
            if a is None:
                continue
            if any(b.key == a.key for b in attrs):
                out.append(f"Two attributes have the key {a.key!r}")
                continue
            attrs.append(a)
        by_key = {a.key: a for a in attrs}
        raw_rules = data.get("rules") or []
        if len(raw_rules) > MAX_RULES:
            out.append(f"Up to {MAX_RULES} rules")
        rules: list[Rule] = []
        for i, raw in enumerate(raw_rules, 1):
            r = _rule(raw, i, by_key, out)
            if r is None:
                continue
            if any(x.id == r.id for x in rules):
                out.append(f"Two rules have the id {r.id}")
                continue
            rules.append(r)
        pattern = _text(data.get("code_pattern"), 100)
        if not pattern:
            out.append("Give the variant code pattern, for example FL-{size}-{class}")
        else:
            named = PLACEHOLDER.findall(pattern)
            for k in named:
                if k not in by_key:
                    out.append(f"The variant code pattern names {{{k}}}, which is not an "
                               "attribute")
            missing = [a.label for a in attrs if a.key not in named]
            if missing:
                out.append(f"The variant code pattern leaves out {', '.join(missing)}, so "
                           "different products would get the same code")
        if out:
            raise ModelProblems(out)
        return cls(attrs, rules, pattern)

    def __post_init__(self) -> None:
        self.by_key = {a.key: a for a in self.attributes}
        imps: list[_Imp] = []
        for r in self.rules:
            if r.kind == "requires":
                imps.append(_Imp(r.when, r.then, False, r, _attrs(r.when, r.then)))
            elif r.kind == "excludes":
                imps.append(_Imp(r.when, r.then, True, r, _attrs(r.when, r.then)))
            else:  # only_when: the option needs each condition
                imps.extend(_Imp((r.then,), c, False, r, _attrs((r.then,), c)) for c in r.when)
        self.imps = imps
        self.imps_of: dict[str, list[_Imp]] = {a.key: [] for a in self.attributes}
        for imp in imps:
            for k in imp.attrs:
                self.imps_of[k].append(imp)
        self.domains: dict[str, list[Token]] = {a.key: self._domain(a) for a in self.attributes}

    # Domains --------------------------------------------------------------------------

    def _domain(self, a: Attribute) -> list[Token]:
        toks: list[Token]
        if a.kind == "choice":
            toks = [o.value for o in a.options]
        else:
            toks = list(self._regions(a))
        return toks + ([None] if not a.required else [])

    def _regions(self, a: Attribute) -> list[Region]:
        assert a.lo is not None and a.hi is not None and a.step is not None
        lo, step = a.lo, a.step

        def up(x: Decimal) -> Decimal:  # the first value on the grid at or above x
            k = ((x - lo) / step).to_integral_value(rounding=ROUND_CEILING)
            return lo + max(k, Decimal(0)) * step

        last = lo + ((a.hi - lo) / step).to_integral_value(rounding=ROUND_FLOOR) * step
        starts = {lo}
        for r in self.rules:
            for c in (*r.when, r.then):
                if c.attr != a.key:
                    continue
                if c.lo is not None:
                    starts.add(up(c.lo))
                if c.hi is not None:
                    g = up(c.hi)
                    starts.add(g + step if g == c.hi else g)
        cuts = sorted(s for s in starts if lo <= s <= last)
        return [Region(s, (cuts[i + 1] - step) if i + 1 < len(cuts) else last)
                for i, s in enumerate(cuts)]

    def token(self, a: Attribute, value: Any) -> Token:
        """The solver's token for a picked value; ValueError says why it cannot be used."""
        if value is None or value == "":
            if a.required:
                raise ValueError(f"Pick the {a.label.lower()}")
            return None
        if a.kind == "choice":
            v = str(value)
            if a.option(v) is None:
                raise ValueError(f"{a.label} has no option {v!r}")
            return v
        d = _dec(value)
        if d is None:
            raise ValueError(f"{a.label} must be a number")
        assert a.lo is not None and a.hi is not None and a.step is not None
        unit = f" {a.unit}" if a.unit else ""
        if d < a.lo or d > a.hi:
            raise ValueError(f"{a.label} must be from {num(a.lo)} to {num(a.hi)}{unit}")
        if (d - a.lo) % a.step != 0:
            raise ValueError(f"{a.label} goes in steps of {num(a.step)}{unit}")
        return next(r for r in self.domains[a.key]
                    if isinstance(r, Region) and r.lo <= d <= r.hi)

    # The solver -----------------------------------------------------------------------

    def _violated(self, imp: _Imp, assign: dict[str, Token]) -> bool:
        if any(c.attr not in assign or not _holds(c, assign[c.attr]) for c in imp.ante):
            return False
        if imp.cons.attr not in assign:
            return False
        return _holds(imp.cons, assign[imp.cons.attr]) == imp.neg

    def _broken(self, assign: dict[str, Token], only: str | None = None) -> list[_Imp]:
        """The rules the assignment breaks (all their attributes picked)."""
        imps = self.imps_of[only] if only else self.imps
        return [i for i in imps if i.attrs <= assign.keys() and self._violated(i, assign)]

    def _narrow(self, assign: dict[str, Token], domains: dict[str, list[Token]],
                changed: list[str]) -> dict[str, list[Token]] | None:
        """Forward checking: drop the values a rule rules out once all its other
        attributes are picked. None when an attribute has nothing left."""
        out = dict(domains)
        for key in changed:
            for imp in self.imps_of[key]:
                rest = imp.attrs - assign.keys()
                if len(rest) != 1:
                    continue
                (x,) = rest
                kept = [t for t in out[x] if not self._violated(imp, {**assign, x: t})]
                if not kept:
                    return None
                out[x] = kept
        return out

    def _search(self, assign: dict[str, Token], domains: dict[str, list[Token]],
                budget: list[int]) -> dict[str, Token] | None:
        if not domains:
            return assign
        budget[0] -= 1
        if budget[0] < 0:
            raise TooComplex
        key = min(domains, key=lambda k: (len(domains[k]), k))
        rest = {k: v for k, v in domains.items() if k != key}
        for t in domains[key]:
            trial = {**assign, key: t}
            if self._broken(trial, key):
                continue
            narrowed = self._narrow(trial, rest, [key])
            if narrowed is None:
                continue
            found = self._search(trial, narrowed, budget)
            if found is not None:
                return found
        return None

    def solve(self, assign: dict[str, Token], budget: list[int] | None = None
              ) -> dict[str, Token] | None:
        """A complete valid product extending `assign`, or None if there is none."""
        budget = budget if budget is not None else [NODE_BUDGET]
        if self._broken(assign):
            return None
        domains = {k: v for k, v in self.domains.items() if k not in assign}
        narrowed = self._narrow(assign, domains, list(assign))
        if narrowed is None:
            return None
        return self._search(dict(assign), narrowed, budget)

    def why_not(self, base: dict[str, Token], key: str, t: Token,
                budget: list[int]) -> str:
        """In words, why picking `t` for `key` leaves no valid product."""
        trial = {**base, key: t}
        direct = self._broken(trial, key)
        if direct:
            return _reasons(direct)
        domains = {k: v for k, v in self.domains.items() if k not in trial}
        for other, values in domains.items():
            blockers: list[_Imp] = []
            left = []
            for v in values:
                hit = self._broken({**trial, other: v}, other)
                if hit:
                    blockers.extend(hit)
                else:
                    left.append(v)
            if not left and blockers:
                label = self.by_key[other].label.lower()
                return f"It leaves no {label} that fits: {_reasons(blockers)}"
        return "It leaves no complete product that fits the other choices"

    # Questions ------------------------------------------------------------------------

    def tokens(self, values: dict[str, Any]) -> tuple[dict[str, Token], list[str]]:
        """Picked values as solver tokens, and the problems with the ones that cannot be
        used (unknown attributes, options or out-of-range numbers). Empty = not picked."""
        assign: dict[str, Token] = {}
        problems: list[str] = []
        for k, v in (values or {}).items():
            a = self.by_key.get(k)
            if a is None:
                problems.append(f"{k!r} is not one of the attributes")
                continue
            if v is None or v == "":
                continue
            try:
                assign[k] = self.token(a, v)
            except ValueError as exc:
                problems.append(str(exc))
        return assign, problems

    def state(self, values: dict[str, Any], budget: int = NODE_BUDGET) -> State:
        """The guided step: for each attribute, which options fit the other choices and
        why the rest do not; whether the product is complete and valid."""
        assign, problems = self.tokens(values)
        left = [budget]
        broken = self._broken(assign)
        if broken:
            problems.append(_reasons(broken))
        elif self.solve(assign, left) is None:
            problems.append("These choices leave no complete product: change one of them")
        out = []
        for a in self.attributes:
            base = {k: v for k, v in assign.items() if k != a.key}
            entries = []
            for t in self.domains[a.key]:
                ok = self.solve({**base, a.key: t}, left) is not None
                entry: dict[str, Any] = {"available": ok}
                if not ok:
                    entry["why"] = self.why_not(base, a.key, t, left)
                if a.kind == "choice":
                    if t is None:
                        continue
                    o = a.option(str(t))
                    assert o is not None
                    entries.append({"value": o.value, "label": o.label, "code": o.code,
                                    **entry})
                elif isinstance(t, Region):
                    entries.append({"min": num(t.lo), "max": num(t.hi), **entry})
            chosen = values.get(a.key) if a.key in assign else None
            item: dict[str, Any] = {
                "key": a.key, "label": a.label, "kind": a.kind, "required": a.required,
                "chosen": (num(_dec(chosen)) if a.kind == "number" and chosen is not None  # type: ignore[arg-type]
                           else chosen),
            }
            if a.kind == "choice":
                item["options"] = entries
            else:
                item.update({"unit": a.unit, "min": num(a.lo), "max": num(a.hi),  # type: ignore[arg-type]
                             "step": num(a.step), "ranges": entries})  # type: ignore[arg-type]
            out.append(item)
        complete = all(a.key in assign for a in self.attributes if a.required)
        valid = complete and not problems
        result = self._result(values, assign) if valid else None
        return State(out, complete, valid, problems, result)

    def configure(self, family_key: str, values: dict[str, Any]) -> Result:
        """A finished configuration, or ModelProblems with the reasons it is not one."""
        assign, problems = self.tokens(values)
        for a in self.attributes:
            if a.required and a.key not in assign and not any(
                    p.startswith(a.label) for p in problems):
                problems.append(f"Pick the {a.label.lower()}")
        broken = self._broken(assign)
        if broken:
            problems.append(_reasons(broken))
        if problems:
            raise ModelProblems(problems)
        return self._result(values, assign, family_key)

    def _result(self, values: dict[str, Any], assign: dict[str, Token],
                family_key: str = "") -> Result:
        canon: dict[str, str] = {}
        labels: list[dict[str, str]] = []
        for a in self.attributes:
            if assign.get(a.key) is None:
                continue
            if a.kind == "choice":
                o = a.option(str(values[a.key]))
                assert o is not None
                canon[a.key] = o.value
                text = o.label
            else:
                d = _dec(values[a.key])
                assert d is not None
                canon[a.key] = num(d)
                text = f"{num(d)} {a.unit}".strip() if a.unit else num(d)
            labels.append({"key": a.key, "label": a.label, "value": canon[a.key], "text": text})
        return Result(canon, labels, self.code(canon), fingerprint(family_key, canon))

    def code(self, canon: dict[str, str]) -> str:
        def part(m: re.Match[str]) -> str:
            a = self.by_key[m.group(1)]
            v = canon.get(a.key)
            if v is None:
                return ""
            if a.kind == "choice":
                o = a.option(v)
                return o.code if o else v
            return v

        s = PLACEHOLDER.sub(part, self.code_pattern)
        s = re.sub(r"([-/_.])[-/_.]+", r"\1", s)  # an empty optional part leaves no "--"
        return s.strip("-/_. ")[:100]

    # Checking a model before it is saved ----------------------------------------------

    def check(self, budget: int = NODE_BUDGET * 5) -> list[str]:
        """Conflicts and dead ends: the rules must leave at least one product, every
        option must be possible in some product, an optional attribute must really be
        optional, and no rule may repeat another."""
        out: list[str] = []
        seen: dict[tuple[Any, ...], str] = {}
        for r in self.rules:
            sig = (r.kind, frozenset(r.when), r.then)
            if sig in seen:
                out.append(f"Rule {r.id} repeats rule {seen[sig]}")
            seen.setdefault(sig, r.id)
        left = [budget]
        try:
            if self.solve({}, left) is None:
                return [*out, "No product can be made: together the rules rule out every "
                              "combination"]
            for a in self.attributes:
                for t in self.domains[a.key]:
                    if self.solve({a.key: t}, left) is not None:
                        continue
                    why = self.why_not({}, a.key, t, left)
                    if t is None:
                        out.append(f"{a.label} is marked optional, but the rules always need "
                                   f"it: mark it required ({why})")
                    elif isinstance(t, Region):
                        unit = f" {a.unit}" if a.unit else ""
                        span = num(t.lo) if t.lo == t.hi else f"{num(t.lo)} to {num(t.hi)}"
                        out.append(f"{a.label} {span}{unit} can never be chosen: {why}")
                    else:
                        o = a.option(t)
                        out.append(f"{a.label} {o.label if o else t!r} can never be chosen: "
                                   f"{why}")
        except TooComplex:
            out.append("The rules are too tangled to check; split them into simpler rules")
        return out

    def dump(self) -> dict[str, Any]:
        """The model as it is saved: only what was understood, in a fixed shape."""
        def cond(c: Cond) -> dict[str, Any]:
            if c.values is not None:
                a = self.by_key[c.attr]
                return {"attr": c.attr, "in": [o.value for o in a.options if o.value in c.values]}
            return {"attr": c.attr, **({"min": num(c.lo)} if c.lo is not None else {}),
                    **({"max": num(c.hi)} if c.hi is not None else {})}

        attrs = []
        for a in self.attributes:
            d: dict[str, Any] = {"key": a.key, "label": a.label, "kind": a.kind,
                                 "required": a.required}
            if a.kind == "choice":
                d["options"] = [{"value": o.value, "label": o.label, "code": o.code,
                                 **({"erp_item": o.erp_item} if o.erp_item else {})}
                                for o in a.options]
            else:
                d.update({"unit": a.unit, "min": num(a.lo), "max": num(a.hi),  # type: ignore[arg-type]
                          "step": num(a.step)})  # type: ignore[arg-type]
            attrs.append(d)
        rules = [{"id": r.id, "kind": r.kind, "when": [cond(c) for c in r.when],
                  "then": cond(r.then), "message": r.message} for r in self.rules]
        return {"attributes": attrs, "rules": rules, "code_pattern": self.code_pattern}

    def items(self) -> set[str]:
        """The ERP item codes the options point to (for change flags)."""
        return {o.erp_item for a in self.attributes for o in a.options if o.erp_item}


def _attrs(ante: tuple[Cond, ...], cons: Cond) -> frozenset[str]:
    return frozenset([*(c.attr for c in ante), cons.attr])


def _holds(c: Cond, t: Token) -> bool:
    if t is None:
        return False
    if c.values is not None:
        return t in c.values
    if not isinstance(t, Region):
        return False
    return (c.lo is None or t.lo >= c.lo) and (c.hi is None or t.hi <= c.hi)


def _reasons(imps: list[_Imp]) -> str:
    seen: list[str] = []
    for i in imps:
        text = f"{i.rule.message} (rule {i.rule.id})"
        if text not in seen:
            seen.append(text)
    return "; ".join(seen)


def fingerprint(family_key: str, canon: dict[str, str]) -> str:
    """The same family and values give the same fingerprint, whichever model version
    or person made it (F3.1: dedup and price history)."""
    blob = json.dumps({"family": family_key, "values": canon}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def load(data: dict[str, Any]) -> Model:
    """A saved model (already checked when it was saved)."""
    return Model.parse(data)


def next_rule_ids(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give new rules the next free id (R-1, R-2 ...); kept ids stay as they are."""
    used = {str(r.get("id")) for r in rules if r.get("id")}
    n = max([int(m.group(1)) for u in used if (m := re.match(r"^R-(\d+)$", u))] or [0])
    out = []
    for r in rules:
        if not r.get("id"):
            n += 1
            while f"R-{n}" in used:
                n += 1
            r = {**r, "id": f"R-{n}"}
            used.add(r["id"])
        out.append(r)
    return out
