"""M1 Enquiry: from an email to an enquiry with an owner, timers and an
acknowledgement (F1.1, F1.5, F1.8, F1.9, F1.10; decisions N1-N15)."""
# ruff: noqa: F811  (the ctx fixture is imported from test_agents)

import base64
import io
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.channels import send
from app.db import tenant_session
from app.enquiries import guard, intake, rules, timers
from app.llm import gateway, routing
from app.llm.providers import ProviderResponse
from app.models import (
    Account,
    Contact,
    Enquiry,
    InboundEmail,
    Message,
    OutboxEvent,
    SlaTimer,
    StoredFile,
    User,
)
from tests.test_agents import API, _user_with_role, ctx, drain, steward_handlers  # noqa: F401

E = f"{API}/enquiries"

RFQ = """Dear Sir,

Please quote your best rates for the following:

1. MS Plate 10mm x 1500 x 6300, IS 2062 E250 - 25 MT
2. ISMB 200 - 12 MT
3. ISMC 150 - 8 MT

Delivery at: Patancheru, Hyderabad
Required by 30/11/2026

Regards,
Ravi Kumar
Purchase Manager
Sri Venkateswara Engineering Pvt Ltd
Hyderabad, Telangana
Mob: 98480 12345
"""


class Reader:
    """A fake model: answers each call with the next prepared reading."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    async def chat(self, model, messages, **kw):
        self.calls.append(messages)
        a = self.answers.pop(0) if self.answers else self.answers_default
        if isinstance(a, Exception):
            raise a
        return ProviderResponse(text=a if isinstance(a, str) else json.dumps(a),
                                input_tokens=500, output_tokens=200)

    answers_default = {"label": "enquiry", "confidence": 0.9}


@pytest.fixture
def ai(monkeypatch):
    def use(*answers):
        r = Reader(*answers)
        monkeypatch.setattr(gateway.providers_mod, "PROVIDERS", {"fake": r})
        monkeypatch.setattr(routing, "ROUTES", {"extraction": [routing.Route("fake", "x1")]})
        return r
    return use


def reading(**over):
    base = {
        "label": "enquiry", "confidence": 0.93, "could_be_enquiry": True,
        "reason": "Asks for rates on steel plates and beams", "language": "en",
        "is_tender": False, "tender_ref": None,
        "summary": "Rates for MS plates, ISMB 200 and ISMC 150, delivered to Patancheru",
        "customer": {"company": "Sri Venkateswara Engineering Pvt Ltd",
                     "contact_name": "Ravi Kumar", "designation": "Purchase Manager",
                     "phone": "+91 98480 12345", "email": "ravi@sve.example.in",
                     "city": "Hyderabad", "state": "Telangana"},
        "lines": [
            {"description": "MS Plate", "dimensions": "10mm x 1500 x 6300",
             "material": "E250", "standard": "IS 2062", "qty": 25, "unit": "MT",
             "confidence": 0.95},
            {"description": "ISMB 200", "qty": "12", "unit": "MT", "confidence": 0.9},
            {"description": "ISMC 150", "qty": 8, "unit": "MT", "confidence": 0.9},
        ],
        "target_date": "2026-11-30", "target_date_text": "Required by 30/11/2026",
        "delivery_location": "Patancheru, Hyderabad", "special_requirements": None,
        "product_family": None, "given": ["grade", "size", "qty", "delivery_location",
                                          "required_date"],
    }
    base.update(over)
    return base


async def _mail(client, h, *, sender="ravi@sve.example.in", name="Ravi Kumar",
                subject="RFQ for MS plates and beams", body=RFQ, reply_to=None, files=None):
    r = await client.post(f"{E}/test-mailbox", headers=h, json={
        "from_address": sender, "from_name": name, "subject": subject, "body": body,
        "reply_to_message_id": reply_to, "attachments": files or []})
    assert r.status_code == 201, r.text
    return r.json()


async def _enquiry(client, h, enquiry_id):
    r = await client.get(f"{E}/{enquiry_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


async def _seller(client, ctx, name="Seller One", **profile):
    role, h = await _user_with_role(client, ctx, name, [
        "enquiries.read", "enquiries.work", "messages.send", "accounts.read"])
    async with tenant_session(ctx["tid"]) as s:
        u = await s.scalar(select(User).where(User.full_name == name))
        u.takes_enquiries = profile.get("takes", True)
        u.regions = profile.get("regions", [])
        u.product_family_ids = profile.get("families", [])
        u.max_open_enquiries = profile.get("max_open")
        uid = u.id
    return uid, h


async def _notices(tid, title_part):
    async with tenant_session(tid) as s:
        rows = list(await s.scalars(select(OutboxEvent).where(
            OutboxEvent.event_type == "inbox.notify")))
    return [r.payload for r in rows if title_part in (r.payload.get("title") or "")]


# Reading without the AI -------------------------------------------------------------

async def test_without_an_ai_key_every_email_becomes_an_enquiry_for_a_person_to_check(
        client, ctx):
    h, tid = ctx["h"], ctx["tid"]
    out = await _mail(client, h)
    assert out["outcome"] == "enquiry" and out["label_source"] == "rules"
    assert out["ref"].startswith(f"ENQ-{datetime.now(UTC).year}-")
    e = await _enquiry(client, h, out["enquiry_id"])
    assert e["needs_review"] is True
    assert e["review_reasons"][0] == "Not read by AI: no AI key yet (open question D3)"
    assert [ln["description"] for ln in e["lines"]] == [
        "MS Plate 10mm x 1500 x 6300, IS 2062 E250", "ISMB 200", "ISMC 150"]
    first = e["lines"][0]
    assert first["qty"] == "25.000" and first["unit"] == "MT"
    assert first["standard"].startswith("IS 2062") and first["material"] == "E250"
    assert e["customer"]["phone"] == "+919848012345"
    assert e["customer"]["company"] == "Sri Venkateswara Engineering Pvt Ltd"
    assert e["customer"]["city"] == "Hyderabad" and e["customer"]["state"] == "Telangana"
    assert e["details"]["delivery_location"] == "Patancheru, Hyderabad"
    assert e["details"]["target_date"] == "2026-11-30"
    # Not sure what it is, so nothing goes to the customer on its own, and no contact
    # is made from a guess; nobody takes enquiries yet, so admins are told.
    assert [m for m in e["messages"] if m["direction"] == "out"] == []
    assert e["contact"] is None and e["owner_id"] is None
    assert await _notices(tid, f"{e['ref']} has no owner")
    stages = {t["stage"]: t for t in e["timers"]}
    assert stages["first_response"]["clock"] == "always"
    assert stages["first_response"]["target_seconds"] == 15 * 60
    assert stages["quote"]["clock"] == "working" and stages["quote"]["running"]
    assert "first_look" not in stages
    # The raw email is kept, encrypted, in the file store.
    async with tenant_session(tid) as s:
        row = await s.get(InboundEmail, uuid.UUID(out["email_id"]))
        raw = await s.get(StoredFile, row.raw_file_id)
        assert raw.kind == "raw_email" and raw.size_bytes == row.size_bytes
    counts = (await client.get(f"{E}/counts", headers=h)).json()
    assert counts["review"] == 1 and counts["unassigned"] == 1


def test_rules_read_hindi_and_telugu_requests_as_enquiries():
    from datetime import date

    hi = rules.read(subject="कोटेशन", body="हमें 20 MT TMT बार Fe 500D चाहिए। कृपया रेट भेजें।",
                    from_address="a@b.in", from_name="A", docs=[], families=[],
                    today=date(2026, 10, 12))
    assert hi["label"] == "enquiry" and hi["language"] == "hi"
    assert hi["lines"][0]["qty"] == 20 and hi["lines"][0]["unit"] == "MT"
    te = rules.read(subject="", body="మాకు 500 nos ఫ్లాంజ్‌లు కావాలి. ధర తెలియజేయండి.",
                    from_address="a@b.in", from_name="A", docs=[], families=[],
                    today=date(2026, 10, 12))
    assert te["label"] == "enquiry" and te["language"] == "te"
    pay = rules.read(subject="Payment done", body="We have paid invoice 4471 by NEFT, UTR "
                     "SBIN0012345.", from_address="a@b.in", from_name="A", docs=[],
                     families=[], today=date(2026, 10, 12))
    assert pay["label"] == "payment" and pay["lines"] == []


@pytest.mark.parametrize(("line", "qty", "unit"), [
    ("Pipe rack 12m - 5 Nos", 5, "nos"),  # a length is the size, not the quantity
    ("Shed structure 30m x 15m - 25 Sets", 25, "sets"),
    ("SS 304 Sheet 2mm 10 Pcs", 10, "nos"),  # 304 is the grade
    ("1 | Pipe rack | 12m | - | 4 | Pcs", 4, "nos"),
    ("We need 20 MT HR Coil 3mm x 1250", 20, "MT"),
])
def test_rules_take_the_quantity_not_the_size_or_grade(line, qty, unit):
    got = rules.lines_in(line)
    assert len(got) == 1 and got[0]["qty"] == qty and got[0]["unit"] == unit


def test_rules_read_the_signature_after_the_sign_off():
    text = ("Hello,\nWe need a quote for:\n1. Fabricated pipe rack - 12 MT\n"
            "Material IS 2062 E350, IBR approved.\nThanks,\nAnil Varma\nProject Engineer\n"
            "Deccan Process Plants Pvt Ltd\n98480 11223")
    got = rules.customer_in(text, "anil@dpp.example.in", "Anil Varma")
    assert got["designation"] == "Project Engineer"
    assert got["company"] == "Deccan Process Plants Pvt Ltd"
    assert got["phone"] == "+919848011223"
    te = "దయచేసి కోట్ పంపండి\nధన్యవాదాలు,\nరవి\nPurchase Manager\nSri Sai Steels"
    assert rules.customer_in(te, None, None)["designation"] == "Purchase Manager"


def test_the_guard_finds_promises_and_lets_questions_through():
    assert guard.claims("The price is Rs 52,000 per MT") == ["a price"]
    assert guard.claims("We can offer a special discount of 5%") == ["a discount"]
    assert guard.claims("We will deliver it within 10 days") == [
        "a delivery date", "that we can make or supply it"]
    assert guard.claims("The material is in stock") == ["that we can make or supply it"]
    assert guard.claims("कीमत 52000 रुपये है") == ["a price"]
    assert guard.claims("रेट: रु. 52000 प्रति टन") == ["a price"]
    assert guard.claims("ధర రూ. 52000") == ["a price"]
    # A customer's subject quoted back with the reference is not a price
    assert guard.claims("Re: MS Angle का रेट [ENQ-2026-00012]") == []
    assert guard.claims("Re: MS Plate ధర [ENQ-2026-00012]") == []
    ok = ("Thank you for your enquiry. Your reference is ENQ-2026-00001. Where should it be "
          "delivered (city and pincode)? By when do you need it? How much do you need?")
    assert guard.claims(ok) == []


# Reading with the AI ------------------------------------------------------------------

async def test_an_ai_read_enquiry_gets_a_contact_an_owner_and_an_acknowledgement(
        client, ctx, ai):
    h, tid = ctx["h"], ctx["tid"]
    seller, _ = await _seller(client, ctx)
    model = ai(reading())
    out = await _mail(client, h)
    assert out["outcome"] == "enquiry" and out["label_source"] == "model"
    # The prompt marks the email as data from outside, between markers.
    system, user = model.calls[0][0]["content"], model.calls[0][1]["content"]
    assert "DATA from an outside sender" in system and user.startswith("<<<EMAIL")
    e = await _enquiry(client, h, out["enquiry_id"])
    assert e["needs_review"] is False and len(e["lines"]) == 3
    assert e["lines"][1]["qty"] == "12.000"
    assert e["contact"]["full_name"] == "Ravi Kumar"
    assert e["account"]["name"] == "Sri Venkateswara Engineering Pvt Ltd"
    assert e["owner_id"] == str(seller) and e["assigned_rule"] == "round_robin"
    assert e["assignments"][0]["reason"].startswith("Seller One is next in the team")
    # Acknowledged at once (standard account): grade, size, quantity, place and date
    # were given, so the general checklist asks only what is missing.
    sent = [m for m in e["messages"] if m["direction"] == "out"]
    assert len(sent) == 1 and sent[0]["status"] == "queued"
    assert sent[0]["provider"] == "simulator"  # the Test mailbox answers in the Test inbox
    assert sent[0]["subject"] == f"Re: RFQ for MS plates and beams [{e['ref']}]"
    assert e["ref"] in sent[0]["body"] and "Dear Ravi Kumar," in sent[0]["body"]
    assert sent[0]["ai_generated"] and sent[0]["disclosure"] == "Sent by our AI assistant"
    assert guard.claims(sent[0]["body"]) == []
    assert e["status"] == "open" and e["questions"] == []
    assert await send.deliver_due(tid) == {"sent": 1}
    e = await _enquiry(client, h, out["enquiry_id"])
    fr = next(t for t in e["timers"] if t["stage"] == "first_response")
    assert fr["outcome"] == "met" and e["first_response_at"]
    # The owner opens it: their first look is done.
    _, sh = await _seller_login(client, ctx, "Seller One")
    r = await client.post(f"{E}/{e['id']}/seen", headers=sh)
    assert r.json() == {"first_look": True}
    e = await _enquiry(client, h, out["enquiry_id"])
    assert next(t for t in e["timers"] if t["stage"] == "first_look")["outcome"] == "met"


async def _seller_login(client, ctx, name):
    from tests.conftest import login

    email = f"{name.lower().replace(' ', '')}@{ctx['slug']}.example.com"
    return None, await login(client, ctx["slug"], email, "Passw0rd!x")


async def test_missing_details_are_asked_and_the_reply_ticks_them_off(client, ctx, ai):
    h, tid = ctx["h"], ctx["tid"]
    await _seller(client, ctx)
    ai(reading(lines=[{"description": "MS plates", "confidence": 0.8}], given=["product"],
               target_date=None, target_date_text=None, delivery_location=None),
       reading(label="enquiry", lines=[], given=["qty", "delivery_location"],
               target_date=None, target_date_text=None))
    out = await _mail(client, h)
    e = await _enquiry(client, h, out["enquiry_id"])
    assert e["status"] == "waiting_customer" and e["waiting_on"] == "customer"
    assert [q["key"] for q in e["questions"]] == ["qty", "delivery_location", "required_date"]
    ack_msg = next(m for m in e["messages"] if m["direction"] == "out")
    assert "1. How much do you need?" in ack_msg["body"]
    quote = next(t for t in e["timers"] if t["stage"] == "quote")
    assert quote["paused"] is True
    await send.deliver_due(tid)
    # The customer answers in the same thread.
    out2 = await _mail(client, h, subject=f"Re: RFQ [{e['ref']}]",
                       body="Qty 40 MT, delivery at Patancheru.", reply_to=ack_msg["id"])
    assert out2["outcome"] == "reply" and out2["enquiry_id"] == e["id"]
    e = await _enquiry(client, h, e["id"])
    answered = {q["key"]: q["answered_at"] for q in e["questions"]}
    assert answered["qty"] and answered["delivery_location"] and not answered["required_date"]
    assert e["status"] == "open" and e["waiting_on"] == "us"
    assert next(t for t in e["timers"] if t["stage"] == "quote")["running"] is True
    assert [ev["kind"] for ev in e["events"]][-1] == "customer_replied"
    assert len(e["emails"]) == 2


async def test_tenders_and_strategic_accounts_are_drafted_for_a_person(client, ctx, ai):
    h, tid = ctx["h"], ctx["tid"]
    await _seller(client, ctx)
    ai(reading(is_tender=True, tender_ref="NIT/2026/77"))
    out = await _mail(client, h, subject="Tender NIT/2026/77 for structural steel")
    e = await _enquiry(client, h, out["enquiry_id"])
    assert e["is_tender"] and [m for m in e["messages"] if m["direction"] == "out"] == []
    cards = (await client.get(f"{API}/decisions?status=pending", headers=h)).json()
    card = next(c for c in cards if c["tool_key"] == "enquiry.acknowledge")
    assert "Tenders are always drafted for a person" in card["reasons"][0]
    assert card["record_type"] == "enquiry" and card["record_id"] == e["id"]
    # A strategic account's enquiry starts at L0: a person sends it.
    async with tenant_session(tid) as s:
        acc = Account(tenant_id=tid, name="Bharat Heavy Fab", tier="strategic",
                      normalized_name="bharat heavy fab")
        s.add(acc)
        await s.flush()
        s.add(Contact(tenant_id=tid, account_id=acc.id, full_name="Meena Rao",
                      email="meena@bhf.example.in", normalized_name="meena rao"))
    ai(reading(customer={"company": "Bharat Heavy Fab", "contact_name": "Meena Rao",
                         "email": "meena@bhf.example.in"}))
    out = await _mail(client, h, sender="meena@bhf.example.in", name="Meena Rao",
                      subject="Enquiry for plates")
    e = await _enquiry(client, h, out["enquiry_id"])
    assert e["account_tier"] == "strategic" and e["contact"]["full_name"] == "Meena Rao"
    cards = (await client.get(f"{API}/decisions?status=pending", headers=h)).json()
    card = next(c for c in cards if c["tool_key"] == "enquiry.acknowledge"
                and c["record_id"] == e["id"])
    assert any("L0" in r for r in card["reasons"])


async def test_no_message_about_an_enquiry_may_promise_a_price(client, ctx, ai):
    h = ctx["h"]
    await _seller(client, ctx)
    ai(reading(is_tender=True))
    out = await _mail(client, h, subject="Tender for plates")
    draft = (await client.get(f"{E}/{out['enquiry_id']}/acknowledgement", headers=h)).json()
    assert draft["subject"].endswith(f"[{out['ref']}]")
    r = await client.post(f"{E}/{out['enquiry_id']}/acknowledge", headers=h, json={
        "subject": draft["subject"], "language": "en",
        "body": draft["body"] + "\nOur price is Rs 52,000 per MT, delivered in 10 days."})
    assert r.status_code == 400
    assert "promises a price and a delivery date" in r.json()["error"]["message"]
    r = await client.post(f"{E}/{out['enquiry_id']}/acknowledge", headers=h, json={
        "subject": draft["subject"], "body": draft["body"], "language": "en"})
    assert r.status_code == 200, r.text
    sent = [m for m in r.json()["messages"] if m["direction"] == "out"]
    assert sent[0]["ai_generated"] is False and sent[0]["created_by"].startswith("user:")


async def test_other_mail_is_kept_converted_and_complaints_are_told(client, ctx, ai):
    h, tid = ctx["h"], ctx["tid"]
    ai(reading(label="payment", confidence=0.95, could_be_enquiry=False, lines=[]),
       reading(label="complaint", confidence=0.9, could_be_enquiry=False, lines=[]),
       reading(label="vendor_mail", confidence=0.6, could_be_enquiry=True, lines=[]))
    pay = await _mail(client, h, subject="Payment for invoice 4471", body="Paid by NEFT.")
    assert pay["outcome"] == "other" and pay["label"] == "payment"
    bad = await _mail(client, h, subject="Rusted plates received", body="Half are rusted.")
    assert bad["outcome"] == "other"
    assert await _notices(tid, "A customer complaint arrived")
    # Unsure, and it could be an enquiry: it becomes one, for a person to check.
    maybe = await _mail(client, h, subject="Our products", body="Can you also send rates?")
    assert maybe["outcome"] == "enquiry"
    e = await _enquiry(client, h, maybe["enquiry_id"])
    assert "was not sure (60%)" in e["review_reasons"][0]
    other = (await client.get(f"{E}/mail/other", headers=h)).json()
    assert {o["label"] for o in other} == {"payment", "complaint"}
    # One click makes Other mail an enquiry; a relabel is kept with the original.
    r = await client.post(f"{E}/mail/{pay['email_id']}/label", headers=h,
                          json={"label": "order_status"})
    assert r.json()["original_label"] == "payment" and r.json()["label_source"] == "person"
    ai(reading())
    r = await client.post(f"{E}/mail/{pay['email_id']}/convert", headers=h)
    assert r.status_code == 200, r.text
    conv = await _enquiry(client, h, r.json()["enquiry_id"])
    assert conv["review_reasons"][0] == "Moved from Other mail by a person"
    assert [m for m in conv["messages"] if m["direction"] == "out"] == []  # a person checks
    # And an enquiry that is not one goes back to Other mail with the person's label.
    r = await client.post(f"{E}/{e['id']}/not-enquiry", headers=h, json={"label": "spam"})
    assert r.json()["status"] == "closed"
    assert r.json()["close_reason"] == "Not an enquiry: spam mail"


async def test_a_model_that_cannot_be_reached_is_tried_three_times_then_rules_read_it(
        client, ctx, ai):
    h, tid = ctx["h"], ctx["tid"]
    ai(RuntimeError("timeout"), RuntimeError("timeout"), RuntimeError("timeout"))
    out = await _mail(client, h)
    assert out["outcome"] == "retry"
    now = datetime.now(UTC)
    assert await intake.process_due(tid, now) == {}  # waits a minute before trying again
    assert await intake.process_due(tid, now + timedelta(minutes=2)) == {"retry": 1}
    got = await intake.process_due(tid, now + timedelta(minutes=10))
    assert got == {"enquiry": 1}
    async with tenant_session(tid) as s:
        e = await s.scalar(select(Enquiry))
        assert e.review_reasons[0] == "Not read by AI: the AI could not be reached after 3 tries"


async def test_an_answer_that_does_not_fit_is_asked_for_again(client, ctx, ai):
    h = ctx["h"]
    model = ai("not json at all", reading())
    out = await _mail(client, h)
    e = await _enquiry(client, h, out["enquiry_id"])
    assert e["needs_review"] is False and len(e["lines"]) == 3
    assert "does not fit the form" in model.calls[1][-1]["content"]


# Attachments ---------------------------------------------------------------------------

def _xlsx() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["S.No", "Item", "Size", "Grade", "Qty", "Unit"])
    ws.append([1, "HR Plate", "12 x 2000 x 6300", "IS 2062 E350", 30, "MT"])
    ws.append([2, "Chequered plate", "6 mm", "IS 3502", 5, "MT"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _pdf(text: str) -> bytes:
    """A one-page PDF with real text (no library writes one, so by hand)."""
    stream = f"BT /F1 12 Tf 50 750 Td ({text}) Tj ET".encode()
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


async def test_excel_and_pdf_attachments_are_read_and_kept_encrypted(client, ctx):
    h, tid = ctx["h"], ctx["tid"]
    bom = _xlsx()
    files = [{"filename": "BOM.xlsx", "content_type": "application/vnd.openxmlformats-"
              "officedocument.spreadsheetml.sheet", "data_base64": base64.b64encode(bom).decode()},
             {"filename": "spec.pdf", "content_type": "application/pdf",
              "data_base64": base64.b64encode(_pdf(
                  "Item: Round bar EN8 dia 50 mm - 3 MT required for our machine shop")).decode()},
             {"filename": "drawing.png", "content_type": "image/png",
              "data_base64": base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()}]
    out = await _mail(client, h, subject="Enquiry - see attached BOM",
                      body="Dear team, please see the attached BOM and quote.\nRegards,\nAnil",
                      files=files)
    e = await _enquiry(client, h, out["enquiry_id"])
    by_file = {}
    for ln in e["lines"]:
        by_file.setdefault(ln["source_ref"], []).append(ln)
    assert [ln["description"] for ln in by_file["BOM.xlsx"]] == [
        "HR Plate 12 x 2000 x 6300 IS 2062 E350", "Chequered plate 6 mm IS 3502"]
    assert by_file["BOM.xlsx"][0]["qty"] == "30.000" and by_file["BOM.xlsx"][0]["source"] == \
        "attachment"
    assert by_file["spec.pdf"][0]["qty"] == "3.000"
    assert any("drawing.png: Not read" in r or "drawing.png" in r for r in e["review_reasons"])
    email = e["emails"][0]
    assert [a["filename"] for a in email["attachments"]] == ["BOM.xlsx", "spec.pdf",
                                                             "drawing.png"]
    fid = email["attachments"][0]["file_id"]
    r = await client.get(f"{E}/files/{fid}", headers=h)
    assert r.status_code == 200 and r.content == bom
    assert r.headers["content-disposition"] == 'attachment; filename="BOM.xlsx"'
    async with tenant_session(tid) as s:
        f = await s.get(StoredFile, uuid.UUID(fid))
        from app.files.store import store as file_store

        assert bom[:50] not in file_store().get(f.storage_key)  # encrypted at rest



async def test_a_drawing_is_read_unreadable_parts_flagged_and_corrections_teach_the_reader(
        client, ctx, ai):
    h = ctx["h"]
    await _seller(client, ctx)
    model = ai(
        # The picture model writes out the drawing; the quantity is smudged.
        "Title block: DRG-114 Rev B\nItem 1: Flange 150 NB, IS 6392, qty unreadable",
        reading(lines=[{"description": "Flange 150 NB", "standard": "IS 6392", "qty": None,
                        "unsure": ["qty", "colour"], "confidence": 0.6,
                        "source": "attachment", "source_file": "drawing.png"}],
                given=["product", "grade"]),
        reading())
    routing.ROUTES["vision"] = [routing.Route("fake", "v1")]
    out = await _mail(client, h, subject="Enquiry as per drawing",
                      body="Please quote as per the attached drawing.\nRegards,\nAnil",
                      files=[{"filename": "drawing.png", "content_type": "image/png",
                              "data_base64": base64.b64encode(b"\x89PNG\r\n\x1a\nx").decode()}])
    picture = model.calls[0][0]["content"]
    assert picture[1]["type"] == "image_url" and "say 'unreadable'" in picture[0]["text"]
    assert "unreadable" in model.calls[1][1]["content"]  # the transcription is read too
    e = await _enquiry(client, h, out["enquiry_id"])
    line = e["lines"][0]
    assert line["qty"] is None and line["unsure"] == ["qty"]  # left empty, not guessed
    assert line["source_ref"] == "drawing.png"
    assert "drawing.png: parts could not be read and were left empty" in e["review_reasons"]
    assert any("not sure of some details on 1 line" in r for r in e["review_reasons"])
    # A person corrects it: the line as read stays, and the next reading learns from it.
    r = await client.patch(f"{E}/{e['id']}/lines/{line['id']}", headers=h, json={
        "description": "Weld neck flange 150 NB", "standard": "IS 6392", "qty": 40,
        "unit": "nos"})
    assert r.status_code == 200 and r.json()["unsure"] == []
    assert r.json()["original"]["description"] == "Flange 150 NB"
    await _mail(client, h, sender="meena@kaveri.example.in", name="Meena")
    user = model.calls[2][1]["content"]
    assert user.startswith("<<<CORRECTIONS") and "<<<EMAIL" in user
    learnt = json.loads(user.split("\n")[1])
    assert learnt == [{"read": {"description": "Flange 150 NB", "standard": "IS 6392"},
                       "corrected_to": {"description": "Weld neck flange 150 NB",
                                        "qty": "40.000", "unit": "nos"}}]
    assert "CORRECTIONS, when there are any" in model.calls[2][0]["content"]

# Routing ----------------------------------------------------------------------------------

async def test_routing_owner_away_product_line_region_and_round_robin(client, ctx, ai):
    h, tid = ctx["h"], ctx["tid"]
    fam = (await client.post(f"{E}/setup/families", headers=h,
                             json={"template": "steel"})).json()
    owner, _ = await _seller(client, ctx, "Owner Olga", takes=True)
    spec, _ = await _seller(client, ctx, "Steel Sam", families=[fam["id"]])
    south, _ = await _seller(client, ctx, "South Suma", regions=["Telangana"])
    stand, _ = await _seller(client, ctx, "Stand In Sid")
    async with tenant_session(tid) as s:
        acc = Account(tenant_id=tid, name="Owned Ltd", owner_id=owner,
                      normalized_name="owned ltd")
        s.add(acc)
        await s.flush()
        s.add(Contact(tenant_id=tid, account_id=acc.id, full_name="Olu",
                      email="olu@owned.example.in", normalized_name="olu"))
    ai(reading(customer={"email": "olu@owned.example.in"}),
       reading(product_family="Steel sections and plates",
               customer={"company": "New Co", "contact_name": "N", "state": "Gujarat"}),
       reading(customer={"company": "Deccan Co", "contact_name": "D", "state": "Telangana"},
               lines=[{"description": "Gaskets", "qty": 5, "unit": "nos"}]),
       reading(customer={"company": "Elsewhere", "contact_name": "X", "state": "Kerala"},
               lines=[{"description": "Gaskets", "qty": 5, "unit": "nos"}]))
    one = await _mail(client, h, sender="olu@owned.example.in", subject="Need plates")
    two = await _mail(client, h, sender="n@newco.example.in", subject="Need beams")
    three = await _mail(client, h, sender="d@deccan.example.in", subject="Gaskets",
                        body="Please quote 5 nos rubber gaskets.\nRegards,\nD")
    four = await _mail(client, h, sender="x@else.example.in", subject="Gaskets please",
                       body="Please quote 5 nos rubber gaskets.\nRegards,\nX")
    rules_ = {}
    for o in (one, two, three, four):
        e = await _enquiry(client, h, o["enquiry_id"])
        rules_[e["ref"]] = (e["owner_name"], e["assigned_rule"])
    assert list(rules_.values())[:3] == [("Owner Olga", "account_owner"),
                                        ("Steel Sam", "product_line"),
                                        ("South Suma", "region")]
    assert list(rules_.values())[3][1] == "round_robin"
    assert list(rules_.values())[3][0] in ("Stand In Sid",)  # the only one with none open
    # The account owner is away: their stand-in takes it.
    from app.models import Delegation

    async with tenant_session(tid) as s:
        s.add(Delegation(tenant_id=tid, kind="away", from_user_id=owner, to_user_id=stand,
                         created_by="test"))
    ai(reading(customer={"email": "olu@owned.example.in"}, lines=[
        {"description": "Something else entirely", "qty": 1, "unit": "nos"}]))
    five = await _mail(client, h, sender="olu@owned.example.in", subject="Another one")
    e = await _enquiry(client, h, five["enquiry_id"])
    assert e["owner_name"] == "Stand In Sid" and e["assigned_rule"] == "away"
    # Reassigning needs a reason; three overrides of a rule in 30 days suggest a look.
    r = await client.post(f"{E}/{two['enquiry_id']}/assign", headers=h,
                          json={"user_id": str(owner), "reason": ""})
    assert r.status_code == 422
    by_line = [two]
    for i in (2, 3):
        ai(reading(product_family="Steel sections and plates",
                   customer={"company": f"New Co {i}", "contact_name": "N", "state": "Gujarat"}))
        by_line.append(await _mail(client, h, sender=f"n{i}@newco{i}.example.in",
                                   subject=f"Need beams {i}"))
    for n, o in enumerate(by_line, 1):
        assert (await _enquiry(client, h, o["enquiry_id"]))["assigned_rule"] == "product_line"
        r = await client.post(f"{E}/{o['enquiry_id']}/assign", headers=h,
                              json={"user_id": str(south), "reason": "Knows this customer"})
        assert r.status_code == 200, r.text
        got = (await client.get(f"{E}/setup/settings", headers=h)).json()
        assert [x["rule"] for x in got["suggestions"]] == (["product_line"] if n == 3 else [])
    # Changing the owner again counts once: the second change overrides a person, not a rule.
    r = await client.post(f"{E}/{two['enquiry_id']}/assign", headers=h,
                          json={"user_id": str(spec), "reason": "Back to Sam"})
    got = (await client.get(f"{E}/setup/settings", headers=h)).json()
    assert got["suggestions"][0]["overrides"] == 3


# Timers -----------------------------------------------------------------------------------

async def test_timers_remind_the_owner_then_tell_the_manager_and_head(client, ctx, ai):
    h, tid = ctx["h"], ctx["tid"]
    seller, _ = await _seller(client, ctx, "Timed Tara")
    boss, _ = await _seller(client, ctx, "Boss Bala", takes=False)
    head, _ = await _seller(client, ctx, "Head Hari", takes=False)
    for who, to in ((seller, boss), (boss, head)):
        r = await client.put(f"{E}/setup/people/{who}", headers=h, json={
            "takes_enquiries": who == seller, "reports_to_id": str(to)})
        assert r.status_code == 200, r.text
    ai(reading(is_tender=True))  # drafted for a person: the first-response clock runs
    out = await _mail(client, h)
    async with tenant_session(tid) as s:
        t = await s.scalar(select(SlaTimer).where(SlaTimer.stage == "first_response"))
        start = t.started_at
    async with tenant_session(tid) as s:
        await timers.tick(s, tid, start + timedelta(minutes=12))
    assert await _notices(tid, "first response due by")
    async with tenant_session(tid) as s:
        await timers.tick(s, tid, start + timedelta(minutes=16))
    missed = await _notices(tid, "missed its first response")
    assert missed and missed[0]["user_ids"] == [str(boss)]
    async with tenant_session(tid) as s:
        await timers.tick(s, tid, start + timedelta(minutes=31))
    hod = await _notices(tid, "twice the time for its first response")
    assert hod and hod[0]["user_ids"] == [str(head)]
    e = await _enquiry(client, h, out["enquiry_id"])
    assert [ev["detail"]["level"] for ev in e["events"] if ev["kind"] == "escalated"] == [
        "manager", "head_of_department"]
    # Nothing is said twice.
    async with tenant_session(tid) as s:
        assert await timers.tick(s, tid, start + timedelta(minutes=40)) == {}


# Duplicates ----------------------------------------------------------------------------------

async def test_sure_duplicates_merge_others_are_flagged_and_people_decide(client, ctx, ai):
    h = ctx["h"]
    await _seller(client, ctx)
    ai(reading(), reading(), reading(customer={"company": "Sri Venkateswara Engineering "
                                                          "Pvt Ltd", "contact_name": "Other",
                                               "phone": "+91 90000 11111"},
                                     lines=[{"description": "ISMB 200", "qty": 12,
                                             "unit": "MT"}]))
    first = await _mail(client, h)
    # Sent again from the same phone with the same items: merged on its own.
    again = await _mail(client, h, sender="ravi.k@gmail.com", subject="RFQ again")
    e2 = await _enquiry(client, h, again["enquiry_id"])
    assert e2["status"] == "merged" and e2["merged_into"]["id"] == first["enquiry_id"]
    e1 = await _enquiry(client, h, first["enquiry_id"])
    assert [m["ref"] for m in e1["merged_in"]] == [e2["ref"]]
    assert len([m for m in e1["messages"] if m["direction"] == "out"]) == 1  # one thank-you
    # Same company, different phone, overlapping item: flagged for a person.
    third = await _mail(client, h, sender="other@sve.example.in", subject="Beams")
    e3 = await _enquiry(client, h, third["enquiry_id"])
    assert e3["possible_duplicate"]["of_id"] == first["enquiry_id"]
    r = await client.post(f"{E}/{e3['id']}/duplicate", headers=h,
                          json={"other_id": first["enquiry_id"], "same": False})
    assert r.json()["possible_duplicate"] is None
    # Undo the automatic merge.
    r = await client.post(f"{E}/{e2['id']}/undo-merge", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] in ("open", "new") and r.json()["merged_into"] is None


# Nudges and parking ------------------------------------------------------------------------------

async def test_waiting_enquiries_are_nudged_twice_then_parked(client, ctx, ai):
    h, tid = ctx["h"], ctx["tid"]
    await _seller(client, ctx)
    ai(reading(lines=[{"description": "MS plates"}], given=["product"], target_date=None,
               target_date_text=None, delivery_location=None))
    out = await _mail(client, h)
    async with tenant_session(tid) as s:
        e = await s.get(Enquiry, uuid.UUID(out["enquiry_id"]))
        since = e.waiting_since
    for days, want in ((1, {}), (2, {"done": 1}), (3, {}), (5, {"done": 1}), (7, {"parked": 1})):
        async with tenant_session(tid) as s:
            assert await intake.followups(s, tid, since + timedelta(days=days, minutes=1)) \
                == want, days
    e = await _enquiry(client, h, out["enquiry_id"])
    assert e["status"] == "parked" and e["nudges"] == 2
    sent = [m for m in e["messages"] if m["direction"] == "out"]
    assert len(sent) == 3 and "gentle reminder" in sent[1]["body"]
    r = await client.post(f"{E}/{e['id']}/reopen", headers=h)
    assert r.json()["status"] == "open"


# People's own enquiries --------------------------------------------------------------------------

async def test_a_manual_enquiry_is_routed_and_its_quote_timer_runs(client, ctx):
    h = ctx["h"]
    await _seller(client, ctx)
    r = await client.post(E, headers=h, json={
        "customer": {"company": "Walk-in Fabricators", "contact_name": "Suresh"},
        "summary": "Needs 10 MT of angles", "received_how": "Phone call",
        "lines": [{"description": "ISA 65x65x6", "qty": 10, "unit": "MT"}]})
    assert r.status_code == 201, r.text
    e = r.json()
    assert e["source"] == "manual" and e["owner_name"] == "Seller One"
    assert e["first_response_at"] and e["lines"][0]["source"] == "person"
    r = await client.post(f"{E}/{e['id']}/quote-sent", headers=h)
    q = next(t for t in r.json()["timers"] if t["stage"] == "quote")
    assert q["outcome"] == "met" and r.json()["quote_sent_at"]
    r = await client.post(f"{E}/{e['id']}/close", headers=h, json={"reason": "Ordered"})
    assert r.json()["status"] == "closed"
    # A correction keeps the line as first read.
    r = await client.post(E, headers=h, json={
        "customer": {"company": "X"}, "lines": [{"description": "Plate"}, {"description": "Bar"}]})
    assert r.json()["summary"] == "Plate and 1 more"  # no summary: the first line stands in
    line = r.json()["lines"][0]
    r = await client.patch(f"{E}/{r.json()['id']}/lines/{line['id']}", headers=h,
                           json={"description": "Plate 12 mm", "qty": 4, "unit": "MT"})
    assert r.json()["corrected_by"] and r.json()["qty"] == "4.000"
    # The sender of an email enquiry can be added to contacts in one step.
    out = await _mail(client, h, sender="new.buyer@kaveri.example.in", name="Anand Rao",
                      subject="Need angles", body="Please quote ISA 65x65x6 - 10 MT.\n"
                      "Regards,\nAnand Rao\nKaveri Structurals Pvt Ltd")
    e = await _enquiry(client, h, out["enquiry_id"])
    assert e["contact"] is None
    r = await client.post(f"{E}/{e['id']}/add-customer", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["contact"]["email"] == "new.buyer@kaveri.example.in"
    assert r.json()["account"]["name"] == "Kaveri Structurals Pvt Ltd"
    assert (await client.post(f"{E}/{e['id']}/add-customer", headers=h)).status_code == 400


async def test_reading_needs_permissions(client, ctx):
    _, h = await _user_with_role(client, ctx, "Reader Only", ["enquiries.read"])
    r = await client.post(f"{E}/test-mailbox", headers=h, json={
        "from_address": "a@b.in", "subject": "x", "body": "y"})
    assert r.status_code == 403
    assert (await client.get(E, headers=h)).status_code == 200
    r = await client.put(f"{E}/setup/settings/timers", headers=h, json={"reminder_pct": 50})
    assert r.status_code == 403


# Mail plumbing ------------------------------------------------------------------------------------

async def test_bounces_of_our_messages_become_receipts_and_our_own_copies_are_set_aside(
        client, ctx, ai):
    h, tid = ctx["h"], ctx["tid"]
    await _seller(client, ctx)
    ai(reading())
    out = await _mail(client, h)
    await send.deliver_due(tid)
    async with tenant_session(tid) as s:
        msg = await s.scalar(select(Message).where(Message.direction == "out"))
        ref = msg.provider_ref
    bounce = (
        "From: MAILER-DAEMON@mx.example.in\r\nTo: forge@simulator.test\r\n"
        "Subject: Undelivered Mail Returned to Sender\r\nMessage-ID: <b1@mx>\r\n"
        "Content-Type: multipart/report; report-type=delivery-status; boundary=XX\r\n\r\n"
        "--XX\r\nContent-Type: text/plain\r\n\r\nIt failed.\r\n"
        "--XX\r\nContent-Type: message/delivery-status\r\n\r\n"
        "Reporting-MTA: dns; mx\r\n\r\nFinal-Recipient: rfc822; ravi@sve.example.in\r\n"
        "Action: failed\r\nStatus: 5.1.1\r\nDiagnostic-Code: smtp; 550 No such user\r\n"
        f"--XX\r\nContent-Type: text/rfc822-headers\r\n\r\nMessage-ID: {ref}\r\n\r\n--XX--\r\n"
    ).encode()
    async with tenant_session(tid) as s:
        row, new = await intake.ingest(s, tid, bounce, provider="imap", provider_id="1:7")
        assert new and row.outcome == "receipt" and row.stage == "done"
        again, new = await intake.ingest(s, tid, bounce, provider="imap", provider_id="1:7")
        assert not new and again.id == row.id
    async with tenant_session(tid) as s:
        msg = await s.scalar(select(Message).where(Message.direction == "out"))
        assert msg.status == "bounced"
    own = b"From: forge@simulator.test\r\nTo: x@y.in\r\nSubject: copy\r\n\r\nhi\r\n"
    async with tenant_session(tid) as s:
        row, _ = await intake.ingest(s, tid, own, provider="imap", provider_id="1:8")
        assert row.auto_kind == "own" and row.outcome == "auto"
    assert out["enquiry_id"]


async def test_imap_reads_only_new_mail_and_keeps_its_place(client, ctx, monkeypatch):
    from app.enquiries.mail import imap
    from app.enquiries.mail import service as mail

    h, tid = ctx["h"], ctx["tid"]
    box = {"validity": 7, "uids": {}}

    class Fake:
        def __init__(self, *a):
            pass

        def login(self, *a):
            return "OK", []

        def status(self, folder, what):
            nxt = max(box["uids"], default=40) + 1
            return "OK", [f'"INBOX" (UIDVALIDITY {box["validity"]} UIDNEXT {nxt})'.encode()]

        def select(self, folder, readonly=False):
            assert readonly
            return "OK", [b"1"]

        def uid(self, cmd, *args):
            if cmd == "SEARCH":
                lo = int(args[1].split()[1].split(":")[0])
                return "OK", [" ".join(str(u) for u in sorted(box["uids"]) if u >= lo).encode()]
            assert args[1] == "(BODY.PEEK[])"  # never marks mail as read
            return "OK", [(b"1 (BODY[] {10}", box["uids"][int(args[0])]), b")"]

        def logout(self):
            return "BYE", []

    monkeypatch.setattr(imap, "connect", lambda *a: Fake())
    r = await client.put(f"{E}/setup/mailbox/imap", headers=h, json={
        "address": "sales@kpl.example.in", "imap_host": "imap.kpl.example.in",
        "password": "app-password-1234"})
    assert r.status_code == 200, r.text
    assert r.json()["mailbox"]["secret_hint"] == "…1234"
    assert "password" not in json.dumps(r.json()).replace("password-", "")
    old = b"From: a@b.in\r\nSubject: old\r\nMessage-ID: <old@b>\r\n\r\nold mail\r\n"
    box["uids"][40] = old
    assert await mail.check_all() == {"checked": 1, "kept": 0, "errors": 0}  # notes its place
    new = b"From: c@d.in\r\nSubject: RFQ\r\nMessage-ID: <new@d>\r\n\r\nNeed 5 MT plates\r\n"
    box["uids"][41] = new
    async with tenant_session(tid) as s:
        from app.models import Mailbox

        mb = await s.scalar(select(Mailbox))
        out = await mail.check(s, mb)
    assert out == {"kept": 1, "more": False}
    async with tenant_session(tid) as s:
        rows = list(await s.scalars(select(InboundEmail)))
        assert [r.subject for r in rows] == ["RFQ"] and rows[0].provider_id == "7:41"


async def test_gmail_and_microsoft_read_through_their_apis(client, ctx, monkeypatch):
    import httpx

    from app.config import get_settings
    from app.enquiries.mail import oauth
    from app.enquiries.mail import service as mail

    h, tid = ctx["h"], ctx["tid"]
    s_ = get_settings()
    monkeypatch.setattr(s_, "google_client_id", "gid")
    monkeypatch.setattr(s_, "google_client_secret", "gsecret")
    raw = b"From: buyer@x.in\r\nSubject: Need flanges\r\nMessage-ID: <g1@x>\r\n\r\n100 nos\r\n"
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        u = str(req.url)
        if u.startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600,
                                             "refresh_token": "rt-secret-9999"})
        if u.endswith("/profile"):
            return httpx.Response(200, json={"emailAddress": "Sales@KPL.example.in",
                                             "historyId": "100"})
        if "/history" in u:
            return httpx.Response(200, json={"historyId": "105", "history": [
                {"messagesAdded": [{"message": {"id": "m1"}}]}]})
        if "/messages/m1" in u:
            return httpx.Response(200, json={"raw": base64.urlsafe_b64encode(raw).decode(),
                                             "internalDate": "1760000000000"})
        return httpx.Response(404)

    monkeypatch.setattr(oauth, "transport", httpx.MockTransport(handler))
    r = await client.post(f"{E}/setup/mailbox/oauth/gmail", headers=h)
    url = r.json()["url"]
    assert url.startswith("https://accounts.google.com/") and "gmail.readonly" in url
    state = url.split("state=")[1].split("&")[0]
    r = await client.get(f"{API}/public/mail/oauth/gmail/callback?code=c1&state={state}",
                         follow_redirects=False)
    assert r.status_code == 303 and "connected=gmail" in r.headers["location"]
    got = (await client.get(f"{E}/setup/mailbox", headers=h)).json()["mailbox"]
    assert got["address"] == "sales@kpl.example.in" and got["provider"] == "gmail"
    assert "rt-secret" not in json.dumps(got)
    assert (await mail.check_all())["checked"] == 1  # notes history id 100
    async with tenant_session(tid) as s:
        from app.models import Mailbox

        mb = await s.scalar(select(Mailbox))
        assert mb.cursor == {"history_id": "100"}
        assert await mail.check(s, mb) == {"kept": 1, "more": False}
        assert mb.cursor == {"history_id": "105"}
    # A bad state is refused.
    r = await client.get(f"{API}/public/mail/oauth/gmail/callback?code=c1&state=nope",
                         follow_redirects=False)
    assert "error=" in r.headers["location"]


async def test_erasing_a_contact_blanks_their_enquiry_emails_and_files(client, ctx, ai):
    from app.privacy.erasure import erase_contact

    h, tid = ctx["h"], ctx["tid"]
    await _seller(client, ctx)
    ai(reading())
    out = await _mail(client, h, files=[{"filename": "bom.txt", "content_type": "text/plain",
                                         "data_base64": base64.b64encode(
                                             b"ISMB 200 - 12 MT").decode()}])
    e = await _enquiry(client, h, out["enquiry_id"])
    async with tenant_session(tid) as s:
        res = await erase_contact(s, tid, uuid.UUID(e["contact"]["id"]), actor="test",
                                  reason="asked", requested_by="test")
    assert res["stores"]["emails"] == 1 and res["stores"]["email_files"] == 2
    e = await _enquiry(client, h, out["enquiry_id"])
    assert e["customer"]["contact_name"] == "[erased]" and e["customer"]["phone"] == "[erased]"
    assert e["emails"][0]["body"] == "[erased]" and e["emails"][0]["from_address"] is None
    r = await client.get(f"{E}/files/{e['emails'][0]['attachments'][0]['file_id']}", headers=h)
    assert r.status_code == 400


def test_the_readers_form_takes_empty_and_odd_quantities_without_failing():
    from app.enquiries.reader import LineOut

    assert LineOut(description="Plate", qty=None).qty is None
    assert LineOut(description="Plate", qty="25 MT").qty == 25
    assert LineOut(description="Plate", qty=-3).qty is None
    assert LineOut(description="Plate", qty=True).qty is None
    assert LineOut(description="Plate", unsure=["QTY", "colour", "unit"]).unsure == [
        "qty", "unit"]
