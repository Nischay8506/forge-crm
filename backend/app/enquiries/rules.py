"""Reading an email by rules, without the AI (the fallback in app/enquiries/reader.py).

Used when no AI key is set yet (open question D3), when the AI budget is spent, or
when the AI's answers could not be used. It labels by keywords in English, Hindi and
Telugu, picks out lines that carry a quantity and a unit, and finds the sender's
phone, company and place in the signature. It is careful rather than clever: what
it reads is always marked for a person to check.
"""

import re
from datetime import date
from typing import Any

from app.language.script import detect
from app.steward.identifiers import normalize_email, normalize_phone

KEYWORDS: dict[str, tuple[str, ...]] = {
    "enquiry": (
        "rfq", "rfi", "enquiry", "inquiry", "quotation", "quote", "best rate", "best price",
        "your rates", "rate for", "price for", "requirement", "we require", "we need",
        "kindly send", "please send your offer", "offer for", "techno-commercial", "tender",
        "bid", "boq", "bill of quantities", "price list", "budgetary",
        "कोटेशन", "भाव", "रेट", "जरूरत", "ज़रूरत", "आवश्यकता", "चाहिए", "दाम",
        "కొటేషన్", "ధర", "రేటు", "అవసరం", "కావాలి",
    ),
    "complaint": (
        "complaint", "defect", "defective", "damaged", "rejected", "rejection", "not as per",
        "shortage", "wrong material", "crack", "rusted", "rust", "poor quality", "not acceptable",
        "replace the", "replacement", "शिकायत", "खराब", "टूटा", "ఫిర్యాదు", "పాడైన", "తప్పు",
    ),
    "payment": (
        "payment", "paid", "remittance", "utr", "neft", "rtgs", "imps", "transferred",
        "credit note", "debit note", "outstanding", "ledger", "account statement", "tds",
        "भुगतान", "पेमेंट", "చెల్లింపు", "పేమెంట్",
    ),
    "order_status": (
        "order status", "status of our order", "status of po", "our po", "purchase order no",
        "dispatch details", "dispatch status", "when will", "tracking", "lr copy", "vehicle no",
        "e-way bill", "eway bill", "delivery status", "material not received",
        "डिस्पैच", "ऑर्डर का स्टेटस", "ఆర్డర్ స్టేటస్", "డిస్పాచ్",
    ),
    "vendor_mail": (
        "we are pleased to introduce", "we are a leading", "company profile", "our products",
        "we offer", "we would like to offer", "our catalogue", "our catalog", "introduce our",
        "manufacturers and suppliers of", "dealers in", "vendor registration",
        "approved vendor", "vendor list", "revised price list", "book your orders",
    ),
    "spam": (
        "lottery", "you have won", "bitcoin", "crypto", "seo services", "click here",
        "increase your ranking", "dear friend", "unsubscribe", "limited time offer",
        "work from home", "loan approved", "lucky draw", "claim your", "b2b leads",
        "verified leads",
    ),
}

UNITS = {
    "mt": "MT", "m.t.": "MT", "m.t": "MT", "ton": "MT", "tons": "MT", "tonne": "MT",
    "tonnes": "MT", "t": "MT", "kg": "kg", "kgs": "kg", "no": "nos", "nos": "nos",
    "no.": "nos", "nos.": "nos", "numbers": "nos", "pcs": "nos", "pc": "nos",
    "pieces": "nos", "piece": "nos", "set": "sets", "sets": "sets", "m": "m", "mtr": "m",
    "mtrs": "m", "meter": "m", "meters": "m", "metre": "m", "metres": "m", "rmt": "m",
    "sheet": "nos", "sheets": "nos", "plates": "nos", "unit": "nos", "units": "nos",
    "lot": "lot", "lots": "lot", "ltr": "l", "litres": "l",
}
_UNIT_RX = "|".join(sorted((re.escape(u) for u in UNITS), key=len, reverse=True))
QTY_UNIT = re.compile(rf"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*({_UNIT_RX})(?![\w])", re.IGNORECASE)
NUMBER = re.compile(r"^\d[\d,]*(?:\.\d+)?$")
STANDARD = re.compile(
    r"\b(IS\s?:?\s?\d{3,5}(?:\s?[-:]?\s?(?:E\s?\d{3}\w*|Gr(?:ade)?\.?\s?\w+|Part\s?\d+))?|"
    r"ASTM\s?A?\s?\d+\w*|EN\s?10\d{3}[-\w]*|SA\s?\d{2,3}\w*|DIN\s?\d+|JIS\s?[A-Z]?\s?\d+|"
    r"BS\s?\d{3,5}|API\s?5L\w*)", re.IGNORECASE)
GRADE = re.compile(
    r"\b(E\s?(?:250|275|300|350|410|450)\s?(?:BR|BO|B0|A|B|C)?|Fe\s?\d{3}D?|SS\s?\d{3}L?|"
    r"EN\s?\d{1,2}[A-Z]?|SAE\s?\d{4}|42CrMo4|C\d{2}|FG\s?\d{3}|SG\s?\d{3}/\d+|"
    r"SAILMA\s?\d*|S\d{3}JR|S355\w*|A36|A516\s?Gr\.?\s?\d+)\b", re.IGNORECASE)
DIMENSION = re.compile(
    r"(\d+(?:\.\d+)?\s*(?:mm|cm|m|\")?\s*[xX×*]\s*\d+(?:\.\d+)?\s*(?:mm|cm|m|\")?"
    r"(?:\s*[xX×*]\s*\d+(?:\.\d+)?\s*(?:mm|cm|m|\")?)?|"
    r"\b(?:ISMB|ISMC|ISJB|ISLB|NPB|WPB|ISA|HEB|UB|UC)\s?\d+(?:\s?[xX×*]\s?\d+){0,2}|"
    r"\b\d+(?:\.\d+)?\s*mm\s*(?:thk|thick|dia|od|id)?\b|\b(?:dia|od|nb)\s*\d+(?:\.\d+)?\s*(?:mm)?)",
    re.IGNORECASE)
PHONE = re.compile(r"(?:\+?91[\s-]?)?(?<!\d)[6-9]\d{4}[\s-]?\d{5}(?!\d)")
COMPANY = re.compile(
    r"\b(?:pvt\.?|private|ltd\.?|limited|llp|industries|enterprises|engineering|engineers|"
    r"corporation|steels?|works|infra|projects|constructions?|fabricators?|udyog|"
    r"company|& co\.?)\b", re.IGNORECASE)
DESIGNATION = re.compile(
    r"\b(purchase|procurement|buyer|sourcing|materials?|stores?|project)\s*"
    r"(?:manager|head|officer|executive|engineer|in-charge|incharge|dept\.?|department)?|"
    r"\b(manager|director|proprietor|partner|owner|ceo|md|gm|agm|dgm|engineer|executive)\b",
    re.IGNORECASE)
TENDER = re.compile(r"\b(tender|e-?tender|nit|gem bid|bid no|bid number|rfp|eoi)\b",
                    re.IGNORECASE)
TENDER_REF = re.compile(
    r"\b(?:tender|nit|bid|rfp|enquiry|rfq)\s*(?:no\.?|number|ref\.?|reference|id|#)\s*[:\-]?\s*"
    r"([A-Z0-9][A-Z0-9/\-._]{3,40})|\b(GEM/\d{4}/[A-Z]/\d+)", re.IGNORECASE)
LOCATION = re.compile(
    r"\b(?:delivery (?:at|to|location|address|place)|deliver(?:ed)? (?:at|to)|ship to|"
    r"site|destination|consignee address)\s*[:\-]?\s*([^\n]{3,120})", re.IGNORECASE)
WHEN = re.compile(
    r"\b(?:required by|needed by|require(?:d)? (?:it )?by|delivery by|before|latest by|"
    r"by|within|delivery within|delivery in|required within)\s+"
    r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}(?:,?\s+\d{4})?|"
    r"\d{1,3}\s+(?:working\s+)?(?:days|weeks|months)|[A-Za-z]{3,9}\s+end|end of [A-Za-z]{3,9})",
    re.IGNORECASE)
MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
QUOTED = re.compile(r"^(>|On .{5,120}wrote:|-----\s*Original Message|From: .+@)", re.IGNORECASE)

STATES = (
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat",
    "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh",
    "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab",
    "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh",
    "Uttarakhand", "West Bengal", "Delhi", "Jammu and Kashmir", "Ladakh", "Puducherry",
    "Chandigarh",
)
CITIES = {
    "Hyderabad": "Telangana", "Secunderabad": "Telangana", "Warangal": "Telangana",
    "Visakhapatnam": "Andhra Pradesh", "Vizag": "Andhra Pradesh", "Vijayawada": "Andhra Pradesh",
    "Guntur": "Andhra Pradesh", "Nellore": "Andhra Pradesh", "Tirupati": "Andhra Pradesh",
    "Kakinada": "Andhra Pradesh", "Chennai": "Tamil Nadu", "Coimbatore": "Tamil Nadu",
    "Hosur": "Tamil Nadu", "Madurai": "Tamil Nadu", "Trichy": "Tamil Nadu",
    "Bengaluru": "Karnataka", "Bangalore": "Karnataka", "Mysuru": "Karnataka",
    "Belgaum": "Karnataka", "Hubli": "Karnataka", "Mumbai": "Maharashtra",
    "Pune": "Maharashtra", "Nagpur": "Maharashtra", "Nashik": "Maharashtra",
    "Aurangabad": "Maharashtra", "Thane": "Maharashtra", "Ahmedabad": "Gujarat",
    "Surat": "Gujarat", "Vadodara": "Gujarat", "Rajkot": "Gujarat", "Jamnagar": "Gujarat",
    "Kolkata": "West Bengal", "Durgapur": "West Bengal", "Delhi": "Delhi",
    "New Delhi": "Delhi", "Noida": "Uttar Pradesh", "Ghaziabad": "Uttar Pradesh",
    "Kanpur": "Uttar Pradesh", "Lucknow": "Uttar Pradesh", "Gurugram": "Haryana",
    "Gurgaon": "Haryana", "Faridabad": "Haryana", "Ludhiana": "Punjab", "Jaipur": "Rajasthan",
    "Indore": "Madhya Pradesh", "Bhopal": "Madhya Pradesh", "Raipur": "Chhattisgarh",
    "Bhilai": "Chhattisgarh", "Jamshedpur": "Jharkhand", "Ranchi": "Jharkhand",
    "Bhubaneswar": "Odisha", "Rourkela": "Odisha", "Kochi": "Kerala", "Patna": "Bihar",
    "Guwahati": "Assam", "Dehradun": "Uttarakhand", "Goa": "Goa",
}


def _lower(text: str) -> str:
    return text.lower()


def label_scores(subject: str, text: str) -> dict[str, int]:
    s, t = _lower(subject), _lower(text)
    out = {}
    for label, words in KEYWORDS.items():
        out[label] = sum(2 for w in words if w in s) + sum(1 for w in words if w in t)
    return out


def own_text(text: str) -> str:
    """The sender's own words, without the quoted conversation below them."""
    lines = []
    for line in (text or "").splitlines():
        if QUOTED.match(line.strip()):
            break
        lines.append(line)
    return "\n".join(lines)


def _number(v: str) -> float | None:
    try:
        return float(v.replace(",", ""))
    except ValueError:
        return None


def _line_from_cells(cells: list[str]) -> dict[str, Any] | None:
    """A table row: 'S.No | Item | Size | Grade | Qty | Unit'."""
    qty, unit, rest = None, None, []
    # A number with the unit in the next cell is the quantity; a cell like '12m' is
    # only taken when there is no such pair (it is more often the size).
    pair = next((i for i, c in enumerate(cells[:-1]) if NUMBER.match(c)
                 and cells[i + 1].lower().strip(". ") in UNITS), None)
    for i, c in enumerate(cells):
        if i == pair:
            qty, unit = _number(c), UNITS[cells[i + 1].lower().strip(". ")]
            cells[i + 1] = ""
            continue
        m = QTY_UNIT.fullmatch(c.strip()) if qty is None and pair is None else None
        if m:
            qty, unit = _number(m.group(1)), UNITS[m.group(2).lower()]
            continue
        rest.append(c)
    if qty is None:
        return None
    if rest and NUMBER.match(rest[0]) and len(rest) > 1:
        rest = rest[1:]  # the row number
    desc = " ".join(c for c in rest if c).strip()
    return _line(desc, qty, unit) if desc else None


def _line(desc: str, qty: float | None, unit: str | None) -> dict[str, Any] | None:
    desc = re.sub(r"^\s*(?:\d{1,3}[.)]|[-•*]|item\s*\d+\s*[:.-])\s*", "", desc,
                  flags=re.IGNORECASE)
    desc = re.sub(r"\s*[-–:,|]\s*$", "", desc).strip(" -–:,|")
    if len(re.findall(r"[^\W\d_]", desc)) < 3:
        return None
    std = STANDARD.search(desc)
    grade = GRADE.search(desc)
    dims = DIMENSION.search(desc)
    return {"item_ref": None, "description": desc[:1000],
            "material": grade.group(0).strip() if grade else None,
            "dimensions": dims.group(0).strip() if dims else None,
            "qty": qty, "unit": unit, "standard": std.group(0).strip() if std else None,
            "notes": None, "confidence": 0.5}


def _qty_in(line: str) -> re.Match[str] | None:
    """The quantity in a line of text. A number that is part of a size ('30m x 15m') or
    of a grade ('SS 304 Sheet') is not one. When there are several, the one after a
    dash or 'Qty' wins, then one that is not a length ('Pipe rack 12m - 5 Nos'), then
    the first."""
    names = [g.span() for g in (*GRADE.finditer(line), *STANDARD.finditer(line))]
    found = [m for m in QTY_UNIT.finditer(line)
             if not re.search(r"[xX×*]\s*$", line[:m.start()])
             and not re.match(r"\s*[xX×*]", line[m.end():])
             and not any(a < m.end(1) and m.start(1) < b for a, b in names)]
    if len(found) < 2:
        return found[0] if found else None

    def rank(m: re.Match[str]) -> int:
        marked = re.search(r"(?:[-–:=]|\bqty\.?|\bquantity)\s*$", line[:m.start()],
                           re.IGNORECASE)
        return 2 * bool(marked) + (UNITS[m.group(2).lower()] != "m")
    return max(found, key=rank)  # max keeps the first of equals


def lines_in(text: str, source: str = "email", source_file: str | None = None
             ) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) > 400:
            continue
        cells = [c.strip() for c in re.split(r"\s*\|\s*|\t", line)]
        found = None
        if len(cells) >= 3:
            found = _line_from_cells(cells)
        if found is None:
            m = _qty_in(line)
            if m:
                desc = (line[:m.start()] + " " + line[m.end():]).strip()
                desc = re.sub(r"\b(?:qty|quantity)\s*[:\-]?\s*$", "", desc, flags=re.IGNORECASE)
                found = _line(desc, _number(m.group(1)), UNITS[m.group(2).lower()])
        if found:
            found["source"] = source
            found["source_file"] = source_file
            out.append(found)
        if len(out) >= 200:
            break
    return out


def _signature(text: str) -> list[str]:
    lines = [ln.strip() for ln in own_text(text).splitlines() if ln.strip()]
    return lines[-15:]


def customer_in(text: str, from_address: str | None, from_name: str | None
                ) -> dict[str, Any]:
    sig = _signature(text)
    phone = None
    for ln in reversed(sig):
        m = PHONE.search(ln)
        if m:
            phone = normalize_phone(m.group(0))
            if phone:
                break
    company = next((ln for ln in sig if COMPANY.search(ln) and len(ln) <= 120
                    and "@" not in ln and not PHONE.search(ln)), None)
    designation = None
    for ln in sig:
        m = DESIGNATION.search(ln)
        if m and len(ln) <= 60 and not COMPANY.search(ln):
            designation = ln
            break
    city = state = None
    blob = "\n".join(sig) + "\n" + text[-2000:]
    for c, st in CITIES.items():
        if re.search(rf"\b{re.escape(c)}\b", blob, re.IGNORECASE):
            city, state = c, st
            break
    for st in STATES:
        if re.search(rf"\b{re.escape(st)}\b", blob, re.IGNORECASE):
            state = st
            break
    return {"company": company, "contact_name": (from_name or "").strip() or None,
            "designation": designation, "phone": phone,
            "email": normalize_email(from_address), "city": city, "state": state}


def _parse_date(text: str, today: date) -> date | None:
    m = re.match(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})$", text.strip())
    try:
        if m:
            d, mo, y = (int(x) for x in m.groups())
            return date(y + 2000 if y < 100 else y, mo, d)
        m = re.match(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})(?:,?\s+(\d{4}))?$",
                     text.strip())
        if m and m.group(2)[:3].lower() in MONTHS:
            y = int(m.group(3)) if m.group(3) else today.year
            got = date(y, MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
            return got if m.group(3) or got >= today else date(y + 1, got.month, got.day)
    except ValueError:
        return None
    return None


def read(*, subject: str, body: str, from_address: str | None, from_name: str | None,
         docs: list[Any], families: list[Any], today: date) -> dict[str, Any]:
    """The same shape as the AI's reading (app/enquiries/reader.py)."""
    from app.enquiries.families import match

    own = own_text(body)
    doc_text = "\n".join(d.text or "" for d in docs)
    scores = label_scores(subject or "", own + "\n" + doc_text[:3000])
    lines = lines_in(own)
    for d in docs:
        if d.text:
            lines += lines_in(d.text, "attachment", d.filename)
    lines = lines[:200]
    if lines:
        scores["enquiry"] += 2
    label = max(scores, key=lambda k: (scores[k], k == "enquiry"))
    top = scores[label]
    if top == 0:
        label = "enquiry"
    confidence = min(0.6, 0.3 + 0.1 * top) if top else 0.2
    everything = f"{subject}\n{own}\n{doc_text}"
    tender = TENDER.search(everything) is not None
    ref = TENDER_REF.search(everything)
    when = WHEN.search(own)
    where = LOCATION.search(own)
    given = set()
    if any(ln["material"] or ln["standard"] for ln in lines):
        given.add("grade")
    if any(ln["dimensions"] for ln in lines):
        given.update({"size", "dimensions"})
    if lines:
        given.update({"qty", "product"})
    if any(d.kind in ("pdf", "scan", "image", "cad") for d in docs) or \
            re.search(r"\bdrawing", everything, re.IGNORECASE):
        given.add("drawing")
    if where:
        given.add("delivery_location")
    if when:
        given.add("required_date")
    family = match(everything, families)
    first = lines[0]["description"] if lines else None
    summary = (subject or "").strip() or first
    det = detect(own)
    return {
        "label": label, "confidence": confidence,
        "could_be_enquiry": scores["enquiry"] > 0 or bool(lines),
        "reason": "Read by rules (keywords), not by the AI",
        "language": det.language, "is_tender": tender,
        "tender_ref": (ref.group(1) or ref.group(2)) if ref else None,
        "summary": summary[:300] if summary else None,
        "customer": customer_in(body, from_address, from_name),
        "lines": lines, "target_date": _parse_date(when.group(1), today) if when else None,
        "target_date_text": when.group(0).strip() if when else None,
        "delivery_location": where.group(1).strip() if where else None,
        "special_requirements": None,
        "product_family": family.name if family else None,
        "given": sorted(given),
    }
