"""The acknowledgement, the nudges and parking (F1.8, decision N7 A).

The acknowledgement is a fixed text written by people, in English, Hindi or Telugu,
with the reference number and the questions for the required details the enquiry
did not give (from the product family's checklist). It makes no promise about price,
delivery or what we can supply: those wait for the product knowledge base (F1.6).

The Enquiry Agent sends it on its own for most customers; for strategic accounts
(they start at L0) and tenders it is drafted for a person to check and send. Waiting
for answers, the agent nudges on day 2 and day 5, then parks the enquiry on day 7.

The Hindi and Telugu texts were written by Forge; a native speaker should check them
before real customers see them (as for Step 6's fixed texts).
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.enquiries.families import GENERAL
from app.models import Contact, Enquiry, ProductFamily, Tenant

LANGS = ("en", "hi", "te")
MAX_QUESTIONS = 5

TEXT: dict[str, dict[str, str]] = {
    "en": {
        "dear": "Dear {name},", "dear_none": "Dear Sir/Madam,",
        "thanks": "Thank you for your enquiry. We have received it and our team is working "
                  "on it. Your reference number is {ref}; please mention it when you write "
                  "to us about this enquiry.",
        "ask": "To prepare our quotation, could you please tell us:",
        "nudge": "This is a gentle reminder about your enquiry {ref}. To prepare our "
                 "quotation we still need:",
        "close": "Regards,\n{company}",
        "subject": "Re: {subject} [{ref}]", "subject_none": "Your enquiry {ref}",
    },
    "hi": {
        "dear": "प्रिय {name},", "dear_none": "महोदय/महोदया,",
        "thanks": "आपकी पूछताछ के लिए धन्यवाद। हमें यह मिल गई है और हमारी टीम इस पर काम कर "
                  "रही है। आपका संदर्भ नंबर {ref} है; इस पूछताछ के बारे में लिखते समय कृपया "
                  "इसका उल्लेख करें।",
        "ask": "कोटेशन तैयार करने के लिए, कृपया हमें बताएं:",
        "nudge": "आपकी पूछताछ {ref} के बारे में एक विनम्र याद दिलाना। कोटेशन तैयार करने के "
                 "लिए हमें अभी भी यह जानकारी चाहिए:",
        "close": "धन्यवाद,\n{company}",
        "subject": "Re: {subject} [{ref}]", "subject_none": "आपकी पूछताछ {ref}",
    },
    "te": {
        "dear": "ప్రియమైన {name} గారికి,", "dear_none": "అయ్యా/అమ్మా,",
        "thanks": "మీ విచారణకు ధన్యవాదాలు. ఇది మాకు అందింది, మా బృందం దీనిపై పని చేస్తోంది. మీ "
                  "రిఫరెన్స్ నంబర్ {ref}; ఈ విచారణ గురించి రాసేటప్పుడు దయచేసి దీనిని "
                  "పేర్కొనండి.",
        "ask": "కొటేషన్ తయారు చేయడానికి, దయచేసి మాకు తెలియజేయండి:",
        "nudge": "మీ విచారణ {ref} గురించి ఒక చిన్న గుర్తు. కొటేషన్ తయారు చేయడానికి మాకు ఇంకా ఈ "
                 "వివరాలు కావాలి:",
        "close": "ధన్యవాదాలు,\n{company}",
        "subject": "Re: {subject} [{ref}]", "subject_none": "మీ విచారణ {ref}",
    },
}


def language_for(enq: Enquiry, contact: Contact | None) -> str:
    for lang in (enq.language, contact.preferred_language if contact else None):
        if lang in LANGS:
            return lang
    return "en"


async def checklist(session: AsyncSession, enq: Enquiry) -> list[dict[str, Any]]:
    fam = await session.get(ProductFamily, enq.product_family_id) \
        if enq.product_family_id else None
    return list(fam.checklist) if fam is not None and fam.checklist else list(GENERAL)


def missing(items: list[dict[str, Any]], given: set[str]) -> list[dict[str, Any]]:
    """Required details not given yet, in the checklist's order (at most five)."""
    return [i for i in items if i.get("required") and i["key"] not in given][:MAX_QUESTIONS]


def _question(item: dict[str, Any], lang: str) -> str:
    qs = item.get("questions") or {}
    return qs.get(lang) or qs.get("en") or item.get("label") or item["key"]


async def _parts(session: AsyncSession, enq: Enquiry, contact: Contact | None
                 ) -> tuple[str, str, str]:
    tenant = await session.get(Tenant, enq.tenant_id)
    company = tenant.name if tenant else "Sales team"
    name = (contact.full_name if contact else None) or enq.customer.get("contact_name")
    return company, (name or "").strip(), language_for(enq, contact)


async def compose(session: AsyncSession, enq: Enquiry, contact: Contact | None,
                  given: set[str], original_subject: str | None) -> dict[str, Any]:
    company, name, lang = await _parts(session, enq, contact)
    t = TEXT[lang]
    ask = missing(await checklist(session, enq), given)
    body = [t["dear"].format(name=name) if name else t["dear_none"], "",
            t["thanks"].format(ref=enq.ref), ""]
    if ask:
        body.append(t["ask"])
        body += [f"{i}. {_question(q, lang)}" for i, q in enumerate(ask, 1)]
        body.append("")
    body.append(t["close"].format(company=company))
    subj = (original_subject or "").strip()
    subj = subj[3:].strip() if subj.lower().startswith("re:") else subj
    subject = (t["subject"].format(subject=subj[:200], ref=enq.ref) if subj
               else t["subject_none"].format(ref=enq.ref))
    return {"subject": subject[:300], "body": "\n".join(body), "language": lang,
            "question_keys": [q["key"] for q in ask]}


async def compose_nudge(session: AsyncSession, enq: Enquiry, contact: Contact | None
                        ) -> dict[str, Any] | None:
    open_q = [q for q in enq.questions if not q.get("answered_at")]
    if not open_q:
        return None
    company, name, lang = await _parts(session, enq, contact)
    t = TEXT[lang]
    items = {i["key"]: i for i in await checklist(session, enq)}
    lines = [t["dear"].format(name=name) if name else t["dear_none"], "",
             t["nudge"].format(ref=enq.ref)]
    lines += [f"{i}. {_question(items.get(q['key'], {'key': q['key'], 'label': q['label']}), lang)}"
              for i, q in enumerate(open_q, 1)]
    lines += ["", t["close"].format(company=company)]
    return {"subject": t["subject_none"].format(ref=enq.ref), "body": "\n".join(lines),
            "language": lang, "question_keys": [q["key"] for q in open_q]}


# A courteous decline for an enquiry we can't supply (F1.6, P1 A: a person approves it
# before it is sent). Written by Forge; a native speaker should check the Hindi and
# Telugu before real customers see them.
DECLINE: dict[str, dict[str, str]] = {
    "en": {
        "body": "Thank you for your enquiry {ref} and for thinking of us. We have looked at "
                "it carefully, but it is outside what we make, so we are not able to quote "
                "for it this time.",
        "refer": "For this requirement you could contact {refer_to}.",
        "later": "We would be glad to help with any other requirement in future.",
    },
    "hi": {
        "body": "आपकी पूछताछ {ref} और हमें याद करने के लिए धन्यवाद। हमने इसे ध्यान से देखा, "
                "लेकिन यह हमारे उत्पादों की सीमा से बाहर है, इसलिए इस बार हम इसका कोटेशन "
                "नहीं दे पाएंगे।",
        "refer": "इस आवश्यकता के लिए आप {refer_to} से संपर्क कर सकते हैं।",
        "later": "भविष्य में किसी अन्य आवश्यकता में आपकी मदद करके हमें खुशी होगी।",
    },
    "te": {
        "body": "మీ విచారణ {ref} కు మరియు మమ్మల్ని గుర్తుంచుకున్నందుకు ధన్యవాదాలు. మేము దీనిని "
                "జాగ్రత్తగా పరిశీలించాము, కానీ ఇది మేము తయారు చేసే వాటి పరిధికి వెలుపల ఉంది, "
                "కాబట్టి ఈసారి కొటేషన్ ఇవ్వలేము.",
        "refer": "ఈ అవసరానికి మీరు {refer_to} ను సంప్రదించవచ్చు.",
        "later": "భవిష్యత్తులో ఏదైనా ఇతర అవసరానికి సహాయం చేయడానికి మేము సంతోషిస్తాము.",
    },
}


async def compose_decline(session: AsyncSession, enq: Enquiry, contact: Contact | None,
                          original_subject: str | None, refer_to: str | None
                          ) -> dict[str, Any]:
    company, name, lang = await _parts(session, enq, contact)
    t, d = TEXT[lang], DECLINE[lang]
    body = [t["dear"].format(name=name) if name else t["dear_none"], "",
            d["body"].format(ref=enq.ref)]
    if refer_to:
        body += ["", d["refer"].format(refer_to=refer_to)]
    body += ["", d["later"], "", t["close"].format(company=company)]
    subj = (original_subject or "").strip()
    subj = subj[3:].strip() if subj.lower().startswith("re:") else subj
    subject = (t["subject"].format(subject=subj[:200], ref=enq.ref) if subj
               else t["subject_none"].format(ref=enq.ref))
    return {"subject": subject[:300], "body": "\n".join(body), "language": lang,
            "question_keys": []}


def ask_questions(enq: Enquiry, items: list[dict[str, Any]], keys: list[str],
                  now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    labels = {i["key"]: i.get("label") or i["key"] for i in items}
    have = {q["key"] for q in enq.questions}
    enq.questions = [*enq.questions, *[{"key": k, "label": labels.get(k, k),
                                        "asked_at": now.isoformat(), "answered_at": None}
                                       for k in keys if k not in have]]


def mark_answered(enq: Enquiry, given: set[str], now: datetime | None = None) -> list[str]:
    now = now or datetime.now(UTC)
    done, out = [], []
    for q in enq.questions:
        if not q.get("answered_at") and q["key"] in given:
            q = {**q, "answered_at": now.isoformat()}
            done.append(q["key"])
        out.append(q)
    enq.questions = out
    return done
