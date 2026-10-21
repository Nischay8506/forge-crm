"""Who has to approve a discount, and in what order (F3.4; Z1-Z10 A, 2026-10-21).

A company writes its delegation of authority as a **matrix**: rows that say "when a quote
looks like this, these roles approve, in this order". A quote's discount is measured three
ways (Z1 A): how deep the discount is, the margin it leaves against the family's floor, and
what the quote is worth. Payment terms and special terms become rows later, with F3.5.

Approvers are named by role, never by person (Z2 A), and the chain this module works out is
handed to the approvals engine as it stands, so everything Step 5 already guarantees still
holds: step 1 decides before step 2, a mandatory step can never be skipped, a rejection ends
the chain, a counter goes back through the policy gate, and only an authorised delegate can
decide in someone's place.

**When more than one row matches** (Z3 A, in his words: "combine approval requirements
without duplicating people unnecessarily; if several rows match, preserve all required
approval stages and their order; a duplicated role should not produce redundant approvals
unless the matrix explicitly requires separate stages"), the rows are merged rather than
picked between:

  - every matching row's stages are kept, so adding a row can only add approval;
  - the orders the rows ask for are all honoured together, by sorting the stages so that
    each row's own sequence survives the merge;
  - a role two rows both ask for is one stage, unless a row sets `separate_stages`, which
    says that row wants a stage of its own even so;
  - two rows that order the same pair of roles against each other contradict, which
    check() refuses at save time and stages_for() reports if it ever gets through.

**The clock** (Z6 A and his refinement): a reminder at two working hours, and after four the
next role up and the company's escalation role are told. Being told is not deciding: the
stage stays with whoever it belongs to, and only Step 5's authorised delegation can move it
(app/approvals/chain.escalation_people already works this way, safeguard 4). Whether the
two-hour median is actually met is measured, not assumed (Z9 A, app/approvals/analytics).

The matrix is never edited (Z4 A): a change is a new version from a date, as rates, cost
models and price policies are, so a quote approved in March can still be explained with
March's rulebook.

This module is only the rulebook and the arithmetic: no database, no cards. The table, the
card and the screens sit on top of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.errors import ValidationFailed

# What a row may ask about (Z1 A). Every condition a row sets must hold for it to match.
CONDITIONS = ("discount_pct_from", "margin_pct_upto", "below_floor", "value_from")
MAX_ROWS = 40
MAX_APPROVERS = 6
# The clock, in working hours (Z6 A). The approvals engine takes the deadline and a
# fraction of it for the reminder, so four hours with a reminder at half gives two.
SLA_HOURS = Decimal("4")
REMINDER_AT = Decimal("0.5")
# What the median turnaround is measured against (Z9 A).
TARGET_MEDIAN_HOURS = Decimal("2")

# What a new company can start from on the setup screen. The roles are named rather than
# identified, because a company's roles are its own: the screen turns each name into one of
# its role ids and leaves a name it cannot find blank for a person to pick.
SAMPLE_MATRIX: dict[str, Any] = {
    "rows": [
        {"when": {"discount_pct_from": "5"}, "approver_names": ["Sales Manager"],
         "note": "Any discount worth the name"},
        {"when": {"discount_pct_from": "15"}, "approver_names": ["Sales Manager", "Finance"],
         "note": "A deep discount goes to Finance as well"},
        {"when": {"below_floor": True}, "approver_names": ["Finance"],
         "note": "Under the family's floor, whatever the discount"},
        {"when": {"value_from": "2500000"}, "approver_names": ["Finance"],
         "note": "A big quote, even at a small discount"},
    ],
}


def _dec(v: Any) -> Decimal | None:
    try:
        d = Decimal(str(v).replace(",", "").replace("₹", "").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None
    return d if d.is_finite() else None


def _num(v: Decimal) -> str:
    return format(v.normalize(), "f")


# What a quote looks like to the matrix ----------------------------------------------------

@dataclass(frozen=True)
class Facts:
    """The three things the matrix measures (Z1 A), as the quote stands.

    `margin_pct` is None when a line's cost is not known, so no margin can be worked out.
    A quote like that is treated as under its floor: it cannot clear a floor it cannot be
    measured against.
    """

    discount_pct: Decimal
    value: Decimal
    margin_pct: Decimal | None = None
    floor_margin_pct: Decimal | None = None

    @property
    def below_floor(self) -> bool:
        if self.floor_margin_pct is None:
            return False
        return self.margin_pct is None or self.margin_pct < self.floor_margin_pct

    def out(self) -> dict[str, Any]:
        return {"discount_pct": _num(self.discount_pct), "value": _num(self.value),
                "margin_pct": _num(self.margin_pct) if self.margin_pct is not None else None,
                "floor_margin_pct": (_num(self.floor_margin_pct)
                                     if self.floor_margin_pct is not None else None),
                "below_floor": self.below_floor}


# The rulebook -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Row:
    """One line of the rulebook: when it applies, and who approves."""

    position: int
    discount_pct_from: Decimal | None
    margin_pct_upto: Decimal | None
    below_floor: bool
    value_from: Decimal | None
    approvers: tuple[str, ...]
    separate_stages: bool
    note: str | None

    def matches(self, f: Facts) -> bool:
        if self.discount_pct_from is not None and f.discount_pct < self.discount_pct_from:
            return False
        if self.value_from is not None and f.value < self.value_from:
            return False
        if self.below_floor and not f.below_floor:
            return False
        if self.margin_pct_upto is not None:
            # No margin at all does not clear a margin test either.
            if f.margin_pct is not None and f.margin_pct > self.margin_pct_upto:
                return False
        return True

    def why(self) -> str:
        """The row in a few words, for the card and the audit record."""
        if self.note:
            return self.note
        parts = []
        if self.discount_pct_from is not None:
            parts.append(f"a discount of {_num(self.discount_pct_from)}% or more")
        if self.below_floor:
            parts.append("a quote under its floor")
        if self.margin_pct_upto is not None:
            parts.append(f"a margin of {_num(self.margin_pct_upto)}% or less")
        if self.value_from is not None:
            parts.append(f"a quote worth {_num(self.value_from)} or more")
        return " and ".join(parts) or "every quote"


def rows_of(data: dict[str, Any]) -> list[Row]:
    """The stored matrix as rows. Anything unreadable is left out rather than guessed."""
    out: list[Row] = []
    for i, r in enumerate(data.get("rows") or [], start=1):
        when = r.get("when") or {}
        approvers = tuple(str(a) for a in (r.get("approvers") or []) if str(a).strip())
        if not approvers:
            continue
        out.append(Row(
            position=i,
            discount_pct_from=_dec(when.get("discount_pct_from")),
            margin_pct_upto=_dec(when.get("margin_pct_upto")),
            below_floor=bool(when.get("below_floor")),
            value_from=_dec(when.get("value_from")),
            approvers=approvers,
            separate_stages=bool(r.get("separate_stages")),
            note=(str(r["note"])[:200] if r.get("note") else None),
        ))
    return out


# Working the chain out --------------------------------------------------------------------

@dataclass
class Stage:
    """One approval the quote needs: a role, and the rows that asked for it."""

    role: str
    rows: list[int] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    own_stage: bool = False  # a row asked for a stage of its own, even for a repeated role

    def out(self) -> dict[str, Any]:
        return {"role": self.role, "rows": list(self.rows), "because": list(self.reasons)}


@dataclass
class Chain:
    """What a quote needs before it can go out, and anything wrong with the rulebook."""

    stages: list[Stage] = field(default_factory=list)
    matched: list[int] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def needed(self) -> bool:
        return bool(self.stages)

    def roles(self) -> list[str]:
        return [s.role for s in self.stages]

    def out(self) -> dict[str, Any]:
        return {"stages": [s.out() for s in self.stages], "rows": list(self.matched),
                "problems": list(self.problems)}


def _order(stages: list[Stage], edges: list[tuple[int, int]]) -> tuple[list[int], list[str]]:
    """The stages in an order that keeps every row's own sequence (Z3).

    A stable topological sort: where two stages are free to go in either order, the one a
    row asked for first goes first, so the same matrix always gives the same chain. Two rows
    that order the same pair against each other cannot both be honoured; that is reported
    and the stages are left in the order they were asked for.
    """
    after: dict[int, set[int]] = {i: set() for i in range(len(stages))}
    for a, b in edges:
        if a != b:
            after[b].add(a)
    order: list[int] = []
    left = set(range(len(stages)))
    while left:
        free = [i for i in sorted(left) if not (after[i] & left)]
        if not free:
            stuck = ", ".join(sorted({stages[i].role for i in left}))
            return list(range(len(stages))), [
                f"the rulebook asks for {stuck} in two different orders, so they cannot all "
                "be honoured: rows "
                + ", ".join(str(p) for p in sorted({p for i in left for p in stages[i].rows}))]
        order.append(free[0])
        left.discard(free[0])
    return order, []


def stages_for(data: dict[str, Any], f: Facts) -> Chain:
    """Every approval a quote needs under this matrix, in order (Z3 A, Z5 A).

    One chain for the whole quote: the discount is the quote's, not a line's, so the lines'
    numbers ride on this one card. A line keeps a card of its own only where Forge could not
    work its price out at all, which is a cost question and goes to whoever manages costs.
    """
    chain = Chain()
    stages: list[Stage] = []
    by_role: dict[str, int] = {}
    edges: list[tuple[int, int]] = []
    for row in rows_of(data):
        if not row.matches(f):
            continue
        chain.matched.append(row.position)
        here: list[int] = []
        for role in row.approvers:
            idx = by_role.get(role)
            if idx is None or row.separate_stages:
                stages.append(Stage(role=role, own_stage=row.separate_stages))
                idx = len(stages) - 1
                # A stage of a row's own is not reused by later rows; a shared one is.
                if not row.separate_stages:
                    by_role[role] = idx
            stages[idx].rows.append(row.position)
            if row.why() not in stages[idx].reasons:
                stages[idx].reasons.append(row.why())
            here.append(idx)
        for a, b in zip(here, here[1:], strict=False):
            edges.append((a, b))
    order, problems = _order(stages, edges)
    chain.stages = [stages[i] for i in order]
    chain.problems = problems
    return chain


# Saving a version -------------------------------------------------------------------------

def check(data: dict[str, Any]) -> dict[str, Any]:
    """A matrix as it will be stored, or what is wrong with it, row by row.

    Roles are checked for shape only; that each one is a role of this company is checked
    where the version is saved, which is also where a role with nobody in it is called out
    (Z2 A).
    """
    problems: list[str] = []
    rows_in = data.get("rows") or []
    if not isinstance(rows_in, list):
        raise ValidationFailed(["rows: a list of rows is needed"])
    if len(rows_in) > MAX_ROWS:
        problems.append(f"rows: up to {MAX_ROWS}")
        rows_in = rows_in[:MAX_ROWS]
    out_rows: list[dict[str, Any]] = []
    for i, r in enumerate(rows_in, start=1):
        if not isinstance(r, dict):
            problems.append(f"row {i}: a row is needed")
            continue
        when_in = r.get("when") or {}
        when: dict[str, Any] = {}
        for key, word, top in (("discount_pct_from", "discount from", Decimal(100)),
                               ("margin_pct_upto", "margin up to", Decimal(100))):
            if when_in.get(key) in (None, ""):
                continue
            v = _dec(when_in[key])
            if v is None or not (0 <= v <= top):
                problems.append(f"row {i}, {word}: a percentage from 0 to {top} is needed")
            else:
                when[key] = _num(v)
        if when_in.get("value_from") not in (None, ""):
            v = _dec(when_in["value_from"])
            if v is None or v < 0:
                problems.append(f"row {i}, quote worth: an amount from 0 up is needed")
            else:
                when["value_from"] = _num(v)
        if when_in.get("below_floor"):
            when["below_floor"] = True
        if not when:
            problems.append(f"row {i}: say when the row applies, or it would catch every "
                            "quote")
        approvers = [str(a).strip() for a in (r.get("approvers") or []) if str(a).strip()]
        if not approvers:
            problems.append(f"row {i}: at least one approving role is needed")
        elif len(approvers) > MAX_APPROVERS:
            problems.append(f"row {i}: up to {MAX_APPROVERS} approving roles")
            approvers = approvers[:MAX_APPROVERS]
        if len(approvers) != len(set(approvers)):
            problems.append(f"row {i}: the same role is in this row twice")
            seen: set[str] = set()
            approvers = [a for a in approvers if not (a in seen or seen.add(a))]
        row: dict[str, Any] = {"when": when, "approvers": approvers}
        if r.get("separate_stages"):
            row["separate_stages"] = True
        if r.get("note"):
            row["note"] = str(r["note"])[:200]
        out_rows.append(row)
    problems.extend(_contradictions(out_rows))
    if problems:
        raise ValidationFailed(problems)
    return {"rows": out_rows}


def _contradictions(rows: list[dict[str, Any]]) -> list[str]:
    """Two rows that order the same pair of roles against each other (Z3).

    Caught when the version is saved rather than when a quote happens to match both, so a
    company is never left with a rulebook that cannot be honoured.
    """
    said: dict[tuple[str, str], int] = {}
    out: list[str] = []
    for i, r in enumerate(rows, start=1):
        roles = r["approvers"]
        for a_i, a in enumerate(roles):
            for b in roles[a_i + 1:]:
                said.setdefault((a, b), i)
                first = said.get((b, a))
                if first is not None:
                    out.append(f"rows {first} and {i} ask for the same two roles in "
                               "opposite orders")
    return sorted(set(out))
