"""A customer's answers in a reply (F1.8: "customer answers auto-update requirement
lines without re-typing").

When the customer writes again in an enquiry's thread, what they answer is written
into the enquiry: a quantity, grade, standard or size into the line it is about, the
delivery place and the date needed into the enquiry itself, and the customer's words
next to each question they answered.

- Which line: the one the reply names ("Line 2: 12 MT", "item 1 - E350"), else the
  only line, else the only line that lacks that detail. Otherwise the answer is not
  placed and the owner is asked to put it in (the enquiry is marked for checking).
- Only empty fields are filled. A reply that gives a different value for a field that
  already has one changes nothing: the owner is asked to check it, because the earlier
  value may be the customer's own too.
- With an AI key the reader is given the existing lines and the questions, and says
  which line each answer belongs to (app/enquiries/reader.py); the same two rules
  apply. Without one, the rules below read the reply.
- A line in the reply that is the same item as an existing line counts as answers for
  that line, not as a new line. Without the AI, a new line is added only when it
  names one of the company's product families, so leftovers of an answer
  ("Qty , delivery at Patancheru") never become items.
- A filled field counts as read (the line's `original` gets it too), so a later
  correction by a person still teaches the reader only what it really misread.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from rapidfuzz import fuzz

from app.enquiries import rules
from app.models import Enquiry, EnquiryLine

LINE_ANSWERS = ("qty", "unit", "material", "dimensions", "standard")
DETAIL_ANSWERS = ("delivery_location", "target_date_text", "target_date")
# The questions each detail answers (app/enquiries/families.py).
KEYS: dict[str, tuple[str, ...]] = {
    "qty": ("qty",), "material": ("grade",), "standard": ("grade",),
    "dimensions": ("size", "dimensions"), "delivery_location": ("delivery_location",),
    "target_date": ("required_date",), "target_date_text": ("required_date",),
}
FIELD_WORDS = {"qty": "quantity", "unit": "unit", "material": "grade",
               "dimensions": "size", "standard": "standard",
               "delivery_location": "delivery place", "target_date": "date needed",
               "target_date_text": "date needed"}
SAME_ITEM = 85  # as app/enquiries/dedupe.py: two lines name the same item

REF = re.compile(r"\b(?:line|item|sr\.?\s*no\.?|s\.?\s*no\.?|sl\.?\s*no\.?)\s*[#:.-]?\s*"
                 r"(\d{1,3})\b(?!\s*(?:" + rules._UNIT_RX + r")\b)", re.IGNORECASE)
QTY_WORD = re.compile(
    r"\b(?:qty|quantity|quantities|required qty|मात्रा|परिमाण|పరిమాణం|క్వాంటిటీ)\s*"
    r"(?:is|:|-|–|=)?\s*(\d[\d,]*(?:\.\d+)?)\s*(" + rules._UNIT_RX + r")?(?![\w])",
    re.IGNORECASE)
PLACE = re.compile(r"\b(?:delivery|deliver(?:ed)?|ship(?:ping)?|dispatch)\s*"
                   r"(?:place|location|address|point|site)?\s*(?:is|:|-|–)\s*([^\n]{3,120})",
                   re.IGNORECASE)
DATE_FIELD = re.compile(
    r"\b(?:required|delivery|need(?:ed)?|due)\s*(?:date|by)\s*(?:is|:|-|–)?\s*"
    r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}(?:,?\s+\d{4})?)",
    re.IGNORECASE)
NOT_A_PLACE = re.compile(r"^(?:within|in\s+\d|by\b|before|asap|immediate|urgent|\d)",
                         re.IGNORECASE)


@dataclass
class Found:
    """What a reply answers, before it is placed."""
    by_line: dict[int, dict[str, Any]] = field(default_factory=dict)  # position -> fields
    general: dict[str, Any] = field(default_factory=dict)  # for "the" line, when clear
    details: dict[str, Any] = field(default_factory=dict)  # the enquiry's own details
    words: dict[str, str] = field(default_factory=dict)  # question key -> their words
    new_lines: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Applied:
    filled: list[dict[str, Any]] = field(default_factory=list)
    to_check: list[str] = field(default_factory=list)
    keys: set[str] = field(default_factory=set)  # questions answered
    words: dict[str, str] = field(default_factory=dict)


# --- for the AI reader ------------------------------------------------------------------

def context(enq: Enquiry, lines: list[EnquiryLine]) -> dict[str, Any]:
    """What the reader is told about the enquiry a reply belongs to."""
    return {
        "ref": enq.ref,
        "lines": [{"line": ln.position, "description": ln.description,
                   **{f: _plain(getattr(ln, f)) for f in LINE_ANSWERS}} for ln in lines],
        "questions": [{"key": q["key"], "label": q.get("label")} for q in enq.questions
                      if not q.get("answered_at")],
    }


def _plain(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    return v


# --- reading a reply ------------------------------------------------------------------

def _own(body: str) -> str:
    """The customer's own words: without the quoted conversation and the signature."""
    out = []
    for ln in rules.own_text(body).splitlines():
        if rules.SIGN_OFF.match(ln.strip()):
            break
        out.append(ln)
    return "\n".join(out)


def _fields(seg: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    m = QTY_WORD.search(seg) or rules._qty_in(seg)
    if m:
        out["qty"] = rules._number(m.group(1))
        if m.group(2):
            out["unit"] = rules.UNITS[m.group(2).lower()]
    std = rules.STANDARD.search(seg)
    grade = rules.GRADE.search(seg)
    dims = rules.DIMENSION.search(seg)
    if std:
        out["standard"] = std.group(0).strip()
    if grade:
        out["material"] = grade.group(0).strip()
    if dims and not (m and dims.start() <= m.start(1) < dims.end()):
        out["dimensions"] = dims.group(0).strip()
    return {k: v for k, v in out.items() if v is not None}


def _place(text: str) -> str | None:
    m = rules.LOCATION.search(text) or PLACE.search(text)
    if not m:
        return None
    v = re.split(r"\.\s|\.$|;|\s{2,}", m.group(1).strip())[0].strip(" .,-–")
    return v if v and not NOT_A_PLACE.match(v) and len(v) >= 3 else None


def by_rules(body: str, questions: list[dict[str, Any]], today: date) -> Found:
    """Read the answers in a reply without the AI."""
    text = _own(body)
    found = Found()
    refs = list(REF.finditer(text))
    if refs:
        head = text[:refs[0].start()]
        for i, r in enumerate(refs):
            seg = text[r.end():refs[i + 1].start() if i + 1 < len(refs) else len(text)]
            seg = seg.split("\n\n")[0]
            got = _fields(seg)
            if got:
                found.by_line.setdefault(int(r.group(1)), {}).update(got)
    else:
        head = text
    found.general = _fields(head) if not refs or head.strip() else {}
    place = _place(text)
    if place:
        found.details["delivery_location"] = place
    when = DATE_FIELD.search(text) or rules.WHEN.search(text)
    if when:
        found.details["target_date_text"] = when.group(0).strip()
        got = rules._parse_date(when.group(1), today)
        if got:
            found.details["target_date"] = got.isoformat()
    # "Finish: hot dip galvanised" answers the question labelled "Finish".
    for q in questions:
        names = {re.escape(str(q.get("label") or "").strip()),
                 re.escape(q["key"].replace("_", " "))}
        rx = re.compile(r"^\s*(?:" + "|".join(n for n in names if n)
                        + r")\s*[:\-–]\s*(.{1,300})$", re.IGNORECASE | re.MULTILINE)
        m = rx.search(text)
        if m:
            found.words[q["key"]] = m.group(1).strip()
    return found


def from_reading(data: dict[str, Any], lines: list[EnquiryLine]) -> Found:
    """The AI's reading of a reply (reader.read with a thread): its updates and answers,
    and its lines, of which the ones that name an existing item become answers."""
    found = Found()
    for u in data.get("updates") or []:
        got = {f: u.get(f) for f in LINE_ANSWERS if u.get(f) not in (None, "")}
        if got:
            found.by_line.setdefault(int(u["line"]), {}).update(got)
    for ln in data.get("lines") or []:
        same = _same_item(ln, lines)
        if same is not None:
            got = {f: ln.get(f) for f in LINE_ANSWERS if ln.get(f) not in (None, "")}
            found.by_line.setdefault(same.position, {}).update(
                {k: v for k, v in got.items() if k not in found.by_line.get(same.position, {})})
        else:
            found.new_lines.append(ln)
    for k in DETAIL_ANSWERS:
        if data.get(k):
            found.details[k] = str(data[k])
    found.words = {k: str(v) for k, v in (data.get("answers") or {}).items() if v}
    return found


def new_lines_by_rules(data: dict[str, Any], lines: list[EnquiryLine], families: list[Any],
                       found: Found) -> None:
    """Lines the rules read in a reply: the same item as an existing line gives answers
    for it; a line naming a product family is new; anything else is left out."""
    from app.enquiries.families import match

    for ln in data.get("lines") or []:
        same = _same_item(ln, lines)
        if same is not None:
            got = {f: ln.get(f) for f in LINE_ANSWERS if ln.get(f) not in (None, "")}
            mine = found.by_line.setdefault(same.position, {})
            mine.update({k: v for k, v in got.items() if k not in mine})
        elif match(" ".join(str(ln.get(f) or "") for f in ("description", "material",
                                                           "dimensions")), families):
            found.new_lines.append(ln)


def _same_item(ln: dict[str, Any], lines: list[EnquiryLine]) -> EnquiryLine | None:
    desc = str(ln.get("description") or "").strip()
    if len(desc) < 4:
        return None
    best = max(lines, key=lambda x: fuzz.token_set_ratio(desc, x.description), default=None)
    if best is not None and fuzz.token_set_ratio(desc, best.description) >= SAME_ITEM:
        return best
    return None


# --- placing them -----------------------------------------------------------------------

def _shown(f: str, v: Any, unit: Any = None) -> str:
    if f == "qty":
        n = rules._number(str(v)) if not isinstance(v, int | float | Decimal) else float(v)
        s = (f"{n:g}" if n is not None else str(v))
        return f"{s} {unit}".strip() if unit else s
    return str(v)


def _differs(old: Any, new: Any) -> bool:
    if old in (None, ""):
        return False
    try:
        return Decimal(str(old)).normalize() != Decimal(str(new)).normalize()
    except ArithmeticError:
        return str(old).strip().lower() != str(new).strip().lower()


def _fill(line: EnquiryLine, got: dict[str, Any], applied: Applied) -> None:
    name = f"line {line.position}"
    qty_unit = got.get("unit")
    for f in ("qty", "material", "dimensions", "standard"):
        if f not in got:
            continue
        v = got[f]
        old = getattr(line, f)
        if f == "qty":
            if v is None:
                continue
            v = Decimal(str(v))
            if v == v.to_integral_value():
                v = v.quantize(Decimal(1))  # 40.0 is 40
            if old is not None:
                if _differs(old, v) or (qty_unit and line.unit and line.unit != qty_unit):
                    applied.to_check.append(
                        f"The customer's reply gives {name} as {_shown(f, v, qty_unit)}, "
                        f"not {_shown(f, old, line.unit)}: check the quantity")
                continue
            line.qty = v
            if qty_unit and not line.unit:
                line.unit = qty_unit
            _read(line, {"qty": str(v), **({"unit": line.unit} if line.unit else {})})
            applied.filled.append({"line": line.position, "field": f,
                                   "value": _shown(f, v, line.unit)})
        else:
            v = str(v)[:300 if f == "dimensions" else 200]
            if old not in (None, ""):
                if _differs(old, v):
                    applied.to_check.append(
                        f"The customer's reply gives the {FIELD_WORDS[f]} of {name} as {v}, "
                        f"not {old}: check it")
                continue
            setattr(line, f, v)
            _read(line, {f: v})
            applied.filled.append({"line": line.position, "field": f, "value": v})
        applied.keys.update(KEYS[f])
        applied.words.setdefault(KEYS[f][0], _shown(f, v, line.unit if f == "qty" else None))
        if f in (line.unsure or []):
            line.unsure = [x for x in line.unsure if x != f]


def _read(line: EnquiryLine, vals: dict[str, Any]) -> None:
    if line.original is not None:
        line.original = {**line.original, **vals}


def apply(enq: Enquiry, lines: list[EnquiryLine], found: Found) -> Applied:
    """Write what a reply answers into the enquiry and its lines (the rules above)."""
    applied = Applied()
    by_pos = {ln.position: ln for ln in lines}
    for pos, got in sorted(found.by_line.items()):
        line = by_pos.get(pos)
        if line is None:
            what = ", ".join(_shown(f, v, got.get("unit")) for f, v in got.items() if f != "unit")
            applied.to_check.append(f"The customer's reply mentions line {pos} ({what}), "
                                    f"which this enquiry does not have: place it by hand")
            continue
        _fill(line, got, applied)
    general = {k: v for k, v in found.general.items()}
    for f in [x for x in ("qty", "material", "dimensions", "standard") if x in general]:
        got = {f: general[f], **({"unit": general["unit"]} if f == "qty" and "unit" in general
                                 else {})}
        if len(lines) == 1:
            _fill(lines[0], got, applied)
            continue
        lacking = [ln for ln in lines if getattr(ln, f) in (None, "")]
        if len(lacking) == 1:
            _fill(lacking[0], got, applied)
        elif lacking:
            applied.to_check.append(
                f"The customer's reply gives the {FIELD_WORDS[f]} "
                f"({_shown(f, general[f], general.get('unit'))}) without saying for which "
                f"line: put it on the right line")
            applied.keys.update(KEYS[f])
    details = dict(enq.details or {})
    for f in DETAIL_ANSWERS:
        v = found.details.get(f)
        if not v:
            continue
        old = details.get(f)
        if old not in (None, ""):
            if f != "target_date_text" and _differs(old, v):
                applied.to_check.append(f"The customer's reply gives the {FIELD_WORDS[f]} as "
                                        f"{v}, not {old}: check it")
            applied.keys.update(KEYS[f])
            continue
        details[f] = str(v)[:300]
        if f != "target_date_text" or not found.details.get("target_date"):
            applied.filled.append({"field": f, "value": details[f]})  # a date, if read
        applied.keys.update(KEYS[f])
        applied.words.setdefault(KEYS[f][0], details[f])
    enq.details = details
    for k, v in found.words.items():
        applied.words[k] = v[:300]
        applied.keys.add(k)
    return applied
