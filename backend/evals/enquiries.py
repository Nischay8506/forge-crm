"""Synthetic enquiry test set for M1 (decision N6 A): about 200 emails in English, Hindi,
Telugu and mixed language, with Excel, CSV and PDF bills of material, and the mail a
sales inbox also gets (payments, complaints, order status, vendors, spam, auto-replies).

It measures the Done-When checks the set can show:
  F1.1  >=95% correct classification; every email becomes an enquiry or is kept; <60 s
  F1.4  a 40-line Excel BOM becomes 40 lines with >=90% field accuracy
  F1.8  the acknowledgement is in the customer's language and promises nothing

N6 A: these numbers are re-measured on about 100 real anonymised emails and real BOMs
before M1 is marked done.

Two ways to run it:
  uv run python -m evals.enquiries                  # the rules reader only, no database
  uv run python -m evals.enquiries --pipeline       # everything an email goes through, in
                                                    # a throwaway tenant (the AI when a key
                                                    # is set, otherwise the rules)
  --write evals/data/enquiry_set.jsonl              # also save the set for reading
"""

import argparse
import asyncio
import io
import json
import random
import re
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

# Catalogue ---------------------------------------------------------------------------
# (key, description, size, grade/standard, units it is asked in)
STEEL = [
    ("MS Plate", "MS Plate", "10mm x 1500 x 6300", "IS 2062 E250", ["MT"]),
    ("HR Coil", "HR Coil", "3mm x 1250", "IS 2062 E250", ["MT"]),
    ("ISMB 200", "ISMB 200", None, None, ["MT"]),
    ("ISMC 150", "ISMC 150", None, None, ["MT"]),
    ("TMT Bar", "TMT Bar 12mm", None, "Fe 500D", ["MT"]),
    ("Chequered Plate", "Chequered Plate", "6mm", "IS 3502", ["MT"]),
    ("MS Angle", "MS Angle 65x65x6", None, "IS 2062", ["MT"]),
    ("GI Sheet", "GI Sheet 0.8mm", None, "IS 277", ["nos", "MT"]),
    ("Round Bar", "Round Bar EN8 dia 50", None, "EN8", ["kg", "MT"]),
    ("SS 304 Sheet", "SS 304 Sheet 2mm", "1250 x 2500", "ASTM A240", ["kg", "nos"]),
]
FABRICATION = [
    ("Shed structure", "Shed structure 30m x 15m", None, None, ["sets"]),
    ("Storage tank", "MS storage tank 5 KL", None, "IS 2062", ["nos"]),
    ("Platform", "Platform with handrails", "6m x 2m", None, ["sets", "nos"]),
    ("Pipe rack", "Pipe rack", "12m", None, ["nos"]),
]
MACHINED = [
    ("Flange", "Flange 150 NB", None, "IS 6392", ["nos"]),
    ("Shaft", "Shaft EN24 dia 80 x 600", None, "EN24", ["nos"]),
    ("Bronze bush", "Bronze bush 50 ID", None, None, ["nos"]),
    ("Spur gear", "Spur gear 40T module 3", None, "EN8", ["nos"]),
]
CASTINGS = [
    ("Pump housing", "CI casting pump housing", None, "FG 260", ["nos", "kg"]),
    ("Valve body", "SG iron valve body 100 NB", None, "SG 500/7", ["nos"]),
    ("Impeller", "Impeller SS316 dia 180", None, None, ["nos"]),
]
CATALOGUE = STEEL * 3 + FABRICATION + MACHINED + CASTINGS  # steel is what most ask for

# Names in the script each language is written in.
PEOPLE = {
    "en": [("Ravi Kumar", "Purchase Manager"), ("Anita Desai", "Sr. Buyer"),
           ("Suresh Patil", "Purchase Officer"), ("Meena Rao", "Procurement Head"),
           ("Vikram Shah", "Director"), ("Lakshmi Narayana", "Stores Incharge"),
           ("Imran Khan", "Purchase Executive"), ("Priya Menon", "Buyer")],
    "hi": ["सुरेश अग्रवाल", "राजेश गुप्ता", "अनिल शर्मा", "पूजा वर्मा", "मनोज सिंह"],
    "te": ["లక్ష్మి రెడ్డి", "శ్రీనివాస్ రావు", "వెంకటేశ్వర్లు", "రమేష్ నాయుడు", "పద్మ"],
}
LATIN_FOR = {"सुरेश अग्रवाल": "Suresh Agarwal", "राजेश गुप्ता": "Rajesh Gupta",
             "अनिल शर्मा": "Anil Sharma", "पूजा वर्मा": "Pooja Verma",
             "मनोज सिंह": "Manoj Singh", "లక్ష్మి రెడ్డి": "Lakshmi Reddy",
             "శ్రీనివాస్ రావు": "Srinivas Rao", "వెంకటేశ్వర్లు": "Venkateswarlu",
             "రమేష్ నాయుడు": "Ramesh Naidu", "పద్మ": "Padma"}
COMPANIES = ["Sri Venkateswara Engineering Pvt Ltd", "Kaveri Structurals Pvt Ltd",
             "Deccan Fabricators", "Godavari Infra Projects Ltd", "Shree Balaji Industries",
             "Agarwal Steel Traders", "Reddy Engineering Works", "Bharat Heavy Fab Pvt Ltd",
             "Narmada Pumps Pvt Ltd", "Sai Krishna Constructions", "Mahavir Castings LLP",
             "Precision Machine Tools Pvt Ltd", "Krishna Valves Pvt Ltd",
             "Apex Conveyors Pvt Ltd"]
PLACES = [("Hyderabad", "Telangana"), ("Patancheru", "Telangana"), ("Vijayawada", "Andhra Pradesh"),
          ("Visakhapatnam", "Andhra Pradesh"), ("Pune", "Maharashtra"),
          ("Indore", "Madhya Pradesh"),
          ("Chennai", "Tamil Nadu"), ("Bengaluru", "Karnataka"), ("Ahmedabad", "Gujarat"),
          ("Nagpur", "Maharashtra"), ("Warangal", "Telangana"), ("Raipur", "Chhattisgarh")]
UNIT_WORDS = {"MT": ["MT", "MT", "Tons", "MT"], "kg": ["Kg", "kgs"], "nos": ["Nos", "nos", "Pcs"],
              "sets": ["Sets", "set"], "m": ["Mtrs"]}


@dataclass
class Case:
    id: str
    kind: str  # enquiry | bom_xlsx | bom_csv | bom_pdf | payment | complaint | ...
    language: str  # the language a person would answer in
    subject: str
    body: str
    from_address: str
    from_name: str
    label: str  # expected: enquiry, payment, complaint, order_status, vendor_mail, spam, auto
    lines: list[dict[str, Any]] = field(default_factory=list)
    company: str | None = None
    phone: str | None = None
    is_tender: bool = False
    attachment: dict[str, Any] | None = None  # {"type", "filename", "rows"}
    headers: dict[str, str] = field(default_factory=dict)


def _phone(r: random.Random) -> tuple[str, str]:
    """(as written, as Forge should store it)."""
    n = f"{r.choice('6789')}{r.randrange(10**8, 10**9)}"
    shown = r.choice([f"{n[:5]} {n[5:]}", f"+91-{n}", n, f"+91 {n[:5]} {n[5:]}"])
    return shown, f"+91{n}"


def _qty(r: random.Random, unit: str) -> float:
    if unit == "MT":
        return r.choice([2, 5, 8, 10, 12, 15, 20, 25, 30, 40, 50, 2.5, 7.5])
    if unit == "kg":
        return r.choice([100, 250, 500, 750, 1200, 2000])
    return r.choice([2, 4, 5, 10, 12, 20, 25, 50, 100, 200, 500])


def _items(r: random.Random, n: int) -> list[dict[str, Any]]:
    seen, out = set(), []
    for key, desc, size, grade, units in r.sample(CATALOGUE, k=len(CATALOGUE)):
        if key in seen:
            continue
        if len(out) == n:
            break
        seen.add(key)
        unit = r.choice(units)
        out.append({"key": key, "description": desc, "size": size, "grade": grade,
                    "unit": unit, "qty": _qty(r, unit),
                    "unit_word": r.choice(UNIT_WORDS[unit])})
    return out


def _num(q: float) -> str:
    return str(int(q)) if float(q).is_integer() else str(q)


def _item_text(it: dict[str, Any], style: int) -> str:
    bits = [it["description"]]
    if it["size"]:
        bits.append(it["size"])
    if it["grade"]:
        bits.append(it["grade"])
    what = ", ".join(bits)
    q = f"{_num(it['qty'])} {it['unit_word']}"
    return [f"{what} - {q}", f"{what} : Qty {q}", f"{q} {what}"][style % 3]


def _expected(items: list[dict[str, Any]], size: bool = True, grade: bool = True
              ) -> list[dict[str, Any]]:
    """What the email says about each item (a sentence may leave the size or grade out)."""
    return [{"key": i["key"], "qty": i["qty"], "unit": i["unit"],
             "size": i["size"] if size else None, "grade": i["grade"] if grade else None}
            for i in items]


def _signature(r: random.Random, name: str, title: str | None, company: str, city: str,
               state: str, phone: str, mob_word: str = "Mob") -> str:
    lines = [name] + ([title] if title else []) + [company, f"{city}, {state}",
                                                     f"{mob_word}: {phone}"]
    return "\n".join(lines)


def _address(r: random.Random, name: str, company: str) -> str:
    first = re.sub(r"[^a-z]", "", name.lower().split()[0]) or "buyer"
    dom = re.sub(r"[^a-z]", "", company.lower().split()[0])[:12] or "customer"
    return f"{first}.{r.randrange(10, 99)}@{dom}.example.in"


# Enquiries in each language --------------------------------------------------------

def english(r: random.Random, i: int, tender: bool = False) -> Case:
    name, title = r.choice(PEOPLE["en"])
    company = r.choice(COMPANIES)
    city, state = r.choice(PLACES)
    shown, phone = _phone(r)
    items = _items(r, r.choice([1, 2, 3, 3, 4, 5]))
    style = r.randrange(4)
    if style == 0:
        req = "Please quote your best rates for the following:\n\n" + "\n".join(
            f"{n}. {_item_text(it, n)}" for n, it in enumerate(items, 1))
    elif style == 1 and len(items) == 1:
        it = items[0]
        req = (f"We have a requirement of {_num(it['qty'])} {it['unit_word']} of "
               f"{it['description']}{' ' + it['size'] if it['size'] else ''}"
               f"{', ' + it['grade'] if it['grade'] else ''}. Kindly send your quotation.")
    elif style == 2:
        req = "Kindly send your offer for:\n" + "\n".join(
            f"- {_item_text(it, n)}" for n, it in enumerate(items))
    else:
        rows = ["Sl | Item | Size | Grade | Qty | Unit"] + [
            f"{n} | {it['description']} | {it['size'] or '-'} | {it['grade'] or '-'} | "
            f"{_num(it['qty'])} | {it['unit_word']}" for n, it in enumerate(items, 1)]
        req = "Please quote for the items below.\n\n" + "\n".join(rows)
    extra = []
    if r.random() < 0.7:
        extra.append(f"Delivery at: {city}")
    if r.random() < 0.5:
        extra.append(f"Required by {r.randrange(1, 28):02d}/{r.choice([11, 12])}/2026")
    ref = f"NIT/{r.randrange(100, 999)}/2026-27"
    head = f"Tender No: {ref}\nLast date for submission: 30/11/2026\n\n" if tender else ""
    body = (f"Dear Sir,\n\n{head}{req}\n\n" + ("\n".join(extra) + "\n\n" if extra else "")
            + "Regards,\n" + _signature(r, name, title, company, city, state, shown))
    subj = (f"Tender {ref} for supply of {items[0]['key']}" if tender else
            r.choice(["RFQ", "Enquiry", "Requirement", "Request for quotation",
                      "Quotation required"])
            + f" - {items[0]['key']}")
    return Case(f"en-{i:03d}", "enquiry", "en", subj, body, _address(r, name, company), name,
                "enquiry", _expected(items), company, phone, tender)


def hindi(r: random.Random, i: int) -> Case:
    name = r.choice(PEOPLE["hi"])
    company = r.choice(COMPANIES)
    city, state = r.choice(PLACES)
    shown, phone = _phone(r)
    items = _items(r, r.choice([1, 1, 2, 3]))
    if len(items) == 1 and r.random() < 0.6:
        it = items[0]
        req = (f"हमें {_num(it['qty'])} {it['unit_word']} {it['description']}"
               f"{' ' + it['grade'] if it['grade'] else ''} चाहिए। कृपया रेट भेजें।")
        expected = _expected(items, size=False)
    else:
        expected = _expected(items)
        req = "हमें निम्नलिखित सामान का रेट चाहिए:\n" + "\n".join(
            f"{n}. {_item_text(it, 0)}" for n, it in enumerate(items, 1))
    body = (f"नमस्ते,\n\n{req}\nडिलीवरी: {city}\n\nधन्यवाद,\n{name}\n{company}\n"
            f"मोबाइल: {shown}")
    subj = r.choice(["कोटेशन चाहिए", "रेट भेजें", "पूछताछ", f"{items[0]['key']} का रेट"])
    return Case(f"hi-{i:03d}", "enquiry", "hi", subj, body, _address(r, LATIN_FOR[name], company),
                LATIN_FOR[name], "enquiry", expected, company, phone)


def telugu(r: random.Random, i: int) -> Case:
    name = r.choice(PEOPLE["te"])
    company = r.choice(COMPANIES)
    city, state = r.choice(PLACES)
    shown, phone = _phone(r)
    items = _items(r, r.choice([1, 1, 2, 3]))
    if len(items) == 1 and r.random() < 0.6:
        it = items[0]
        req = (f"మాకు {_num(it['qty'])} {it['unit_word']} {it['description']}"
               f"{' ' + it['grade'] if it['grade'] else ''} కావాలి. ధర తెలియజేయండి.")
        expected = _expected(items, size=False)
    else:
        expected = _expected(items)
        req = "మాకు ఈ క్రింది వస్తువులకు ధరలు కావాలి:\n" + "\n".join(
            f"{n}. {_item_text(it, 0)}" for n, it in enumerate(items, 1))
    body = (f"నమస్కారం,\n\n{req}\nడెలివరీ: {city}\n\nధన్యవాదాలు,\n{name}\n{company}\n"
            f"ఫోన్: {shown}")
    subj = r.choice(["కొటేషన్ కావాలి", "ధరల కోసం", "విచారణ", f"{items[0]['key']} ధర"])
    return Case(f"te-{i:03d}", "enquiry", "te", subj, body, _address(r, LATIN_FOR[name], company),
                LATIN_FOR[name], "enquiry", expected, company, phone)


def mixed(r: random.Random, i: int) -> Case:
    company = r.choice(COMPANIES)
    city, state = r.choice(PLACES)
    shown, phone = _phone(r)
    items = _items(r, 1)
    it = items[0]
    q = f"{_num(it['qty'])} {it['unit_word']}"
    style = i % 3
    if style == 0:  # Hindi in Latin letters: answered in English for now (L6 A)
        name = LATIN_FOR[r.choice(PEOPLE["hi"])]
        body = (f"Sir,\n\nHume {q} {it['description']} chahiye urgent. Best rate bhejiye "
                f"aur delivery {city} me hogi.\n\nDhanyavaad\n{name}\n{company}\nMob: {shown}")
        lang, subj = "en", f"{it['key']} rate chahiye"
    elif style == 1:  # Telugu with English words
        te = r.choice(PEOPLE["te"])
        name = LATIN_FOR[te]
        body = (f"Sir,\n\nమాకు urgent గా {it['description']} {q} కావాలి. Best rate pampandi. "
                f"Delivery {city} లో.\n\n{te}\n{company}\nPh: {shown}")
        lang, subj = "te", f"{it['key']} requirement"
    else:  # Hindi greeting, English list
        hi = r.choice(PEOPLE["hi"])
        name = LATIN_FOR[hi]
        body = (f"नमस्ते सर,\n\nकृपया इसका रेट भेजें, जल्दी चाहिए:\n1. {_item_text(it, 0)}\n"
                f"डिलीवरी {city} में।\n\nधन्यवाद\n{hi}\n{company}\nमोबाइल: {shown}")
        lang, subj = "hi", f"Rate for {it['key']}"
    return Case(f"mx-{i:03d}", "enquiry", lang, subj, body, _address(r, name, company), name,
                "enquiry", _expected(items, size=style == 2, grade=style == 2), company, phone)


def with_bom(r: random.Random, i: int, kind: str, n_lines: int) -> Case:
    name, title = r.choice(PEOPLE["en"])
    company = r.choice(COMPANIES)
    city, state = r.choice(PLACES)
    shown, phone = _phone(r)
    items = []
    pool = STEEL + MACHINED + CASTINGS + FABRICATION
    for n in range(n_lines):
        key, desc, size, grade, units = pool[(n + i) % len(pool)]
        unit = r.choice(units)
        item_key = f"{key} {n + 1}" if n_lines > len(pool) else key
        items.append({"key": item_key, "description": f"{desc} (item {n + 1})"
                      if n_lines > len(pool) else desc, "size": size, "grade": grade,
                      "unit": unit, "qty": _qty(r, unit), "unit_word": UNIT_WORDS[unit][0]})
    ext = {"bom_xlsx": "xlsx", "bom_csv": "csv", "bom_pdf": "pdf"}[kind]
    rows = [["S.No", "Description", "Size", "Grade", "Qty", "Unit"]] + [
        [n, it["description"], it["size"] or "", it["grade"] or "", it["qty"], it["unit_word"]]
        for n, it in enumerate(items, 1)]
    what = "BOM" if kind != "bom_pdf" else "specification"
    body = (f"Dear Sir,\n\nPlease find attached our {what}"
            f" for {len(items)} items. Kindly send your best offer.\n\nRegards,\n"
            + _signature(r, name, title, company, city, state, shown))
    return Case(f"bom-{i:03d}", kind, "en", f"Enquiry - {len(items)} items as per attached BOM",
                body, _address(r, name, company), name, "enquiry",
                [{"key": it["key"], "qty": it["qty"], "unit": it["unit"], "size": it["size"],
                  "grade": it["grade"]} for it in items], company, phone,
                attachment={"type": ext, "filename": f"BOM_{company.split()[0]}_{i}.{ext}",
                            "rows": rows})


# Mail that is not an enquiry ------------------------------------------------------

OTHER: dict[str, dict[str, list[tuple[str, str]]]] = {
    "payment": {
        "en": [("Payment done for invoice {n}", "Dear team,\n\nWe have paid invoice {n} by NEFT "
                "today, UTR SBIN{u}. Kindly confirm receipt.\n\nRegards,\nAccounts"),
               ("RTGS details - Inv {n}", "Sir,\n\nPayment of invoice {n} released through RTGS, "
                "UTR HDFC{u}. Please acknowledge.\n\nAccounts department")],
        "hi": [("भुगतान - इनवॉइस {n}", "नमस्ते,\n\nहमने इनवॉइस {n} का भुगतान NEFT से कर दिया है। "
                "UTR SBIN{u}. कृपया पुष्टि करें।\n\nलेखा विभाग")],
        "te": [("చెల్లింపు - ఇన్వాయిస్ {n}", "నమస్కారం,\n\nఇన్వాయిస్ {n} కి చెల్లింపు NEFT ద్వారా "
                "చేశాము. UTR SBIN{u}. దయచేసి నిర్ధారించండి.\n\nఅకౌంట్స్")],
    },
    "complaint": {
        "en": [("Material rejected - PO {n}", "Dear Sir,\n\nThe plates supplied against our PO {n} "
                "are rejected: the thickness is under tolerance and several plates are rusted. "
                "Please arrange replacement at the earliest.\n\nQuality department"),
               ("Complaint: wrong grade supplied", "Sir,\n\nWe ordered E350 but the test "
                "certificate for PO {n} shows E250. This is not acceptable. Please take back the "
                "material.\n\nStores")],
        "hi": [("शिकायत - PO {n}", "नमस्ते,\n\nPO {n} का माल खराब आया है, कई प्लेटों में जंग लगी "
                "है। कृपया तुरंत बदल दें।\n\nगुणवत्ता विभाग")],
        "te": [("ఫిర్యాదు - PO {n}", "నమస్కారం,\n\nPO {n} కింద వచ్చిన సరుకు పాడైపోయింది. "
                "దయచేసి వెంటనే మార్చండి.\n\nక్వాలిటీ విభాగం")],
    },
    "order_status": {
        "en": [("Dispatch status of PO {n}", "Dear Sir,\n\nPlease update the dispatch status of "
                "our "
                "PO {n}. When will the material reach our site?\n\nRegards,\nStores"),
               ("Where is our order {n}?", "Hi,\n\nOur order {n} was due last week. Kindly share "
                "the vehicle number and LR copy.\n\nThanks,\nPurchase")],
        "hi": [("ऑर्डर {n} कब भेजेंगे", "नमस्ते,\n\nहमारा ऑर्डर {n} कब डिस्पैच होगा? गाड़ी नंबर "
                "भेजें।\n\nधन्यवाद")],
        "te": [("ఆర్డర్ {n} స్థితి", "నమస్కారం,\n\nమా ఆర్డర్ {n} ఎప్పుడు డిస్పాచ్ అవుతుంది? వాహనం "
                "నంబర్ పంపండి.\n\nధన్యవాదాలు")],
    },
    "vendor_mail": {
        "en": [("Introducing our company", "Dear Sir,\n\nWe are a leading manufacturer of "
                "fasteners and hardware. We would like to be added to your approved vendor list. "
                "Our catalogue is attached.\n\nRegards,\nMarketing"),
               ("Price list for October", "Dear customer,\n\nPlease find our revised price list "
                "for HR coils for October. Book your orders early.\n\nSales team, Steel Mill"),
               ("Vendor registration", "Sir,\n\nWe supply industrial paints and coatings. Kindly "
                "share your vendor registration form.\n\nThanks")],
    },
    "spam": {
        "en": [("Congratulations! You have won", "Dear winner,\n\nYou have won Rs 25,00,000 in our "
                "lucky draw. Click here to claim your prize now!!!"),
               ("Get 10,000 B2B leads", "Boost your sales with 10,000 verified B2B leads. Limited "
                "offer, reply YES."),
               ("SEO for your website", "Hello,\n\nWe can bring your website to the first page of "
                "Google. Unsubscribe if not interested.")],
    },
    "auto": {
        "en": [("Out of office: RFQ", "I am out of office until Monday with limited access to "
                "email. For urgent matters contact the purchase desk."),
               ("Automatic reply: Your quotation", "Thank you for your email. This is an "
                "automatic reply; we will get back to you shortly.")],
    },
}


def other(r: random.Random, i: int, label: str, lang: str) -> Case:
    subj, body = r.choice(OTHER[label][lang])
    n, u = r.randrange(1000, 9999), r.randrange(10**6, 10**7)
    company = r.choice(COMPANIES)
    sender = f"accounts{r.randrange(10, 99)}@{company.split()[0].lower()}.example.in"
    headers = {"Auto-Submitted": "auto-replied"} if label == "auto" else {}
    return Case(f"{label}-{lang}-{i:03d}", label, lang, subj.format(n=n),
                body.format(n=n, u=u), sender, company, label, headers=headers)


def generate(seed: int = 12) -> list[Case]:
    r = random.Random(seed)
    cases: list[Case] = []
    cases += [english(r, i, tender=(i % 9 == 0)) for i in range(1, 51)]
    cases += [hindi(r, i) for i in range(1, 26)]
    cases += [telugu(r, i) for i in range(1, 26)]
    cases += [mixed(r, i) for i in range(1, 21)]
    boms = [("bom_xlsx", 40)] + [("bom_xlsx", r.randrange(5, 16)) for _ in range(7)] + \
        [("bom_csv", r.randrange(4, 12)) for _ in range(6)] + \
        [("bom_pdf", r.randrange(3, 7)) for _ in range(6)]
    cases += [with_bom(r, i, k, n) for i, (k, n) in enumerate(boms, 1)]
    plan = [("payment", "en", 8), ("payment", "hi", 2), ("payment", "te", 2),
            ("complaint", "en", 8), ("complaint", "hi", 2), ("complaint", "te", 2),
            ("order_status", "en", 8), ("order_status", "hi", 2), ("order_status", "te", 2),
            ("vendor_mail", "en", 10), ("spam", "en", 8), ("auto", "en", 6)]
    n = 0
    for label, lang, count in plan:
        for _ in range(count):
            n += 1
            cases.append(other(r, n, label, lang))
    return cases


# Files ---------------------------------------------------------------------------------

def _pdf(lines: list[str]) -> bytes:
    """A one-page PDF with real text, one line per row (written by hand: no library)."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    ops = ["BT /F1 10 Tf 40 760 Td 14 TL"] + [f"({esc(t)}) Tj T*" for t in lines] + ["ET"]
    stream = "\n".join(ops).encode("latin-1", "replace")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream
            + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


def attachment_bytes(a: dict[str, Any]) -> tuple[str, str, bytes]:
    """(filename, content type, data) for a case's bill of material."""
    rows = a["rows"]
    if a["type"] == "xlsx":
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        for row in rows:
            ws.append(row)
        buf = io.BytesIO()
        wb.save(buf)
        return a["filename"], ("application/vnd.openxmlformats-officedocument."
                               "spreadsheetml.sheet"), buf.getvalue()
    if a["type"] == "csv":
        text = "\n".join(",".join(str(c) for c in row) for row in rows)
        return a["filename"], "text/csv", text.encode()
    lines = ["Technical specification"] + [
        f"{row[0]}. {row[1]} {row[2]} {row[3]} - {row[4]} {row[5]}".replace("  ", " ")
        for row in rows[1:]]
    return a["filename"], "application/pdf", _pdf(lines)


# Measuring -----------------------------------------------------------------------------

def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def _num_eq(a: Any, b: Any) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return False


def score_lines(expected: list[dict[str, Any]], got: list[dict[str, Any]]) -> dict[str, int]:
    """Fields right out of fields expected: the item, quantity, unit, size and grade."""
    out = Counter()
    used: set[int] = set()
    for e in expected:
        key = set(_norm(e["key"]).split())
        best, best_i = None, None
        for i, g in enumerate(got):
            if i in used:
                continue
            text = _norm(f"{g.get('description')} {g.get('dimensions')} {g.get('material')}")
            if key and key <= set(text.split()):
                best, best_i = g, i
                break
        fields = ["item", "qty", "unit"] + (["size"] if e.get("size") else []) + \
            (["grade"] if e.get("grade") else [])
        out["fields"] += len(fields)
        out["expected_lines"] += 1
        if best is None:
            continue
        used.add(best_i)
        out["found_lines"] += 1
        whole = _norm(f"{best.get('description')} {best.get('dimensions')} "
                      f"{best.get('material')} {best.get('standard')}")
        right = {"item": True, "qty": _num_eq(best.get("qty"), e["qty"]),
                 "unit": (best.get("unit") or "").lower() == e["unit"].lower(),
                 "size": bool(e.get("size")) and _norm(e["size"]) in whole,
                 "grade": bool(e.get("grade")) and all(
                     t in whole for t in _norm(e["grade"]).split())}
        out["right"] += sum(1 for f in fields if right[f])
    out["extra_lines"] = max(0, len(got) - out["found_lines"])
    return out


@dataclass
class Result:
    case: Case
    label: str | None
    language: str | None
    lines: list[dict[str, Any]]
    company: str | None
    phone: str | None
    is_tender: bool
    seconds: float
    source: str  # model | rules
    ack_language: str | None = None
    ack_claims: list[str] = field(default_factory=list)
    became_enquiry: bool = False
    kept: bool = True
    guess: str | None = None  # the label the reader gave, before Forge decided


def summarise(results: list[Result]) -> dict[str, Any]:
    by: dict[str, Counter] = defaultdict(Counter)
    lines_total, bom40 = Counter(), None
    for res in results:
        c = res.case
        group = c.kind if c.kind.startswith("bom") else c.language if c.label == "enquiry" \
            else "other mail"
        for g in ("all", group):
            b = by[g]
            b["emails"] += 1
            b["label_right"] += int(res.label == c.label)
            b["guess_right"] += int((res.guess or res.label) == c.label)
            b["enquiry_right"] += int((res.label == "enquiry") == (c.label == "enquiry"))
            b["kept"] += int(res.kept)
            if c.label == "enquiry":
                b["enquiries"] += 1
                b["enquiry_found"] += int(res.became_enquiry)
                b["language_right"] += int((res.language or "en") == c.language)
                b["phone_right"] += int(bool(res.phone) and res.phone == c.phone)
                b["company_right"] += int(bool(res.company) and _norm(c.company) in _norm(
                    res.company))
                b["tender_right"] += int(res.is_tender == c.is_tender)
                if res.ack_language is not None:
                    b["acks"] += 1
                    b["ack_language_right"] += int(res.ack_language == (
                        c.language if c.language in ("en", "hi", "te") else "en"))
                    b["ack_promises"] += int(bool(res.ack_claims))
            b["seconds_max"] = max(b["seconds_max"], int(res.seconds * 1000))
        if c.label == "enquiry":
            s = score_lines(c.lines, res.lines)
            lines_total.update(s)
            by[group]["fields"] += s["fields"]
            by[group]["fields_right"] += s["right"]
            by["all"]["fields"] += s["fields"]
            by["all"]["fields_right"] += s["right"]
            if len(c.lines) == 40:
                bom40 = {"lines_read": len(res.lines), "lines_expected": 40,
                         "field_accuracy": round(100 * s["right"] / max(1, s["fields"]), 1)}
    promises = [f"{r.case.id}: {', '.join(r.ack_claims)}" for r in results if r.ack_claims]
    wrong = [f"{r.case.id}: wanted {r.case.language}, got {r.ack_language}" for r in results
             if r.ack_language is not None and r.ack_language != (
                 r.case.language if r.case.language in ("en", "hi", "te") else "en")]
    return {"groups": {k: dict(v) for k, v in by.items()}, "bom40": bom40,
            "lines": dict(lines_total), "source": Counter(r.source for r in results),
            "ack_promises": promises, "ack_language_wrong": wrong}


def _pct(a: int, b: int) -> str:
    return "–" if not b else f"{100 * a / b:.1f}%"


def report(summary: dict[str, Any], title: str) -> str:
    g = summary["groups"]
    a = g["all"]
    out = [f"# {title}", "",
           f"Read by: {', '.join(f'{k} {v}' for k, v in summary['source'].items())}", "",
           "| Check (Done-When) | Result | Target |", "|---|---|---|",
           f"| F1.1 correct classification (exact label) | "
           f"{_pct(a['label_right'], a['emails'])} | >=95% |",
           f"| F1.1 enquiry or not | {_pct(a['enquiry_right'], a['emails'])} | |",
           f"| The reader's own label (shown to the person who checks) | "
           f"{_pct(a['guess_right'], a['emails'])} | |",
           f"| F1.1 enquiries not lost | "
           f"{_pct(a.get('enquiry_found', 0), a.get('enquiries', 0))} | 100% |",
           f"| F1.1 slowest email | {a['seconds_max'] / 1000:.2f} s | <60 s |"]
    if summary["bom40"]:
        b = summary["bom40"]
        out.append(f"| F1.4 40-line Excel BOM | {b['lines_read']}/40 lines, "
                   f"{b['field_accuracy']}% fields | 40 lines, >=90% |")
    if a.get("acks"):
        out.append(f"| F1.8 acknowledgement in the customer's language | "
                   f"{_pct(a['ack_language_right'], a['acks'])} | 100% |")
        out.append(f"| F1.8 acknowledgements that promise something | {a['ack_promises']} of "
                   f"{a['acks']} | 0 |")
    out += ["", "| Group | Emails | Label right | Lines: fields right | Language | Phone | "
            "Company |", "|---|---|---|---|---|---|---|"]
    for k in sorted(g, key=lambda k: (k != "all", k)):
        v = g[k]
        out.append(f"| {k} | {v['emails']} | {_pct(v['label_right'], v['emails'])} | "
                   f"{_pct(v.get('fields_right', 0), v.get('fields', 0))} | "
                   f"{_pct(v.get('language_right', 0), v.get('enquiries', 0))} | "
                   f"{_pct(v.get('phone_right', 0), v.get('enquiries', 0))} | "
                   f"{_pct(v.get('company_right', 0), v.get('enquiries', 0))} |")
    for title, items in (("Acknowledgements that promise something",
                          summary.get("ack_promises")),
                         ("Acknowledgements in the wrong language",
                          summary.get("ack_language_wrong"))):
        if items:
            out += ["", f"{title}:"] + [f"- {x}" for x in items]
    return "\n".join(out)


def run_rules(cases: list[Case]) -> list[Result]:
    """The rules reader alone, as Forge uses it without an AI key."""
    from app.enquiries import documents, mime, rules
    from app.language.script import detect

    results = []
    for c in cases:
        t0 = time.perf_counter()
        raw = _raw(c)
        p = mime.parse(raw)
        if p.auto_kind == "auto_reply":
            results.append(Result(c, "auto", None, [], None, None, False,
                                  time.perf_counter() - t0, "rules"))
            continue
        docs = [documents.read_sync(a.filename, a.content_type, a.data) for a in p.attachments]
        data = rules.read(subject=p.subject or "", body=p.text or "",
                          from_address=p.from_address, from_name=p.from_name, docs=docs,
                          families=[], today=date(2026, 10, 12))
        cust = data.get("customer") or {}
        lang = (data.get("language") or detect(p.text).language or "en")
        # Without the AI every email is kept as an enquiry for a person to check
        # (app/enquiries/intake.py _decide); the rules' label is shown to them.
        results.append(Result(c, "enquiry", lang, data.get("lines") or [], cust.get("company"),
                              cust.get("phone"), bool(data.get("is_tender")),
                              time.perf_counter() - t0, "rules", became_enquiry=True,
                              guess=data.get("label")))
    return results


def _raw(c: Case) -> bytes:
    from app.enquiries import mime

    files = []
    if c.attachment:
        name, ctype, data = attachment_bytes(c.attachment)
        files.append(mime.Attachment(name, ctype, data))
    raw = mime.build(from_address=c.from_address, from_name=c.from_name,
                     to="sales@forge.test", subject=c.subject, body=c.body, attachments=files)
    for k, v in c.headers.items():
        raw = f"{k}: {v}\r\n".encode() + raw
    return raw


async def run_pipeline(cases: list[Case]) -> list[Result]:
    """Every email through ingest and process in a throwaway tenant, as the mailbox
    would bring it: reading (the AI when a key is set), duplicates, owner, timers and
    the acknowledgement, which is checked for language and promises."""
    from sqlalchemy import select

    from app.cli import create_tenant
    from app.db import dispose_engine, get_sessionmaker, tenant_session
    from app.enquiries import ack, guard, intake
    from app.models import Contact, Enquiry, EnquiryLine, InboundEmail, Tenant

    slug = f"evalenq{uuid.uuid4().hex[:6]}"
    await create_tenant("Enquiry eval", slug, f"eval@{slug}.example.com", "Eval", "x" * 12)
    async with get_sessionmaker()() as s:
        tid = (await s.scalar(select(Tenant).where(Tenant.slug == slug))).id
    results = []
    for c in cases:
        t0 = time.perf_counter()
        async with tenant_session(tid) as s:
            row, _ = await intake.ingest(s, tid, _raw(c), provider="test",
                                         provider_id=f"eval-{c.id}", actor="eval")
            if row.stage == "new":
                await intake.process(s, row)
            row_id = row.id
        seconds = time.perf_counter() - t0
        async with tenant_session(tid) as s:
            row = await s.get(InboundEmail, row_id)
            label = "auto" if row.outcome in ("auto", "receipt") else row.label
            res = Result(c, label, None, [], None, None, False, seconds,
                         row.label_source or "rules", kept=row.raw_file_id is not None,
                         guess=label)
            if row.enquiry_id:
                e = await s.get(Enquiry, row.enquiry_id)
                res.became_enquiry, res.label = True, "enquiry"
                res.language = e.language
                res.company, res.phone = e.customer.get("company"), e.customer.get("phone")
                res.is_tender = e.is_tender
                res.lines = [{"description": ln.description, "dimensions": ln.dimensions,
                              "material": ln.material, "standard": ln.standard,
                              "qty": ln.qty, "unit": ln.unit}
                             for ln in await s.scalars(select(EnquiryLine).where(
                                 EnquiryLine.enquiry_id == e.id,
                                 EnquiryLine.removed_at.is_(None)))]
                contact = await s.get(Contact, e.contact_id) if e.contact_id else None
                draft = await ack.compose(s, e, contact, set(), c.subject)
                res.ack_language = draft["language"]
                res.ack_claims = guard.claims(draft["subject"], draft["body"])
        results.append(res)
    await dispose_engine()
    return results


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=12)
    p.add_argument("--pipeline", action="store_true", help="run every email through Forge")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--write", help="save the set as JSON lines")
    p.add_argument("--json", help="save the summary as JSON")
    a = p.parse_args()
    cases = generate(a.seed)[: a.limit]
    if a.write:
        with open(a.write, "w", encoding="utf-8") as f:
            for c in cases:
                f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
    results = asyncio.run(run_pipeline(cases)) if a.pipeline else run_rules(cases)
    summary = summarise(results)
    title = ("Enquiry test set through Forge" if a.pipeline else
             "Enquiry test set, rules reader only") + f" ({len(cases)} emails, seed {a.seed})"
    print(report(summary, title))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
