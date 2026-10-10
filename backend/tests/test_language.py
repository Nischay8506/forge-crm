"""Languages (F12.5, Step 6): recognising the language, tone guides, the glossary, the
native writer and its rewrite, language reviews and speech to text (L5-L7, L10, L11)."""
# ruff: noqa: F811  (the ctx fixture is imported from test_agents)

import json
import uuid

import pytest
from sqlalchemy import select

from app.channels import send
from app.db import tenant_session
from app.language import glossary
from app.language.script import detect
from app.llm import gateway, routing
from app.llm.providers import ProviderResponse, TranscriptResponse
from app.models import AuditRecord, LanguageReview, Message
from tests.test_agents import (  # noqa: F401
    API,
    _user_with_role,
    ctx,
    steward_handlers,
)
from tests.test_channels import OPEN_HOURS, _setup


@pytest.mark.parametrize(("text", "lang", "sure"), [
    ("Please send the quotation for 20 pumps by Friday.", "en", True),
    ("कृपया शुक्रवार तक 20 पंपों का कोटेशन भेजें। धन्यवाद", "hi", True),
    ("कृपया शुक्रवारपर्यंत २० पंपांचे कोटेशन पाठवा. मी वाट पाहत आहे आणि धन्यवाद", "mr", True),
    ("దయచేసి శుక్రవారం లోపు 20 పంపుల కొటేషన్ పంపండి", "te", True),
    ("தயவுசெய்து வெள்ளிக்கிழமைக்குள் 20 பம்புகளுக்கான விலைப்பட்டியலை அனுப்பவும்", "ta", True),
    ("ದಯವಿಟ್ಟು ಶುಕ್ರವಾರದೊಳಗೆ 20 ಪಂಪುಗಳ ಕೊಟೇಶನ್ ಕಳುಹಿಸಿ", "kn", True),
    ("ദയവായി വെള്ളിയാഴ്ചയ്ക്കകം 20 പമ്പുകളുടെ ക്വട്ടേഷൻ അയയ്ക്കുക", "ml", True),
    ("કૃપા કરીને શુક્રવાર સુધીમાં 20 પંપનું ક્વોટેશન મોકલો", "gu", True),
    ("অনুগ্রহ করে শুক্রবারের মধ্যে ২০টি পাম্পের কোটেশন পাঠান", "bn", True),
    ("ਕਿਰਪਾ ਕਰਕੇ ਸ਼ੁੱਕਰਵਾਰ ਤੱਕ 20 ਪੰਪਾਂ ਦਾ ਕੋਟੇਸ਼ਨ ਭੇਜੋ", "pa", True),
    ("ଦୟାକରି ଶୁକ୍ରବାର ସୁଦ୍ଧା 20ଟି ପମ୍ପର କୋଟେସନ ପଠାନ୍ତୁ", "or", True),
    # Telugu with English product names is Telugu; romanised Hindi is read as English (L6).
    ("KX-200 pump order confirm చేయండి, invoice పంపండి", "te", False),
    ("Aap quotation kab bhejenge?", "en", True),
    ("नमस्ते", "hi", False),
])
def test_the_language_is_recognised_from_its_script(text, lang, sure):
    found = detect(text)
    assert found.language == lang
    assert found.sure is sure or lang == "te"


def test_nothing_to_go_on():
    assert detect("1234 !!").language is None and detect("").language is None


def _term(kind, term, language=None, translation=None):
    return type("T", (), {"kind": kind, "term": term, "language": language,
                          "translation": translation})()


def test_the_glossary_and_text_checks_find_what_is_wrong():
    terms = [_term("keep", "KPL Pumps"), _term("forbid", "cheap"),
             _term("translate", "invoice", "hi", "बिल")]
    assert glossary.problems("Order", "Your order from KPL Pumps ships today.", "en", terms,
                             brief="Tell them the KPL Pumps order ships") == []
    p = glossary.problems("Offer", "A cheap offer for you, [Name].", "en", terms,
                          brief="Offer from KPL Pumps")
    assert p == ['"KPL Pumps" must appear exactly as it is', 'Uses the forbidden word "cheap"',
                 "Something is left to fill in: [Name]"]
    p = glossary.problems("बिल", "आपका invoice भेज दिया गया है। धन्यवाद", "hi", terms)
    assert p == ['Writes "invoice"; in Hindi it is always "बिल"']
    p = glossary.problems("Order", "Your order ships today.", "te", terms)
    assert p == ["It is written in English, not Telugu"]


# The writer (L5, L7) ------------------------------------------------------------------------

class Writer:
    """A pretend model: answers in turn from `answers`."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []
        self.name = "fake"

    async def chat(self, model, messages, **kw):
        self.calls.append(messages)
        return ProviderResponse(text=json.dumps(self.answers.pop(0), ensure_ascii=False),
                                input_tokens=100, output_tokens=50)


@pytest.fixture
def model(monkeypatch):
    def use(*answers):
        w = Writer(*answers)
        monkeypatch.setattr(gateway.providers_mod, "PROVIDERS", {"fake": w})
        monkeypatch.setattr(routing, "ROUTES", {"writing": [routing.Route("fake", "w1")],
                                                "speech": [routing.Route("fake", "s1")]})
        return w
    return use


async def test_draft_it_for_me_writes_natively_and_rewrites_once(client, ctx, model):
    h, tid = ctx["h"], ctx["tid"]
    _, cid = await _setup(client, ctx)
    await client.post(f"{API}/languages/glossary", headers=h,
                      json={"term": "sasta", "kind": "forbid"})
    w = model({"subject": "ऑफ़र", "body": "आदरणीय अनीता जी, यह sasta ऑफ़र है।"},
              {"subject": "नया ऑफ़र", "body": "आदरणीय अनीता जी, हमारे नए पंप पर विशेष छूट है। "
               "धन्यवाद"})
    r = await client.post(f"{API}/messages/write", headers=h, json={
        "contact_id": cid, "language": "hi", "purpose": "promotional",
        "brief": "Tell her about 10% off the new pumps this month"})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["status"] == "draft" and d["ai_generated"] and d["language"] == "hi"
    assert d["problems"] == [] and d["rewritten"] is True
    assert d["disclosure"] == "यह संदेश हमारे AI सहायक की मदद से लिखा गया है"
    system = w.calls[0][0]["content"]
    assert "do not write it in English first and translate it" in system
    assert "Never use these words: sasta" in system and "आप" in system
    assert 'Uses the forbidden word "sasta"' in w.calls[1][-1]["content"]
    async with tenant_session(tid) as s:
        calls = list(await s.scalars(select(AuditRecord).where(
            AuditRecord.kind == "model.called", AuditRecord.run_id == uuid.UUID(d["run_id"]))))
    assert [c.data["task"] for c in calls] == ["writer.draft", "writer.rewrite"]

    # The person changes it and sends it; the first Hindi ones are sampled for review,
    # after it has gone (a person already chose to send it).
    r = await client.patch(f"{API}/messages/{d['id']}", headers=h,
                           json={"body": d["body"] + " शुभकामनाएँ"})
    sent = await client.post(f"{API}/messages/{d['id']}/send", headers=h)
    assert sent.json()["status"] == "queued", sent.text
    rev = (await client.get(f"{API}/languages/reviews?language=hi", headers=h)).json()
    assert len(rev) == 1 and rev[0]["why"] == "sample" and rev[0]["blocking"] is False
    report = {x["language"]: x for x in (await client.get(f"{API}/languages",
                                                          headers=h)).json()["report"]}
    assert report["hi"]["waiting"] == 1 and report["hi"]["reviewed"] == 0
    assert report["te"]["target"] == 10 and not report["te"]["met"]
    ok = await client.post(f"{API}/languages/reviews/{rev[0]['id']}", headers=h,
                           json={"verdict": "approve", "score": 4, "comment": "Natural"})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    report = {x["language"]: x for x in (await client.get(f"{API}/languages",
                                                          headers=h)).json()["report"]}
    assert report["hi"]["reviewed"] == 1 and report["hi"]["average_score"] == 4.0


async def test_an_agents_message_that_still_breaks_a_rule_waits_for_a_person(client, ctx,
                                                                            model):
    h, tid = ctx["h"], ctx["tid"]
    _, cid = await _setup(client, ctx, settings={"review_first": 0, "review_every": 1000})
    model({"subject": "Order", "body": "Your order ships today."},  # English, not Telugu
          {"subject": "Order", "body": "Your order ships today, really."})
    r = await client.post(f"{API}/agents/sandbox/send-test-email", headers=h, json={
        "contact_id": cid, "language": "te", "brief": "Tell her the order ships today"})
    assert r.status_code == 200 and r.json()["message_status"] == "review", r.text
    mid = r.json()["message_id"]
    rev = (await client.get(f"{API}/languages/reviews", headers=h)).json()
    assert rev[0]["why"] == "checks" and rev[0]["blocking"]
    assert rev[0]["problems"] == ["It is written in English, not Telugu"]
    _, reviewer = await _user_with_role(client, ctx, "Telugu Reviewer",
                                        ["languages.review", "accounts.read"])
    fixed = "గౌరవనీయులైన అనీత గారికి, మీ ఆర్డర్ ఈరోజు బయలుదేరుతుంది. ధన్యవాదాలు"
    ok = await client.post(f"{API}/languages/reviews/{rev[0]['id']}", headers=reviewer,
                           json={"verdict": "change", "body": fixed, "score": 2})
    assert ok.status_code == 200, ok.text
    assert ok.json()["original_body"] == "Your order ships today, really."
    got = (await client.get(f"{API}/messages/{mid}", headers=h)).json()
    assert got["status"] == "queued" and got["body"] == fixed
    await send.deliver_due(tid)
    assert (await client.get(f"{API}/messages/{mid}", headers=h)).json()["status"] == "sent"
    # A decided review stays decided.
    assert (await client.post(f"{API}/languages/reviews/{rev[0]['id']}", headers=reviewer,
                              json={"verdict": "approve"})).status_code == 400


async def test_a_rejected_review_cancels_the_waiting_message(client, ctx, model):
    h, tid = ctx["h"], ctx["tid"]
    _, cid = await _setup(client, ctx)
    model({"subject": "Hello", "body": "Hello, your order ships today."})
    r = await client.post(f"{API}/agents/sandbox/send-test-email", headers=h,
                          json={"contact_id": cid, "brief": "Say hello", "language": "en"})
    assert r.json()["message_status"] == "review", r.text
    rev = (await client.get(f"{API}/languages/reviews", headers=h)).json()
    no = await client.post(f"{API}/languages/reviews/{rev[0]['id']}", headers=h,
                           json={"verdict": "reject", "comment": "Too short"})
    assert no.json()["status"] == "rejected"
    got = (await client.get(f"{API}/messages/{r.json()['message_id']}", headers=h)).json()
    assert got["status"] == "cancelled"
    assert tid


async def test_writing_without_the_gemini_key_says_what_is_missing(client, ctx):
    _, cid = await _setup(client, ctx)
    r = await client.post(f"{API}/messages/write", headers=ctx["h"], json={
        "contact_id": cid, "language": "te", "purpose": "transactional", "brief": "Order ships"})
    assert r.status_code == 400 and "GEMINI_API_KEY" in r.text
    r = await client.post(f"{API}/messages/write", headers=ctx["h"], json={
        "contact_id": cid, "language": "ta", "purpose": "transactional", "brief": "Order ships"})
    assert r.status_code == 400 and "English, Hindi, Telugu" in r.text


async def test_sampling_reviews_the_first_ten_then_one_in_twenty(ctx):
    from app.language import review

    tid = ctx["tid"]
    async with tenant_session(tid) as s:
        made = []
        for _ in range(30):
            m = Message(tenant_id=tid, channel="email", direction="out", status="draft",
                        purpose="transactional", subject="x", body="y", created_by="test",
                        language="te", ai_generated=True, attempts=0, checks=[])
            s.add(m)
            await s.flush()
            r = await review.maybe_sample(s, m, blocking=False)
            made.append(r is not None)
            m.status = "sent"
    assert made[:10] == [True] * 10 and sum(made[10:]) == 1
    async with tenant_session(tid) as s:
        assert await s.scalar(select(LanguageReview.id).limit(1))


# Tone guides and the glossary (L7) -----------------------------------------------------------

async def test_tone_guides_start_from_defaults_and_are_editable(client, ctx):
    h = ctx["h"]
    langs = (await client.get(f"{API}/languages", headers=h)).json()
    tones = {t["language"]: t for t in langs["tones"]}
    assert set(tones) == {"en", "hi", "te"} and tones["te"]["is_default"]
    assert "గారు" in tones["te"]["notes"]
    assert len(langs["recognised"]) == 11 and langs["writing"] == ["en", "hi", "te"]
    r = await client.put(f"{API}/languages/tone/te", headers=h, json={
        "formality": "friendly", "greeting": "నమస్కారం {name} గారు,", "avoid": ["slang"]})
    assert r.status_code == 200 and not r.json()["is_default"]
    assert (await client.put(f"{API}/languages/tone/ta", headers=h,
                             json={"formality": "formal"})).status_code == 400
    _, clerk = await _user_with_role(client, ctx, "Clerk", ["accounts.read"])
    assert (await client.put(f"{API}/languages/tone/te", headers=clerk,
                             json={"formality": "formal"})).status_code == 403


async def test_glossary_terms_are_checked_and_unique(client, ctx):
    h = ctx["h"]
    ok = await client.post(f"{API}/languages/glossary", headers=h, json={
        "term": "invoice", "kind": "translate", "language": "hi", "translation": "बिल"})
    assert ok.status_code == 201, ok.text
    dup = await client.post(f"{API}/languages/glossary", headers=h, json={
        "term": "Invoice", "kind": "translate", "language": "hi", "translation": "चालान"})
    assert dup.status_code == 400
    bad = await client.post(f"{API}/languages/glossary", headers=h, json={
        "term": "quote", "kind": "translate"})
    assert bad.status_code == 400
    check = (await client.post(f"{API}/messages/check-text", headers=h, json={
        "subject": "बिल", "body": "आपका invoice भेज दिया गया है।", "language": "hi"})).json()
    assert check["detected"] == "hi" and check["problems"] == [
        'Writes "invoice"; in Hindi it is always "बिल"']
    gone = await client.delete(f"{API}/languages/glossary/{ok.json()['id']}", headers=h)
    assert gone.status_code == 204


# Speech to text (L10) --------------------------------------------------------------------------

async def test_dictation_goes_through_the_gateway_with_glossary_hints(client, ctx, monkeypatch):
    h, tid = ctx["h"], ctx["tid"]
    await client.post(f"{API}/languages/glossary", headers=h,
                      json={"term": "KPL Pumps", "kind": "keep"})
    seen = {}

    class Ear:
        name = "fake"

        async def transcribe(self, model, audio, filename, content_type, language, prompt):
            seen.update(model=model, size=len(audio), language=language, prompt=prompt,
                        content_type=content_type)
            return TranscriptResponse(text="మీ ఆర్డర్ ఈరోజు బయలుదేరుతుంది", language="te",
                                      seconds=4.2)

    monkeypatch.setattr(gateway.providers_mod, "PROVIDERS", {"fake": Ear()})
    monkeypatch.setattr(routing, "ROUTES", {"speech": [routing.Route("fake", "whisper")]})
    r = await client.post(f"{API}/languages/transcribe", headers=h, data={"language": "te"},
                          files={"file": ("note.webm", b"\x1aE\xdf\xa3" * 100,
                                          "audio/webm;codecs=opus")})
    assert r.status_code == 200, r.text
    assert r.json()["text"].startswith("మీ ఆర్డర్") and r.json()["language"] == "te"
    assert seen == {"model": "whisper", "size": 400, "language": "te",
                    "prompt": "KPL Pumps", "content_type": "audio/webm"}
    bad = await client.post(f"{API}/languages/transcribe", headers=h,
                            files={"file": ("x.txt", b"hello", "text/plain")})
    assert bad.status_code == 400
    async with tenant_session(tid) as s:
        from app.models import LlmCall

        row = await s.scalar(select(LlmCall).where(LlmCall.task == "speech.dictate"))
        assert row.status == "ok" and row.tier == "speech"
    assert OPEN_HOURS
