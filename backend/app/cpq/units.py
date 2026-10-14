"""Units for costs (decision K12 A): every quantity and rate is worked out in one
canonical unit per kind of measure, conversions are only the ones listed here, and the
unit a number came in (a rate typed per tonne, a BOM line in grams) is kept next to the
converted value.

    kg    mass       (g, tonne, quintal ...)
    m     length     (mm, cm, km, ft, inch)
    m2    area       (sq ft, sq mm)
    m3    volume     (litre, ml, cu ft)
    nos   count      (each, piece, unit ...)
    hour  time       (minute)

A unit that is not listed (a box, a pair, a set) is only ever the same as itself: Forge
never guesses how many pieces are in a box, so such a conversion makes the cost
incomplete (K10 A) until a person sets it up.
"""

from dataclasses import dataclass
from decimal import Decimal

CANONICAL = ("kg", "m", "m2", "m3", "nos", "hour")
KIND = {"kg": "mass", "m": "length", "m2": "area", "m3": "volume", "nos": "count",
        "hour": "time"}
LABEL = {"kg": "kg", "m": "metre", "m2": "m²", "m3": "m³", "nos": "nos", "hour": "hour"}

_D = Decimal
# unit name (small letters, no dots or spaces) -> (canonical unit, how many canonical units)
_TABLE: dict[str, tuple[str, Decimal]] = {
    **{u: ("kg", _D(1)) for u in ("kg", "kgs", "kilogram", "kilograms", "kilo")},
    **{u: ("kg", _D("0.001")) for u in ("g", "gm", "gms", "gram", "grams")},
    # Not "ton": it is 907 kg in the US and 1016 kg in the UK, so Forge doesn't guess.
    **{u: ("kg", _D(1000)) for u in ("t", "tonne", "tonnes", "mt", "metricton",
                                     "metrictonne")},
    **{u: ("kg", _D(100)) for u in ("quintal", "qtl")},
    **{u: ("m", _D(1)) for u in ("m", "meter", "meters", "metre", "metres", "mtr", "mtrs",
                                 "rm", "runningmetre", "runningmeter")},
    **{u: ("m", _D("0.001")) for u in ("mm", "millimetre", "millimeter")},
    **{u: ("m", _D("0.01")) for u in ("cm", "centimetre", "centimeter")},
    **{u: ("m", _D(1000)) for u in ("km", "kilometre", "kilometer")},
    **{u: ("m", _D("0.3048")) for u in ("ft", "feet", "foot")},
    **{u: ("m", _D("0.0254")) for u in ("in", "inch", "inches")},
    **{u: ("m2", _D(1)) for u in ("m2", "sqm", "sqmtr", "squaremetre", "squaremeter")},
    **{u: ("m2", _D("0.09290304")) for u in ("sqft", "squarefoot", "squarefeet")},
    **{u: ("m2", _D("0.000001")) for u in ("mm2", "sqmm")},
    **{u: ("m3", _D(1)) for u in ("m3", "cum", "cbm", "cubicmetre", "cubicmeter")},
    **{u: ("m3", _D("0.001")) for u in ("l", "litre", "litres", "liter", "liters", "ltr",
                                        "ltrs")},
    **{u: ("m3", _D("0.000001")) for u in ("ml", "millilitre", "milliliter")},
    **{u: ("m3", _D("0.028316846592")) for u in ("cuft", "cft", "cubicfoot", "cubicfeet")},
    **{u: ("nos", _D(1)) for u in ("nos", "no", "number", "numbers", "unit", "units",
                                   "pc", "pcs", "piece", "pieces", "each", "ea")},
    **{u: ("hour", _D(1)) for u in ("hour", "hours", "hr", "hrs", "h")},
    **{u: ("hour", _D(1) / _D(60)) for u in ("min", "mins", "minute", "minutes")},
}


class UnitError(Exception):
    """A unit Forge does not know, or two units that can't be converted; says why."""


@dataclass(frozen=True)
class Unit:
    """A unit as written (`name`) and what one of it is in its canonical unit."""

    name: str
    canonical: str
    factor: Decimal

    @property
    def kind(self) -> str:
        return KIND[self.canonical]


def _key(name: str) -> str:
    return "".join(ch for ch in name.strip().lower() if ch not in " ._")


def parse(name: str | None) -> Unit | None:
    """The unit, or None when Forge doesn't know it."""
    if not name or not name.strip():
        return None
    raw = name.strip()
    k = _key(raw.replace("²", "2").replace("³", "3"))
    hit = _TABLE.get(k)
    return Unit(raw, *hit) if hit else None


def canonical(name: str | None) -> str | None:
    u = parse(name)
    return u.canonical if u else None


def factor(from_unit: str | None, to_unit: str | None) -> Decimal:
    """How many `to_unit` one `from_unit` is. The same name (in any case) is always 1,
    even for units Forge doesn't know (a box is a box)."""
    if from_unit and to_unit and from_unit.strip().lower() == to_unit.strip().lower():
        return Decimal(1)
    a, b = parse(from_unit), parse(to_unit)
    if a is None or b is None:
        unknown = from_unit if a is None else to_unit
        raise UnitError(f"Forge doesn't know the unit {unknown!r}, so it can't convert "
                        f"{from_unit or 'no unit'} to {to_unit or 'no unit'}")
    if a.canonical != b.canonical:
        raise UnitError(f"{from_unit} is a {a.kind} and {to_unit} is a {b.kind}; they can't "
                        "be converted")
    return a.factor / b.factor


def convert(qty: Decimal, from_unit: str | None, to_unit: str | None) -> Decimal:
    return qty * factor(from_unit, to_unit)


def rate_to_canonical(rate: Decimal, unit: str) -> tuple[Decimal, str]:
    """A rate per `unit` as a rate per its canonical unit: ₹62,000 per tonne -> ₹62 per kg."""
    u = parse(unit)
    if u is None:
        raise UnitError(f"Forge doesn't know the unit {unit!r}")
    return rate / u.factor, u.canonical
