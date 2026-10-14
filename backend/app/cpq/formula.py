"""Formulas in cost models (F3.2, decision C3 A). Pure code: no database, no AI, and no
Python eval, so a formula can only do arithmetic, compare, and look values up.

A formula is written the way people write them in Excel:

    lookup(weights, size, class) * 1.05
    if(material == "F316", 410, 95)
    max(2, ceil(qty / 50))

It can use the configuration's attributes (size, class, ...), the quantity (qty), values
named earlier in the same model, and the model's own tables through lookup(table, key,
...). Numbers are exact decimals, so the same configuration gives the same cost every
time. A choice whose value looks like a number (size "150") can be used as one.
"""

from __future__ import annotations

import ast
import keyword
import re
from dataclasses import dataclass
from decimal import (
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_UP,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    localcontext,
)
from typing import Any

MAX_LENGTH, MAX_NODES = 1000, 300
NAME = re.compile(r"^[a-z][a-z0-9_]{0,29}$")
FUNCS = {"min": (1, 20), "max": (1, 20), "round": (1, 2), "ceil": (1, 2), "floor": (1, 2),
         "abs": (1, 1), "iif": (3, 3), "lookup": (2, 6)}
_SINGLE_EQ = re.compile(r"(?<![=!<>])=(?!=)")
_TOKEN = re.compile(r"""("[^"]*"|'[^']*')|([A-Za-z_][A-Za-z0-9_]*)""")
_KEEP = {"and", "or", "not", "True", "False"}
_KW = "kw__"  # Python words (class, in, for ...) can be attribute names


def _prepare(text: str) -> str:
    """Python's own words (class, for, in ...) are fine as names in a formula, and if(...)
    is the if function; text in quotes is left as it is."""
    def word(m: re.Match[str]) -> str:
        if m.group(1) or not keyword.iskeyword(m.group(2)) or m.group(2) in _KEEP:
            return m.group(0)
        if m.group(2) == "if":
            return "iif"
        return _KW + m.group(2)
    return _TOKEN.sub(word, text)
Value = Decimal | str | bool


class FormulaError(Exception):
    """A formula that can't be read or worked out; the message says why, in words."""


def num(v: Decimal) -> str:
    """40.000 -> "40", 12.50 -> "12.5"."""
    s = format(v.normalize(), "f")
    return "0" if s == "-0" else s


def _number(v: Value, what: str) -> Decimal:
    if isinstance(v, bool):
        raise FormulaError(f"{what} is yes/no, not a number")
    if isinstance(v, Decimal):
        return v
    try:
        d = Decimal(v.strip())
    except (InvalidOperation, ValueError):
        raise FormulaError(f"{what} is the text {v!r}, not a number") from None
    if not d.is_finite():
        raise FormulaError(f"{what} is not a number")
    return d


def key_text(v: Value) -> str:
    """How a value is written as a table key: numbers without trailing zeros."""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, Decimal):
        return num(v)
    try:
        return num(Decimal(v.strip()))
    except (InvalidOperation, ValueError):
        return v.strip()


@dataclass(frozen=True)
class Table:
    """A small lookup table, for example weight (kg) by size and class."""

    name: str
    keys: tuple[str, ...]
    rows: dict[tuple[str, ...], Decimal]

    @staticmethod
    def parse(name: str, raw: Any, problems: list[str]) -> Table | None:
        if not NAME.match(name or ""):
            problems.append(f"Table name {name!r} must start with a letter and use only small "
                            "letters, digits and _")
            return None
        if not isinstance(raw, dict):
            problems.append(f"Table {name} is not a table")
            return None
        keys = tuple(str(k).strip() for k in raw.get("keys") or [])
        if not 1 <= len(keys) <= 5 or not all(NAME.match(k) for k in keys):
            problems.append(f"Table {name} needs 1 to 5 key columns, each named like an "
                            "attribute")
            return None
        rows: dict[tuple[str, ...], Decimal] = {}
        for i, r in enumerate(raw.get("rows") or [], start=1):
            k = r.get("key") if isinstance(r, dict) else None
            if not isinstance(k, list) or len(k) != len(keys):
                problems.append(f"Table {name}, row {i}: needs {len(keys)} key "
                                f"{'value' if len(keys) == 1 else 'values'}")
                continue
            kt = tuple(key_text(str(x)) for x in k)
            try:
                v = Decimal(str(r.get("value")).strip())
                assert v.is_finite()
            except (InvalidOperation, ValueError, AssertionError):
                problems.append(f"Table {name}, row {i} ({', '.join(kt)}): the value "
                                f"{r.get('value')!r} is not a number")
                continue
            if kt in rows:
                problems.append(f"Table {name} has {', '.join(kt)} twice")
                continue
            rows[kt] = v
        if len(rows) > 5000:
            problems.append(f"Table {name} has more than 5000 rows")
        return Table(name, keys, rows)

    def get(self, key: tuple[Value, ...]) -> Decimal:
        kt = tuple(key_text(k) for k in key)
        if len(kt) != len(self.keys):
            raise FormulaError(f"lookup({self.name}, ...) needs {len(self.keys)} keys "
                               f"({', '.join(self.keys)})")
        v = self.rows.get(kt)
        if v is None:
            where = ", ".join(f"{k} {x}" for k, x in zip(self.keys, kt, strict=True))
            raise FormulaError(f"Table {self.name} has no row for {where}")
        return v

    def dump(self) -> dict[str, Any]:
        return {"keys": list(self.keys),
                "rows": [{"key": list(k), "value": num(v)} for k, v in self.rows.items()]}


class Formula:
    """One formula, read and checked once, worked out many times."""

    def __init__(self, text: str):
        self.text = str(text or "").strip()
        if not self.text:
            raise FormulaError("The formula is empty")
        if len(self.text) > MAX_LENGTH:
            raise FormulaError(f"The formula is longer than {MAX_LENGTH} characters")
        bare = _TOKEN.sub(lambda m: '""' if m.group(1) else m.group(0), self.text)
        if _SINGLE_EQ.search(bare.replace("<=", "").replace(">=", "")):
            raise FormulaError("Use == to compare (a single = is not allowed)")
        source = _prepare(self.text)
        try:
            tree = ast.parse(source, mode="eval")
        except SyntaxError:
            raise FormulaError("The formula can't be read: check the brackets, commas and "
                               "quotes") from None
        self.names: set[str] = set()
        self.tables: dict[str, int] = {}  # table -> number of keys used
        self._count = 0
        self._check(tree.body)
        self._tree = tree.body

    # Reading ----------------------------------------------------------------------------

    def _check(self, n: ast.AST) -> None:
        self._count += 1
        if self._count > MAX_NODES:
            raise FormulaError("The formula is too long; split it into named values")
        if isinstance(n, ast.Constant):
            if not isinstance(n.value, (int, float, str, bool)) or n.value is None:
                raise FormulaError("The formula has something that is not a number or text")
            return
        if isinstance(n, ast.Name):
            n.id = n.id.removeprefix(_KW)
            if not NAME.match(n.id):
                hint = (f"; names are written in small letters: {n.id.lower()}"
                        if NAME.match(n.id.lower()) else "")
                raise FormulaError(f"{n.id!r} is not a name a formula can use{hint}")
            self.names.add(n.id)
            return
        if isinstance(n, ast.BinOp):
            if not isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
                raise FormulaError("Only + - * / and ** work in formulas")
            self._check(n.left)
            self._check(n.right)
            return
        if isinstance(n, ast.UnaryOp):
            if not isinstance(n.op, (ast.USub, ast.UAdd, ast.Not)):
                raise FormulaError("The formula uses an operation that is not allowed")
            self._check(n.operand)
            return
        if isinstance(n, ast.BoolOp):
            for v in n.values:
                self._check(v)
            return
        if isinstance(n, ast.Compare):
            if not all(isinstance(o, (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq))
                       for o in n.ops):
                raise FormulaError("Compare with < <= > >= == or != only")
            self._check(n.left)
            for c in n.comparators:
                self._check(c)
            return
        if isinstance(n, ast.Call):
            if not isinstance(n.func, ast.Name) or n.keywords:
                raise FormulaError("Only these functions work: " + ", ".join(_func_names()))
            name = n.func.id.lower()
            name = "iif" if name == "if" else name
            if name not in FUNCS:
                raise FormulaError(f"{n.func.id}() is not a function formulas know; use "
                                   + ", ".join(_func_names()))
            lo, hi = FUNCS[name]
            if not lo <= len(n.args) <= hi:
                raise FormulaError(f"{'if' if name == 'iif' else name}() takes "
                                   + (f"{lo}" if lo == hi else f"{lo} to {hi}")
                                   + f" values, not {len(n.args)}")
            n.func.id = name
            args = n.args
            if name == "lookup":
                if not isinstance(args[0], ast.Name):
                    raise FormulaError("lookup() starts with a table's name, for example "
                                       "lookup(weights, size, class)")
                args[0].id = args[0].id.removeprefix(_KW)
                self.tables[args[0].id] = len(args) - 1
                args = args[1:]
            for a in args:
                self._check(a)
            return
        raise FormulaError("The formula uses something that is not allowed (only numbers, "
                           "text in quotes, names, + - * / **, comparisons and functions)")

    # Working out ------------------------------------------------------------------------

    def eval(self, env: dict[str, Value], tables: dict[str, Table]) -> Value:
        with localcontext() as ctx:
            ctx.prec = 28
            ctx.traps[DivisionByZero] = True
            ctx.traps[InvalidOperation] = True
            try:
                return self._eval(self._tree, env, tables)
            except (DivisionByZero, ZeroDivisionError):
                raise FormulaError("It divides by zero") from None
            except InvalidOperation:
                raise FormulaError("It gives a number that can't be worked out") from None

    def number(self, env: dict[str, Value], tables: dict[str, Table]) -> Decimal:
        return _number(self.eval(env, tables), "The result")

    def _eval(self, n: ast.AST, env: dict[str, Value], tables: dict[str, Table]) -> Value:
        if isinstance(n, ast.Constant):
            v = n.value
            if isinstance(v, bool) or isinstance(v, str):
                return v
            return Decimal(repr(v)) if isinstance(v, float) else Decimal(v)
        if isinstance(n, ast.Name):
            if n.id not in env:
                raise FormulaError(f"{n.id} has no value here")
            return env[n.id]
        if isinstance(n, ast.BinOp):
            a = _number(self._eval(n.left, env, tables), _src(n.left))
            b = _number(self._eval(n.right, env, tables), _src(n.right))
            if isinstance(n.op, ast.Add):
                return a + b
            if isinstance(n.op, ast.Sub):
                return a - b
            if isinstance(n.op, ast.Mult):
                return a * b
            if isinstance(n.op, ast.Div):
                if b == 0:
                    raise FormulaError(f"It divides by zero ({_src(n.right)} is 0)")
                return a / b
            if b != b.to_integral_value() or abs(b) > 10:
                raise FormulaError("Powers must be whole numbers from -10 to 10")
            return a ** int(b)
        if isinstance(n, ast.UnaryOp):
            v = self._eval(n.operand, env, tables)
            if isinstance(n.op, ast.Not):
                return not _truth(v)
            d = _number(v, _src(n.operand))
            return -d if isinstance(n.op, ast.USub) else d
        if isinstance(n, ast.BoolOp):
            if isinstance(n.op, ast.And):
                return all(_truth(self._eval(v, env, tables)) for v in n.values)
            return any(_truth(self._eval(v, env, tables)) for v in n.values)
        if isinstance(n, ast.Compare):
            left = self._eval(n.left, env, tables)
            for op, c in zip(n.ops, n.comparators, strict=True):
                right = self._eval(c, env, tables)
                if not _compare(op, left, right):
                    return False
                left = right
            return True
        if isinstance(n, ast.Call):
            assert isinstance(n.func, ast.Name)
            name = n.func.id
            if name == "iif":
                cond = _truth(self._eval(n.args[0], env, tables))
                return self._eval(n.args[1] if cond else n.args[2], env, tables)
            if name == "lookup":
                assert isinstance(n.args[0], ast.Name)
                t = tables.get(n.args[0].id)
                if t is None:
                    raise FormulaError(f"There is no table called {n.args[0].id}")
                return t.get(tuple(self._eval(a, env, tables) for a in n.args[1:]))
            args = [_number(self._eval(a, env, tables), _src(a)) for a in n.args]
            if name == "min":
                return min(args)
            if name == "max":
                return max(args)
            if name == "abs":
                return abs(args[0])
            if name == "round":
                places = int(args[1]) if len(args) > 1 else 0
                if not -6 <= places <= 10:
                    raise FormulaError("round() keeps from -6 to 10 decimal places")
                return args[0].quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
            step = args[1] if len(args) > 1 else Decimal(1)
            if step <= 0:
                raise FormulaError(f"{name}() needs a step above 0")
            q = (args[0] / step).to_integral_value(
                rounding=ROUND_CEILING if name == "ceil" else ROUND_FLOOR)
            return q * step
        raise FormulaError("The formula uses something that is not allowed")


def _func_names() -> list[str]:
    return ["if" if f == "iif" else f for f in FUNCS]


def _src(n: ast.AST) -> str:
    try:
        return ast.unparse(n).replace("iif(", "if(")
    except Exception:  # noqa: BLE001
        return "a value"


def _truth(v: Value) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, Decimal):
        return v != 0
    return bool(v.strip())


def _compare(op: ast.cmpop, a: Value, b: Value) -> bool:
    # Numbers compare as numbers, even when one side is a choice like "150".
    if not isinstance(a, bool) and not isinstance(b, bool):
        try:
            x, y = _number(a, ""), _number(b, "")
        except FormulaError:
            pass
        else:
            if isinstance(op, ast.Lt):
                return x < y
            if isinstance(op, ast.LtE):
                return x <= y
            if isinstance(op, ast.Gt):
                return x > y
            if isinstance(op, ast.GtE):
                return x >= y
            return (x == y) == isinstance(op, ast.Eq)
    if not isinstance(op, (ast.Eq, ast.NotEq)):
        raise FormulaError("Only numbers can be compared with < <= > >=")
    return (key_text(a) == key_text(b)) == isinstance(op, ast.Eq)
