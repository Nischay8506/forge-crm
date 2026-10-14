"""Product families and their quotability checklists (decision N8 A).

Each company sets up what it makes on Enquiries → Setup, starting from these
templates and editing them. A family has the words that point an enquiry to it
(also used to route it to a specialist, F1.9) and the details needed to quote it,
each with its question in English, Hindi and Telugu. The acknowledgement (F1.8)
asks only the required details the enquiry did not give.

The Hindi and Telugu questions were written by Forge; a native speaker should check
them before real customers see them (the same as Step 6's fixed texts).
"""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import ProductFamily


def _q(key: str, label: str, en: str, hi: str, te: str, required: bool = True
       ) -> dict[str, Any]:
    return {"key": key, "label": label, "required": required,
            "questions": {"en": en, "hi": hi, "te": te}}


GRADE = _q("grade", "Material grade or standard",
           "Which material grade or standard do you need (for example IS 2062 E250)?",
           "आपको कौन सा मटेरियल ग्रेड या स्टैंडर्ड चाहिए (जैसे IS 2062 E250)?",
           "మీకు ఏ మెటీరియల్ గ్రేడ్ లేదా స్టాండర్డ్ కావాలి (ఉదాహరణకు IS 2062 E250)?")
DRAWING = _q("drawing", "Drawing or specification",
             "Could you please share the drawing or specification?",
             "क्या आप कृपया ड्रॉइंग या स्पेसिफिकेशन भेज सकते हैं?",
             "దయచేసి డ్రాయింగ్ లేదా స్పెసిఫికేషన్ పంపగలరా?")
LOCATION = _q("delivery_location", "Delivery location",
              "Where should it be delivered (city and pincode)?",
              "डिलीवरी कहाँ करनी है (शहर और पिनकोड)?",
              "డెలివరీ ఎక్కడ చేయాలి (నగరం మరియు పిన్‌కోడ్)?")
WHEN = _q("required_date", "When it is needed",
          "By when do you need it?",
          "आपको यह कब तक चाहिए?",
          "మీకు ఇది ఎప్పటిలోగా కావాలి?")

TEMPLATES: dict[str, dict[str, Any]] = {
    "steel": {
        "name": "Steel sections and plates",
        "make_ability": "standard",
        "keywords": ["plate", "plates", "sheet", "coil", "beam", "channel", "angle", "ismb",
                     "ismc", "isa", "hr", "cr", "ms", "tmt", "bar", "rod", "pipe", "tube",
                     "section", "joist", "flat", "chequered", "steel"],
        "checklist": [
            GRADE,
            _q("size", "Size",
               "What size do you need (thickness, width and length, or the section size)?",
               "आपको कौन सा साइज़ चाहिए (मोटाई, चौड़ाई और लंबाई, या सेक्शन साइज़)?",
               "మీకు ఏ సైజు కావాలి (మందం, వెడల్పు, పొడవు, లేదా సెక్షన్ సైజు)?"),
            _q("qty", "Quantity",
               "How much do you need, in tonnes or pieces?",
               "आपको कितनी मात्रा चाहिए, टन में या पीस में?",
               "మీకు ఎంత పరిమాణం కావాలి, టన్నుల్లో లేదా పీస్‌లలో?"),
            LOCATION, WHEN,
            _q("test_certificate", "Mill test certificate",
               "Do you need a mill test certificate?",
               "क्या आपको मिल टेस्ट सर्टिफिकेट चाहिए?",
               "మీకు మిల్ టెస్ట్ సర్టిఫికెట్ కావాలా?", required=False),
        ],
    },
    "fabrication": {
        "name": "Fabricated structures",
        "make_ability": "eto",
        "keywords": ["fabrication", "fabricated", "structure", "structural", "shed", "frame",
                     "platform", "tank", "skid", "welding", "welded", "assembly", "gantry",
                     "truss", "staircase", "railing"],
        "checklist": [
            DRAWING, GRADE,
            _q("qty", "Quantity",
               "How many units do you need (or the total weight)?",
               "आपको कितनी यूनिट चाहिए (या कुल वज़न)?",
               "మీకు ఎన్ని యూనిట్లు కావాలి (లేదా మొత్తం బరువు)?"),
            _q("finish", "Surface finish",
               "What surface finish do you need (painting or galvanising)?",
               "आपको कौन सी सरफेस फिनिश चाहिए (पेंटिंग या गैल्वनाइज़िंग)?",
               "మీకు ఏ సర్ఫేస్ ఫినిష్ కావాలి (పెయింటింగ్ లేదా గాల్వనైజింగ్)?"),
            LOCATION, WHEN,
            _q("erection", "Erection at site",
               "Do you need supply only, or supply and erection at site?",
               "आपको केवल सप्लाई चाहिए, या साइट पर इरेक्शन भी?",
               "మీకు కేవలం సరఫరా మాత్రమేనా, లేదా సైట్‌లో ఎరెక్షన్ కూడా కావాలా?",
               required=False),
        ],
    },
    "machined": {
        "name": "Machined parts",
        "make_ability": "cto",
        "keywords": ["machined", "machining", "cnc", "turned", "turning", "milled", "milling",
                     "shaft", "flange", "bush", "bushing", "gear", "impeller", "component",
                     "spindle", "coupling", "pin", "sleeve"],
        "checklist": [
            DRAWING, GRADE,
            _q("qty", "Quantity",
               "How many pieces do you need, and is it a one-time or a regular requirement?",
               "आपको कितने पीस चाहिए, और यह एक बार की ज़रूरत है या नियमित?",
               "మీకు ఎన్ని పీస్‌లు కావాలి, ఇది ఒక్కసారి అవసరమా లేక క్రమం తప్పని అవసరమా?"),
            _q("tolerance", "Tolerances and finish",
               "Are there critical tolerances or surface finish requirements?",
               "क्या कोई ज़रूरी टॉलरेंस या सरफेस फिनिश की शर्तें हैं?",
               "ఏవైనా కీలకమైన టాలరెన్స్‌లు లేదా సర్ఫేస్ ఫినిష్ అవసరాలు ఉన్నాయా?",
               required=False),
            _q("heat_treatment", "Heat treatment or coating",
               "Do you need any heat treatment or coating?",
               "क्या हीट ट्रीटमेंट या कोटिंग चाहिए?",
               "హీట్ ట్రీట్‌మెంట్ లేదా కోటింగ్ ఏమైనా కావాలా?", required=False),
            WHEN, LOCATION,
        ],
    },
    "castings": {
        "name": "Castings",
        "make_ability": "cto",
        "keywords": ["casting", "castings", "cast", "foundry", "ci", "sg", "ductile",
                     "grey iron", "gray iron", "investment casting", "pattern", "die casting"],
        "checklist": [
            DRAWING,
            _q("grade", "Material grade",
               "Which material grade do you need (for example FG 260 or SG 500/7)?",
               "आपको कौन सा मटेरियल ग्रेड चाहिए (जैसे FG 260 या SG 500/7)?",
               "మీకు ఏ మెటీరియల్ గ్రేడ్ కావాలి (ఉదాహరణకు FG 260 లేదా SG 500/7)?"),
            _q("unit_weight", "Weight of one piece",
               "What is the approximate weight of one piece?",
               "एक पीस का लगभग वज़न कितना है?",
               "ఒక్క పీస్ సుమారు బరువు ఎంత?"),
            _q("qty", "Quantity",
               "How many pieces do you need per order, and per year?",
               "हर ऑर्डर में और साल में कितने पीस चाहिए?",
               "ప్రతి ఆర్డర్‌కు మరియు సంవత్సరానికి ఎన్ని పీస్‌లు కావాలి?"),
            _q("machining", "Machining",
               "Do you need it machined, or as-cast?",
               "क्या मशीनिंग भी चाहिए, या केवल कास्टिंग?",
               "మెషినింగ్ కూడా కావాలా, లేక కేవలం కాస్టింగ్ మాత్రమేనా?", required=False),
            _q("pattern", "Pattern",
               "Will you provide the pattern, or should we make it?",
               "क्या पैटर्न आप देंगे, या हमें बनाना होगा?",
               "ప్యాటర్న్ మీరు ఇస్తారా, లేక మేము తయారు చేయాలా?", required=False),
            WHEN, LOCATION,
        ],
    },
    # F3.1 (C4 A): the sample configurable family; it comes with sample options and
    # rules (app/cpq/samples.py) that the company edits.
    "flanges": {
        "name": "Flanges",
        "make_ability": "cto",
        "keywords": ["flange", "flanges", "weld neck", "slip on", "blind flange", "wnrf",
                     "sorf", "rtj", "raised face", "a105", "f304", "f316", "lf2",
                     "class 150", "class 300", "class 600", "pn16", "ansi b16.5", "asme b16.5"],
        "checklist": [
            _q("size", "Size",
               "Which size do you need (nominal bore, or in inches)?",
               "आपको कौन सा साइज़ चाहिए (नॉमिनल बोर या इंच में)?",
               "మీకు ఏ సైజు కావాలి (నామినల్ బోర్ లేదా అంగుళాల్లో)?"),
            _q("pressure_class", "Pressure class",
               "Which pressure class do you need (for example Class 150 or 300)?",
               "आपको कौन सी प्रेशर क्लास चाहिए (जैसे Class 150 या 300)?",
               "మీకు ఏ ప్రెషర్ క్లాస్ కావాలి (ఉదాహరణకు Class 150 లేదా 300)?"),
            _q("grade", "Material",
               "Which material do you need (for example ASTM A105 or A182 F316)?",
               "आपको कौन सा मटेरियल चाहिए (जैसे ASTM A105 या A182 F316)?",
               "మీకు ఏ మెటీరియల్ కావాలి (ఉదాహరణకు ASTM A105 లేదా A182 F316)?"),
            _q("facing", "Facing",
               "Which facing do you need: raised face, flat face or ring-type joint?",
               "आपको कौन सा फेसिंग चाहिए: रेज़्ड फेस, फ्लैट फेस या रिंग-टाइप जॉइंट?",
               "మీకు ఏ ఫేసింగ్ కావాలి: రెయిజ్డ్ ఫేస్, ఫ్లాట్ ఫేస్ లేదా రింగ్-టైప్ జాయింట్?"),
            _q("qty", "Quantity",
               "How many pieces do you need?",
               "आपको कितने पीस चाहिए?",
               "మీకు ఎన్ని పీస్‌లు కావాలి?"),
            WHEN, LOCATION,
        ],
    },
}

# When no family fits the enquiry.
GENERAL = [
    _q("product", "What is needed",
       "Could you tell us exactly what you need (product, size and material)?",
       "क्या आप बता सकते हैं कि आपको ठीक-ठीक क्या चाहिए (प्रोडक्ट, साइज़ और मटेरियल)?",
       "మీకు సరిగ్గా ఏమి కావాలో తెలియజేయగలరా (ప్రొడక్ట్, సైజు మరియు మెటీరియల్)?"),
    _q("qty", "Quantity",
       "How much do you need?",
       "आपको कितनी मात्रा चाहिए?",
       "మీకు ఎంత పరిమాణం కావాలి?"),
    LOCATION, WHEN,
]

# Every detail a checklist can ask about, for the reader's prompt.
DETAIL_KEYS = sorted({item["key"] for t in TEMPLATES.values() for item in t["checklist"]}
                     | {item["key"] for item in GENERAL})


MAKE_WORDS = {"standard": "Standard", "cto": "Configurable", "eto": "Needs engineering",
              "no_fit": "No fit"}
QTY_UNITS = ("MT", "kg", "nos", "m", "sets")


def out(f: ProductFamily) -> dict[str, Any]:
    return {"id": str(f.id), "name": f.name, "template": f.template, "keywords": f.keywords,
            "checklist": f.checklist, "make_ability": f.make_ability, "moq": f.moq,
            "sweet_spot": f.sweet_spot, "refer_to": f.refer_to, "active": f.active,
            "updated_by": f.updated_by, "updated_at": f.updated_at}


async def active(session: AsyncSession) -> list[ProductFamily]:
    return list(await session.scalars(select(ProductFamily).where(
        ProductFamily.active.is_(True)).order_by(ProductFamily.name)))


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f >= 0 else None


def _amount(v: Any, keys: tuple[str, ...]) -> dict[str, Any] | None | str:
    """{"qty": 5, "unit": "MT"} or {"min", "max", "unit"}; None when empty; a problem
    as text when it does not fit."""
    if not v:
        return None
    if not isinstance(v, dict):
        return "must be a number and a unit"
    nums = {k: _num(v.get(k)) for k in keys if v.get(k) not in (None, "")}
    if not nums:
        return None
    if any(n is None for n in nums.values()) or v.get("unit") not in QTY_UNITS:
        return f"needs numbers and one of these units: {', '.join(QTY_UNITS)}"
    if "min" in nums and "max" in nums and nums["min"] > nums["max"]:
        return "the smallest is more than the largest"
    return {**nums, "unit": v["unit"]}


def problems(data: dict[str, Any]) -> list[str]:
    out_: list[str] = []
    if data.get("make_ability", "standard") not in MAKE_WORDS:
        out_.append("How it is supplied is standard, configurable, needs engineering or "
                    "not made")
    for key, label, keys in (("moq", "The minimum order", ("qty",)),
                             ("sweet_spot", "The best order size", ("min", "max"))):
        got = _amount(data.get(key), keys)
        if isinstance(got, str):
            out_.append(f"{label} {got}")
    if len(str(data.get("refer_to") or "")) > 300:
        out_.append("Who to refer customers to is up to 300 characters")
    name = str(data.get("name") or "").strip()
    if not name or len(name) > 100:
        out_.append("Give the family a name (up to 100 characters)")
    kws = data.get("keywords") or []
    if not isinstance(kws, list) or len(kws) > 60 or any(
            not isinstance(k, str) or not k.strip() or len(k) > 40 for k in kws):
        out_.append("Keywords are up to 60 words or short phrases")
    items = data.get("checklist") or []
    if not isinstance(items, list) or not items or len(items) > 15:
        out_.append("A checklist has between 1 and 15 details")
        return out_
    keys = set()
    for it in items:
        key = str(it.get("key") or "").strip()
        if not key or key in keys:
            out_.append("Each detail needs its own key")
        keys.add(key)
        if not str(it.get("label") or "").strip():
            out_.append(f"Give the detail '{key}' a name")
        qs = it.get("questions") or {}
        if not str(qs.get("en") or "").strip():
            out_.append(f"Write the English question for '{it.get('label') or key}'")
        for lang, q in qs.items():
            if lang not in ("en", "hi", "te") or len(str(q)) > 300:
                out_.append(f"Questions are in English, Hindi or Telugu, up to 300 characters "
                            f"('{key}')")
    return out_


def clean(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(data["name"]).strip(),
        "keywords": sorted({str(k).strip().lower() for k in data.get("keywords") or []
                            if str(k).strip()}),
        "checklist": [{"key": str(it["key"]).strip(), "label": str(it["label"]).strip(),
                       "required": bool(it.get("required", True)),
                       "questions": {k: str(v).strip() for k, v in
                                     (it.get("questions") or {}).items() if str(v).strip()}}
                      for it in data["checklist"]],
        "make_ability": data.get("make_ability") or "standard",
        "moq": _amount(data.get("moq"), ("qty",)),
        "sweet_spot": _amount(data.get("sweet_spot"), ("min", "max")),
        "refer_to": str(data.get("refer_to") or "").strip() or None,
        "active": bool(data.get("active", True)),
    }


async def create(session: AsyncSession, tenant_id: uuid.UUID, data: dict[str, Any],
                 actor: str, template: str | None = None) -> ProductFamily:
    if template:
        if template not in TEMPLATES:
            raise AppError("Unknown template")
        data = {**TEMPLATES[template], **{k: v for k, v in data.items() if v}}
    errs = problems(data)
    if errs:
        raise AppError("; ".join(errs))
    c = clean(data)
    if await session.scalar(select(ProductFamily.id).where(
            func.lower(ProductFamily.name) == c["name"].lower())):
        raise AppError(f"There is already a family called {c['name']}")
    f = ProductFamily(tenant_id=tenant_id, template=template, created_by=actor,
                      updated_by=actor, **c)
    session.add(f)
    await session.flush()
    return f


async def update(session: AsyncSession, f: ProductFamily, data: dict[str, Any],
                 actor: str) -> ProductFamily:
    from datetime import UTC, datetime

    fields = ("name", "keywords", "checklist", "make_ability", "moq", "sweet_spot",
              "refer_to", "active")
    merged = {**{k: getattr(f, k) for k in fields},
              **{k: v for k, v in data.items() if k in fields}}
    errs = problems(merged)
    if errs:
        raise AppError("; ".join(errs))
    c = clean(merged)
    if c["name"].lower() != f.name.lower() and await session.scalar(
            select(ProductFamily.id).where(func.lower(ProductFamily.name) == c["name"].lower())):
        raise AppError(f"There is already a family called {c['name']}")
    for k, v in c.items():
        setattr(f, k, v)
    f.updated_by, f.updated_at = actor, datetime.now(UTC)
    await session.flush()
    return f


def match(text: str, families: list[ProductFamily]) -> ProductFamily | None:
    """The family whose words appear most in the text (whole words), if any."""
    import re

    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    low = text.lower()
    best, score = None, 0
    for f in families:
        s = sum(2 if " " in k and k in low else 1 for k in f.keywords
                if (" " in k and k in low) or k in words)
        if s > score:
            best, score = f, s
    return best
