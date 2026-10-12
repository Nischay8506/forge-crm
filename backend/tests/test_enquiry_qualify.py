"""M1 Enquiry: 'Can we make this?' (F1.6) and lead scoring (F1.7), with what follows
the check: a decline goes out only after a person approves it (P1 A), an enquiry
needing engineering gets a review with its own timer (P2 A), and the automatic
acknowledgement goes only to standard and not-yet-checked enquiries (P3 A)."""
# ruff: noqa: F811  (the ctx and ai fixtures are imported)

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db import tenant_session
from app.enquiries import capability, qualify, scoring, timers
from app.enquiries.agent import DECLINE_RULE
from app.llm import routing
from app.models import Account, Contact, Enquiry, SlaTimer, User
from tests.test_agents import API, _user_with_role, ctx, decide  # noqa: F401
from tests.test_enquiries import (  # noqa: F401
    E,
    _enquiry,
    _mail,
    _notices,
    _seller,
    ai,
    reading,
)

FLANGES = reading(
    summary="Forged flanges, 150 NB, ASTM A105",
    lines=[{"description": "Forged flange 150 NB", "material": "ASTM A105", "qty": 40,
            "unit": "nos", "confidence": 0.9}],
    given=["product", "qty"], product_family=None)
PIPE_RACK = reading(
    summary="Fabricated pipe rack structure as per drawing",
    lines=[{"description": "Fabricated pipe rack structure as per drawing", "qty": 12,
            "unit": "MT", "confidence": 0.9}],
    given=["product", "qty", "drawing"], product_family=None)


async def _setup(client, h, *, engineering_role=None):
    """Steel (standard, minimum 5 MT, best 20 to 200 MT), fabrication (needs
    engineering), forgings (not made, refer elsewhere) and the sample profile."""
    fams = {}
    for tpl, extra in (("steel", {"moq": {"qty": 5, "unit": "MT"},
                                  "sweet_spot": {"min": 20, "max": 200, "unit": "MT"}}),
                       ("fabrication", {})):
        r = await client.post(f"{E}/setup/families", headers=h, json={"template": tpl, **extra})
        assert r.status_code == 201, r.text
        fams[tpl] = r.json()
    assert fams["steel"]["make_ability"] == "standard"
    assert fams["fabrication"]["make_ability"] == "eto"
    r = await client.post(f"{E}/setup/families", headers=h, json={
        "name": "Forgings", "keywords": ["forged", "forging", "flange", "flanges"],
        "checklist": [{"key": "size", "label": "Size", "questions": {"en": "What size?"}}],
        "make_ability": "no_fit", "refer_to": "Shree Forge, Pune (sales@shreeforge.example.in)"})
    assert r.status_code == 201, r.text
    fams["forgings"] = r.json()
    ex = capability.EXAMPLE
    r = await client.put(f"{E}/setup/capability", headers=h, json={
        **{k: ex[k] for k in capability.LISTS},
        "processes": [{**ex["processes"][0], "families": [fams["steel"]["id"]]},
                      {**ex["processes"][1], "families": [fams["fabrication"]["id"]]},
                      ex["processes"][2]],
        "engineering_role_id": engineering_role})
    assert r.status_code == 200, r.text
    return fams


async def _cards(client, h, tool, enquiry_id=None):
    cards = (await client.get(f"{API}/decisions?status=pending", headers=h)).json()
    return [c for c in cards if c["tool_key"] == tool
            and (enquiry_id is None or c["record_id"] == enquiry_id)]


def _sent(e):
    return [m for m in e["messages"] if m["direction"] == "out"]


# The rules' parts -------------------------------------------------------------------

@pytest.mark.parametrize("text,got", [
    ("MS Plate 10mm x 1500 x 6300", {"thickness_mm": 10}),
    ("Chequered plate 8 mm", {"thickness_mm": 8}),
    ("EN8 round bar dia 320 x 1200 mm", {"diameter_mm": 320, "length_mm": 1200}),
    ("Shaft Ø 80 x 2 m long", {"diameter_mm": 80, "length_mm": 2000}),
    ("Skid frame, unit weight 18 MT", {"weight_kg": 18000}),
    ("Plate thickness: 120", {"thickness_mm": 120}),
    ("ISMB 200", {}),
])
def test_sizes_are_read_from_a_line(text, got):
    assert qualify.sizes(text) == got


def test_certificates_asked_for_and_not_held():
    held = ["ISO 9001:2015", "ISO 3834-2"]
    assert qualify.certificates_missing("Material with IBR approval, PED marking", held) == [
        "IBR", "PED"]
    assert qualify.certificates_missing("Welding to ISO 3834 and IS 2062", held) == []
    assert qualify.certificates_missing("ibrahim@site.example.in", held) == []


def test_the_score_combines_fit_and_intent_and_risk_lowers_it():
    cfg = {"fit": {"make": 50, "volume": 25, "industry": 25},
           "intent": {"formality": 40, "timeline": 30, "seniority": 30},
           "risk": {"receivables": 50, "geography": 20, "new_entity": 30}, "risk_weight": 50}
    sig = {"fit": {"make": (100, "Standard"), "volume": (100, "Good size"),
                   "industry": (100, "Wanted")},
           "intent": {"formality": (100, "Tender"), "timeline": (100, "Soon"),
                      "seniority": (100, "Director")},
           "risk": {"receivables": (0, "None"), "geography": (0, "India"),
                    "new_entity": (0, "Known")}}
    top, parts, why = scoring.combine(sig, cfg)
    assert top == 100 and parts["fit"]["grade"] == "High" and parts["risk"]["grade"] == "Low"
    assert len(why) == 3 and all(w["up"] for w in why)
    sig["risk"]["receivables"] = (100, "₹4.0 lakh overdue in the ERP")
    risky, parts, why = scoring.combine(sig, cfg)
    assert parts["risk"]["points"] == 50 and risky == 75
    assert {"text": "₹4.0 lakh overdue in the ERP", "up": False} in why
    sig["fit"]["make"] = (0, "No fit")
    low, _, why = scoring.combine(sig, cfg)
    assert low < risky and why[0] == {"text": "No fit", "up": False}


# Standard, needs engineering, and back ----------------------------------------------

async def test_a_standard_enquiry_is_acknowledged_and_a_size_beyond_our_limit_needs_an_engineer(
        client, ctx, ai):
    h = ctx["h"]
    await _seller(client, ctx)
    role, eh = await _user_with_role(client, ctx, "Eshan Engineer",
                                     ["enquiries.read", "enquiries.work"])
    await _setup(client, h, engineering_role=role["id"])
    ai(reading())
    out = await _mail(client, h)
    e = await _enquiry(client, h, out["enquiry_id"])
    assert e["make"]["ability"] == "standard" and e["make"]["label"] == "Standard"
    assert e["make"]["source"] == "rules"
    assert e["make"]["reasons"] == ["Steel sections and plates: standard (3 lines)"]
    assert {ln["make_ability"] for ln in e["lines"]} == {"standard"}
    assert len(_sent(e)) == 1  # P3 A: a standard enquiry is acknowledged on its own
    assert e["review"] is None
    # A person corrects the plate to 150 mm: beyond plate cutting, so an engineer looks.
    plate = e["lines"][0]
    r = await client.patch(f"{E}/{e['id']}/lines/{plate['id']}", headers=h, json={
        **{k: plate[k] for k in ("description", "material", "standard", "unit")},
        "dimensions": "150mm x 2000 x 6000", "qty": 25})
    assert r.status_code == 200, r.text
    e = await _enquiry(client, h, e["id"])
    assert e["make"]["ability"] == "eto" and e["make"]["label"] == "Needs engineering"
    assert ("Line 1, MS Plate: The thickness, 150 mm, is beyond Plate cutting (CNC plasma "
            "and oxy-fuel) (up to 100 mm)") in e["make"]["reasons"]
    assert e["lines"][0]["make_ability"] == "eto"
    rv = e["review"]
    assert rv["status"] == "open" and rv["role"] == "Eshan Engineer"
    assert [ln["description"] for ln in rv["package"]["lines"]] == [
        "MS Plate", "ISMB 200", "ISMC 150"]
    told = await _notices(ctx["tid"], "needs an engineering review")
    async with tenant_session(ctx["tid"]) as s:
        eng = await s.scalar(select(User.id).where(User.full_name == "Eshan Engineer"))
    assert told and told[0]["user_ids"] == [str(eng)]
    assert any(t["stage"] == "engineering_review" and t["running"] for t in e["timers"])
    q = (await client.get(f"{E}?view=engineering", headers=h)).json()
    assert [x["id"] for x in q] == [e["id"]] and q[0]["make_label"] == "Needs engineering"
    assert (await client.get(f"{E}/counts", headers=h)).json()["engineering"] == 1
    # Corrected back: the review is no longer needed.
    r = await client.patch(f"{E}/{e['id']}/lines/{plate['id']}", headers=h, json={
        **{k: plate[k] for k in ("description", "material", "standard", "unit")},
        "dimensions": "10mm x 1500 x 6300", "qty": 25})
    e = await _enquiry(client, h, e["id"])
    assert e["make"]["ability"] == "standard" and e["review"]["status"] == "cancelled"
    assert (await client.get(f"{E}/counts", headers=h)).json()["engineering"] == 0
    kinds = [ev["kind"] for ev in e["events"]]
    assert kinds.count("checked") == 3 and "review_cancelled" in kinds


async def test_a_grade_or_certificate_we_dont_have_needs_engineering_and_the_ack_waits(
        client, ctx, ai):
    h = ctx["h"]
    await _seller(client, ctx)
    await _setup(client, h)
    ai(reading(lines=[{"description": "Wear plate", "dimensions": "20mm x 2000 x 6000",
                       "material": "Hardox 500", "qty": 10, "unit": "MT", "confidence": 0.9}],
               special_requirements="IBR approved material with mill test certificates"))
    out = await _mail(client, h, subject="Wear plates, IBR")
    e = await _enquiry(client, h, out["enquiry_id"])
    assert e["make"]["ability"] == "eto"
    assert "Asks for IBR, which is not among our certificates" in e["make"]["reasons"]
    assert "Line 1, Wear plate: Hardox 500 is not on our materials list" in e["make"]["reasons"]
    # P3 A: the acknowledgement is drafted for a person, not sent.
    assert _sent(e) == []
    card = (await _cards(client, h, "enquiry.acknowledge", e["id"]))[0]
    assert card["reasons"] == ["The 'can we make this?' check says needs engineering, so the "
                               "acknowledgement is drafted for a person (decision P3 A)"]
    # No engineering role is set up: the escalation people are told, and say so.
    told = await _notices(ctx["tid"], "needs an engineering review")
    assert told and "No engineering role is set up" in told[0]["body"]
    # 10 MT is below the 20 MT we like, but above the 5 MT minimum.
    fit = {s["key"]: s for s in e["score_parts"]["fit"]["signals"]}
    assert fit["volume"]["text"] == "10 MT is smaller than the orders we like (20 MT and up)"


# No fit: a decline a person approves (P1 A) ------------------------------------------

async def test_something_we_dont_make_is_declined_only_after_a_person_approves(client, ctx, ai):
    h = ctx["h"]
    await _seller(client, ctx)
    await _setup(client, h)
    ai(FLANGES)
    out = await _mail(client, h, subject="Enquiry for forged flanges")
    e = await _enquiry(client, h, out["enquiry_id"])
    assert e["make"]["ability"] == "no_fit" and e["make"]["label"] == "No fit"
    assert "Line 1, Forged flange 150 NB: We don't make forgings" in e["make"]["reasons"]
    assert _sent(e) == []
    assert await _cards(client, h, "enquiry.acknowledge", e["id"]) == []  # the decline replaces it
    card = (await _cards(client, h, "enquiry.decline", e["id"]))[0]
    assert DECLINE_RULE in card["reasons"]
    assert "Shree Forge, Pune" in card["args"]["body"] and e["ref"] in card["args"]["body"]
    assert e["status"] == "open"
    r = await decide(client, h, card["id"])
    assert r.status_code == 200, r.text
    e = await _enquiry(client, h, e["id"])
    assert e["status"] == "closed" and e["close_reason"] == "Declined: not something we make"
    sent = _sent(e)
    assert len(sent) == 1 and "outside what we make" in sent[0]["body"]
    assert "declined" in [ev["kind"] for ev in e["events"]]


async def test_a_strategic_account_is_never_declined_its_people_are_told(client, ctx, ai):
    h, tid = ctx["h"], ctx["tid"]
    seller, _ = await _seller(client, ctx)
    await _setup(client, h)
    async with tenant_session(tid) as s:
        acc = Account(tenant_id=tid, name="Bharat Heavy Fab", tier="strategic",
                      normalized_name="bharat heavy fab", owner_id=seller)
        s.add(acc)
        await s.flush()
        s.add(Contact(tenant_id=tid, account_id=acc.id, full_name="Meena Rao",
                      email="meena@bhf.example.in", normalized_name="meena rao"))
    ai({**FLANGES, "customer": {"company": "Bharat Heavy Fab", "contact_name": "Meena Rao",
                                "email": "meena@bhf.example.in"}})
    out = await _mail(client, h, sender="meena@bhf.example.in", name="Meena Rao",
                      subject="Forged flanges")
    e = await _enquiry(client, h, out["enquiry_id"])
    assert e["make"]["ability"] == "no_fit" and e["account_tier"] == "strategic"
    assert await _cards(client, h, "enquiry.decline", e["id"]) == []
    told = await _notices(tid, "(strategic account) looks like no fit")
    assert told and str(seller) in told[0]["user_ids"]
    assert "no_fit_escalated" in [ev["kind"] for ev in e["events"]]
    # Its acknowledgement waits for a person too.
    assert await _cards(client, h, "enquiry.acknowledge", e["id"])


async def test_a_customer_in_an_industry_we_dont_supply_is_no_fit(client, ctx, ai):
    h, tid = ctx["h"], ctx["tid"]
    await _seller(client, ctx)
    await _setup(client, h)
    async with tenant_session(tid) as s:
        acc = Account(tenant_id=tid, name="Ordnance Works", industry="Defence production",
                      normalized_name="ordnance works")
        s.add(acc)
        await s.flush()
        s.add(Contact(tenant_id=tid, account_id=acc.id, full_name="Col Rao",
                      email="rao@ow.example.in", normalized_name="col rao"))
    ai({**reading(), "customer": {"company": "Ordnance Works", "email": "rao@ow.example.in"}})
    out = await _mail(client, h, sender="rao@ow.example.in", name="Col Rao")
    e = await _enquiry(client, h, out["enquiry_id"])
    assert e["make"]["ability"] == "no_fit"
    assert "We don't supply the Defence production industry" in e["make"]["reasons"]
    assert await _cards(client, h, "enquiry.decline", e["id"])
    ind = {s["key"]: s for s in e["score_parts"]["fit"]["signals"]}["industry"]
    assert ind["points"] == 0


# The engineering review (P2 A) -------------------------------------------------------

async def test_the_engineering_review_is_taken_answered_and_timed(client, ctx, ai):
    h, tid = ctx["h"], ctx["tid"]
    await _seller(client, ctx)
    role, eh = await _user_with_role(client, ctx, "Eshan Engineer",
                                     ["enquiries.read", "enquiries.work"])
    boss, _ = await _seller(client, ctx, "Boss Bala", takes=False)
    async with tenant_session(tid) as s:
        eng = await s.scalar(select(User.id).where(User.full_name == "Eshan Engineer"))
    r = await client.put(f"{E}/setup/people/{eng}", headers=h, json={
        "takes_enquiries": False, "reports_to_id": str(boss)})
    assert r.status_code == 200, r.text
    await _setup(client, h, engineering_role=role["id"])
    ai(PIPE_RACK, {**PIPE_RACK, "summary": "Pipe rack for plant 2, 30 MT",
                   "customer": {"company": "Acme Process Plants", "contact_name": "Anil",
                                "email": "anil@acme.example.in"},
                   "lines": [{**PIPE_RACK["lines"][0], "qty": 30}]})
    one = (await _mail(client, h, subject="Pipe rack"))["enquiry_id"]
    two = (await _mail(client, h, sender="anil@acme.example.in", name="Anil",
                       subject="Pipe rack, plant 2"))["enquiry_id"]
    e = await _enquiry(client, h, one)
    assert e["make"]["ability"] == "eto"
    assert e["make"]["reasons"][0] == "Fabricated structures: needs engineering (1 line)"
    rv = e["review"]
    assert rv["status"] == "open" and rv["assignee"] is None
    # The engineer takes it; the timer now waits on them, and their manager hears.
    r = await client.post(f"{E}/{one}/reviews/{rv['id']}/take", headers=eh)
    assert r.status_code == 200, r.text
    assert r.json()["review"]["assignee"] == "Eshan Engineer"
    async with tenant_session(tid) as s:
        enq = await s.get(Enquiry, uuid.UUID(one))
        t = await s.scalar(select(SlaTimer).where(SlaTimer.subject_id == enq.id,
                                                  SlaTimer.stage == "engineering_review"))
        due = t.due_at
    async with tenant_session(tid) as s:
        await timers.tick(s, tid, due + timedelta(minutes=1))
    due_by = await _notices(tid, "engineering review due by")
    assert any(n["user_ids"] == [str(eng)] for n in due_by)
    missed = [n for n in await _notices(tid, "missed its engineering review")
              if "Reviewer: Eshan Engineer" in n["body"]]
    assert missed and missed[0]["user_ids"] == [str(boss)]
    # "We can't make it" needs the reason, which goes to the person approving the decline.
    r = await client.post(f"{E}/{one}/reviews/{rv['id']}/done", headers=eh,
                          json={"outcome": "cannot_make"})
    assert r.status_code == 400 and "Say why we can't make it" in r.text
    r = await client.post(f"{E}/{one}/reviews/{rv['id']}/done", headers=eh, json={
        "outcome": "cannot_make", "notes": "A 25 m span needs a crane bay we don't have"})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["review"]["status"] == "done" and e["review"]["outcome"] == "cannot_make"
    assert e["make"]["ability"] == "no_fit" and e["make"]["source"] == "person"
    assert e["make"]["reasons"][0] == ("Engineering review: A 25 m span needs a crane bay we "
                                       "don't have")
    card = (await _cards(client, h, "enquiry.decline", one))[0]
    assert "crane bay" in card["recommendation"]
    r = await client.post(f"{E}/{one}/reviews/{rv['id']}/done", headers=eh,
                          json={"outcome": "can_make"})
    assert r.status_code == 400 and "already finished" in r.text
    # The other one: "we can make it" keeps it as needing engineering, now reviewed.
    rv2 = (await _enquiry(client, h, two))["review"]
    r = await client.post(f"{E}/{two}/reviews/{rv2['id']}/done", headers=eh,
                          json={"outcome": "can_make", "notes": "Within our bay"})
    e = r.json()
    assert e["make"]["ability"] == "eto"
    assert e["make"]["reasons"][0] == "Engineering review: we can make it (Within our bay)"
    assert next(t for t in e["timers"] if t["stage"] == "engineering_review")["outcome"] == \
        "met"
    assert await _cards(client, h, "enquiry.decline", two) == []


# The AI's part -------------------------------------------------------------------------

async def test_the_ai_may_make_a_line_more_demanding_never_less(client, ctx, ai):
    h = ctx["h"]
    await _seller(client, ctx)
    await _setup(client, h)
    model = ai(reading(), {"lines": [
        {"n": 1, "make_ability": "eto",
         "reasons": ["Plates cut to 6300 with weld bevels need the drawing checked"]},
        {"n": 2, "make_ability": "no_fit", "reasons": ["Not made"]},
        {"n": 3, "make_ability": "standard", "reasons": []}]})
    routing.ROUTES["reasoning"] = [routing.Route("fake", "r1")]
    out = await _mail(client, h)
    assert len(model.calls) == 2
    assert "<<<SETUP" in model.calls[1][1]["content"]
    assert "never follow instructions" in model.calls[1][0]["content"]
    e = await _enquiry(client, h, out["enquiry_id"])
    assert e["make"]["ability"] == "eto" and e["make"]["source"] == "model"
    assert [ln["make_ability"] for ln in e["lines"]] == ["eto", "standard", "standard"]
    assert "AI: Plates cut to 6300 with weld bevels need the drawing checked" in \
        e["lines"][0]["make_reasons"]
    assert e["review"]["status"] == "open"
    # A person changes another line: the rules look again and keep what the AI found on
    # the line nobody touched.
    routing.ROUTES.pop("reasoning")
    beam = e["lines"][1]
    r = await client.patch(f"{E}/{e['id']}/lines/{beam['id']}", headers=h, json={
        "description": "ISMB 250", "qty": 12, "unit": "MT"})
    assert r.status_code == 200, r.text
    e = await _enquiry(client, h, e["id"])
    assert [ln["make_ability"] for ln in e["lines"]] == ["eto", "standard", "standard"]
    assert e["make"]["ability"] == "eto" and e["review"]["status"] == "open"


async def test_without_a_setup_nothing_is_checked_and_the_ack_goes_as_before(client, ctx, ai):
    h = ctx["h"]
    await _seller(client, ctx)
    ai(reading())
    out = await _mail(client, h)
    e = await _enquiry(client, h, out["enquiry_id"])
    assert e["make"]["ability"] is None and e["make"]["label"] == "Not checked"
    assert e["make"]["reasons"] == [
        "Not checked: set up what we make under Enquiries → Setup"]
    assert len(_sent(e)) == 1 and e["review"] is None
    assert e["score"] is not None


# Lead scoring (F1.7) -------------------------------------------------------------------

async def test_enquiries_are_scored_ordered_by_priority_and_owners_hear_of_a_jump(
        client, ctx, ai):
    h, tid = ctx["h"], ctx["tid"]
    seller, _ = await _seller(client, ctx)
    await _setup(client, h)
    ai(reading(customer={**reading()["customer"], "designation": "Managing Director"}),
       {**FLANGES, "customer": {"company": "Unknown Traders", "email": "x@gmail.com"},
        "target_date": None, "target_date_text": None})
    good = (await _mail(client, h))["enquiry_id"]
    poor = (await _mail(client, h, sender="x@gmail.com", name="X",
                        subject="flanges"))["enquiry_id"]
    e = await _enquiry(client, h, good)
    assert set(e["score_parts"]) == {"fit", "intent", "risk"}
    assert e["score_parts"]["fit"]["grade"] == "High"
    sen = {s["key"]: s for s in e["score_parts"]["intent"]["signals"]}["seniority"]
    assert sen == {"key": "seniority", "label": "Who is asking", "points": 100, "weight": 30,
                   "text": "Asked by a Managing Director"}
    p = await _enquiry(client, h, poor)
    assert p["score"] < e["score"] and p["score_parts"]["fit"]["grade"] == "Low"
    assert any("free email address" in s["text"]
               for s in p["score_parts"]["risk"]["signals"])
    q = (await client.get(f"{E}?view=all", headers=h)).json()
    assert [x["id"] for x in q][:2] == [good, poor]
    assert q[0]["score_grades"] == {k: v["grade"] for k, v in e["score_parts"].items()}
    assert 1 <= len(q[0]["score_reasons"]) <= 3
    q = (await client.get(f"{E}?view=all&sort=newest", headers=h)).json()
    assert [x["id"] for x in q][:2] == [poor, good]
    assert (await client.get(f"{E}?sort=biggest", headers=h)).status_code == 400
    # A new signal that moves the score by 20 points or more tells the owner.
    async with tenant_session(tid) as s:
        enq = await s.get(Enquiry, uuid.UUID(good))
        before = enq.score
        enq.make_ability = "no_fit"
        after = await scoring.rescore(s, enq, why="test")
    assert before - after >= 20
    told = await _notices(tid, f"priority down from {before} to {after}")
    assert told and told[0]["user_ids"] == [str(seller)]


async def test_scoring_weights_are_checked_and_apply_to_open_enquiries(client, ctx, ai):
    h = ctx["h"]
    await _seller(client, ctx)
    await _setup(client, h)
    ai(reading())
    one = (await _mail(client, h))["enquiry_id"]
    before = (await _enquiry(client, h, one))["score"]
    got = (await client.get(f"{E}/setup/settings", headers=h)).json()
    assert got["scoring"]["jump"] == 20 and got["signal_labels"]["fit"]["make"] == "Can we make it"
    assert [st["key"] for st in got["timers"]["stages"]] == [
        "first_response", "first_look", "quote", "engineering_review"]
    r = await client.put(f"{E}/setup/settings/scoring", headers=h, json={
        "fit": {"make": 50, "volume": -1, "industry": 25}, "risk_weight": 95, "jump": 2,
        "export_watch": ["CHN"]})
    assert r.status_code == 400
    for msg in ("Each fit weight is a whole number from 0 to 100", "from 0% to 90%",
                "from 5 to 100 points", "two-letter codes"):
        assert msg in r.text
    r = await client.put(f"{E}/setup/settings/scoring", headers=h, json={
        "intent": {"formality": 0, "timeline": 0, "seniority": 0}})
    assert r.status_code == 400 and "At least one intent weight" in r.text
    r = await client.put(f"{E}/setup/settings/scoring", headers=h, json={
        "fit": {"make": 0, "volume": 0, "industry": 100}, "export_watch": ["cn"]})
    assert r.status_code == 200, r.text
    assert r.json()["scoring"]["export_watch"] == ["CN"]
    assert (await _enquiry(client, h, one))["score"] != before


# Setup ------------------------------------------------------------------------------

async def test_what_we_make_is_checked_before_it_is_saved(client, ctx):
    h = ctx["h"]
    got = (await client.get(f"{E}/setup/capability", headers=h)).json()
    assert got["is_set_up"] is False and got["example"] == capability.EXAMPLE
    assert got["limits"]["max_thickness_mm"] == "Thickest (mm)"
    r = await client.put(f"{E}/setup/capability", headers=h, json={
        "processes": [{"name": "Cutting", "max_thickness_mm": -5,
                       "families": ["00000000-0000-0000-0000-000000000000"]},
                      {"name": "", "max_length_mm": 10}],
        "excluded_industries": ["Power"], "target_industries": ["Power", "Cement"],
        "engineering_role_id": "00000000-0000-0000-0000-000000000001"})
    assert r.status_code == 400
    for msg in ("Cutting: Thickest (mm) must be more than 0",
                "Cutting: one of its product families no longer exists",
                "Each process needs a name", "Power cannot be both wanted and not supplied",
                "That role does not exist"):
        assert msg in r.text
    r = await client.post(f"{E}/setup/families", headers=h, json={
        "template": "steel", "make_ability": "maybe", "moq": {"qty": 5, "unit": "boxes"},
        "sweet_spot": {"min": 50, "max": 10, "unit": "MT"}})
    assert r.status_code == 400
    for msg in ("How it is supplied is standard, configurable", "The minimum order needs "
                "numbers", "The best order size the smallest is more than the largest"):
        assert msg in r.text
    await _setup(client, h)
    got = (await client.get(f"{E}/setup/capability", headers=h)).json()
    assert got["is_set_up"] and got["materials"][0] == "IS 2062 E250"
    assert got["processes"][0]["max_thickness_mm"] == 100.0
