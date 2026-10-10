"""The channel fabric (F12.5, Step 6): one sending path, consent, limits, quiet hours,
the simulator and SMTP (decisions L1, L2, L8, L9, L11)."""
# ruff: noqa: F811  (the ctx fixture is imported from test_agents)

import re
import smtplib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.audit import chain
from app.channels import accounts, send
from app.channels import consent as consent_mod
from app.channels import statuses as st
from app.channels.adapters import smtp as smtp_mod
from app.channels.adapters.base import SENT, OutgoingEmail, SendResult
from app.channels.adapters.simulator import SimulatorAdapter
from app.db import tenant_session
from app.models import AuditRecord, Consent, Contact, EmailAccount, Message, MessageEvent
from tests.test_agents import (  # noqa: F401
    API,
    _rule,
    _user_with_role,
    checker,
    ctx,
    drain,
    steward_handlers,
)

IST = ZoneInfo("Asia/Kolkata")
OPEN_HOURS = {"promo_per_day": 1, "promo_per_week": 3, "quiet_start": "00:00",
              "quiet_end": "00:00", "promo_rest_days": [], "promo_skip_holidays": True,
              "reply_window_hours": 24, "review_first": 10, "review_every": 20,
              "staff_email": True}


def ist(y, mo, d, h, mi=0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=IST)


async def _setup(client, ctx, *, settings=None, email="anita@kpl.example.in", name="Anita"):
    h = ctx["h"]
    if settings is not False:
        r = await client.put(f"{API}/channels/settings", headers=h,
                             json={**OPEN_HOURS, **(settings or {})})
        assert r.status_code == 200, r.text
    acc = (await client.post(f"{API}/accounts", headers=h,
                             json={"name": f"KPL Pumps {uuid.uuid4().hex[:4]}"})).json()
    c = (await client.post(f"{API}/contacts", headers=h, json={
        "account_id": acc["id"], "full_name": f"{name} Desai", "email": email})).json()
    return acc["id"], c["id"]


async def _compose(client, h, contact_id, *, purpose="transactional", send_now=True, **kw):
    r = await client.post(f"{API}/messages", headers=h, json={
        "contact_id": contact_id, "purpose": purpose, "subject": kw.pop("subject", "Your order"),
        "body": kw.pop("body", "Your order 4471 leaves our Pune plant today."),
        "send": send_now, **kw})
    assert r.status_code == 201, r.text
    return r.json()


async def _events(tid, mid):
    async with tenant_session(tid) as s:
        return [(e.status, e.source, e.applied) for e in await s.scalars(
            select(MessageEvent).where(MessageEvent.message_id == uuid.UUID(mid))
            .order_by(MessageEvent.id))]


@pytest.fixture
def mails(monkeypatch):
    """Everything the simulator 'sends', so a test can read the email itself."""
    seen: list[OutgoingEmail] = []
    original = SimulatorAdapter.send

    async def capture(self, mail):
        seen.append(mail)
        return await original(self, mail)

    monkeypatch.setattr(SimulatorAdapter, "send", capture)
    return seen


# The path --------------------------------------------------------------------------

async def test_a_message_is_checked_queued_and_lands_in_the_test_inbox(client, ctx, mails):
    h, tid = ctx["h"], ctx["tid"]
    acc, cid = await _setup(client, ctx)
    m = await _compose(client, h, cid)
    assert m["status"] == "queued" and m["to_address"] == "anita@kpl.example.in"
    assert [c["check"] for c in m["checks"]] == ["recipient", "address", "controls", "content",
                                                 "consent", "timing"]
    assert all(c["ok"] for c in m["checks"])
    assert m["account_id"] == acc and m["language"] == "en"

    assert await send.deliver_due(tid) == {"sent": 1}
    got = (await client.get(f"{API}/messages/{m['id']}", headers=h)).json()
    assert got["status"] == "sent" and got["provider"] == "simulator" and got["sent_at"]
    assert [e["status"] for e in got["events"]] == ["draft", "queued", "sending", "sent"]
    assert mails[0].to == "anita@kpl.example.in" and "List-Unsubscribe" not in mails[0].headers
    inbox = (await client.get(f"{API}/messages?test_inbox=true", headers=h)).json()
    assert [x["id"] for x in inbox] == [m["id"]]
    kinds = [r["kind"] for r in (await client.get(f"{API}/audit?kind=message.",
                                                  headers=h)).json()]
    assert kinds == ["message.sent", "message.queued"]
    # Nothing left to do: a second run sends nothing again.
    assert await send.deliver_due(tid) == {}


async def test_recipient_and_address_checks_block_with_a_reason(client, ctx):
    h = ctx["h"]
    acc, cid = await _setup(client, ctx)
    nomail = (await client.post(f"{API}/contacts", headers=h, json={
        "account_id": acc, "full_name": "Ravi Kumar"})).json()
    m = await _compose(client, h, nomail["id"])
    assert m["status"] == "blocked" and m["reason"] == "No valid email address for this contact"
    other = (await client.post(f"{API}/accounts", headers=h, json={"name": "Other Ltd"})).json()
    async with tenant_session(ctx["tid"]) as s:
        msg = await send.draft(s, ctx["tid"], purpose="transactional", subject="x", body="y",
                               created_by="test", contact_id=uuid.UUID(cid),
                               account_id=uuid.UUID(other["id"]))
        await send.queue(s, msg, actor="test")
        assert msg.status == "blocked" and msg.reason.startswith("This contact is not at Other")
        hole = await send.draft(s, ctx["tid"], purpose="transactional", subject="Hi",
                                body="Dear [Name], your order", created_by="test",
                                contact_id=uuid.UUID(cid))
        await send.queue(s, hole, actor="test")
        assert hole.status == "blocked" and "[Name]" in hole.reason
    sms = await client.post(f"{API}/messages", headers=h, json={
        "contact_id": cid, "subject": "", "body": "hello", "send": True})
    assert sms.json()["status"] == "blocked" and sms.json()["reason"] == "An email needs a subject"


# Consent and STOP (L9) ------------------------------------------------------------------

async def test_stop_stops_promotional_everywhere_is_confirmed_and_transactional_still_goes(
        client, ctx):
    h, tid = ctx["h"], ctx["tid"]
    _, cid = await _setup(client, ctx)
    promo = await _compose(client, h, cid, purpose="promotional", subject="New pump range",
                           body="Our new KX pumps are here.")
    assert promo["status"] == "queued"
    await send.deliver_due(tid)
    r = await client.post(f"{API}/channels/test-inbox/{promo['id']}/reply", headers=h,
                          json={"body": "STOP"})
    assert r.status_code == 201, r.text
    inbound = r.json()
    assert inbound["direction"] == "in" and inbound["contact_id"] == cid
    consent = (await client.get(f"{API}/contacts/{cid}/consent", headers=h)).json()
    assert {c["channel"]: c["status"] for c in consent["channels"]} == {
        "email": "withdrawn", "whatsapp": "withdrawn", "sms": "withdrawn"}
    assert consent["history"][0]["how"] == "stop_word"
    # The confirmation goes at once, as a reply, even though it is one more email.
    await send.deliver_due(tid)
    msgs = (await client.get(f"{API}/messages?contact_id={cid}", headers=h)).json()
    confirm = next(x for x in msgs if x["feature"] == "consent.confirm")
    assert confirm["status"] == "sent" and confirm["subject"] == "You are unsubscribed"
    assert confirm["in_reply_to_id"] == inbound["id"]
    # A second STOP is recorded but not confirmed twice.
    await client.post(f"{API}/channels/test-inbox/{promo['id']}/reply", headers=h,
                      json={"body": "stop."})
    msgs = (await client.get(f"{API}/messages?contact_id={cid}", headers=h)).json()
    assert len([x for x in msgs if x["feature"] == "consent.confirm"]) == 1

    again = await _compose(client, h, cid, purpose="promotional", subject="Offer",
                           body="Ten percent off this week.")
    assert again["status"] == "blocked" and again["reason"].startswith(
        "Asked not to get promotional messages (Replied STOP")
    order = await _compose(client, h, cid)
    assert order["status"] == "queued"
    kinds = {r["kind"] for r in (await client.get(f"{API}/audit?kind=consent.",
                                                  headers=h)).json()}
    assert kinds == {"consent.withdrawn"}


async def test_a_person_records_consent_and_promotional_follows_the_latest(client, ctx):
    h = ctx["h"]
    _, cid = await _setup(client, ctx)
    r = await client.post(f"{API}/contacts/{cid}/consent", headers=h, json={
        "channel": "email", "status": "withdrawn", "how": "phone",
        "note": "Asked on the phone not to get offers"})
    assert r.status_code == 201, r.text
    m = await _compose(client, h, cid, purpose="promotional", subject="Offer", body="Offer.")
    assert m["status"] == "blocked" and "Told us on the phone" in m["reason"]
    await client.post(f"{API}/contacts/{cid}/consent", headers=h, json={
        "channel": "email", "status": "granted", "how": "form"})
    m = await _compose(client, h, cid, purpose="promotional", subject="Offer", body="Offer.")
    assert m["status"] == "queued"
    # Only people who may edit contacts record consent.
    _, viewer = await _user_with_role(client, ctx, "Viewer", ["accounts.read"])
    r = await client.post(f"{API}/contacts/{cid}/consent", headers=viewer, json={
        "channel": "all", "status": "withdrawn", "how": "person"})
    assert r.status_code == 403


async def test_unsubscribe_link_needs_the_button_and_works_once(client, ctx, mails):
    h, tid = ctx["h"], ctx["tid"]
    _, cid = await _setup(client, ctx)
    promo = await _compose(client, h, cid, purpose="promotional", subject="New pumps",
                           body="Our new KX pumps are here.")
    await send.deliver_due(tid)
    mail = mails[-1]
    link = re.search(r"https?://\S+/api/v1/public/unsubscribe/(\S+)", mail.text)
    assert link and mail.headers["List-Unsubscribe"] == f"<{link.group(0)}>"
    assert mail.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    token = link.group(1)
    page = await client.get(f"{API}/public/unsubscribe/{token}")
    assert page.status_code == 200 and "<button" in page.text
    st_ = (await client.get(f"{API}/contacts/{cid}/consent", headers=h)).json()
    assert st_["channels"][0]["status"] == "unknown"  # opening the page changed nothing
    done = await client.post(f"{API}/public/unsubscribe/{token}")
    assert done.status_code == 200 and "unsubscribed" in done.text
    st_ = (await client.get(f"{API}/contacts/{cid}/consent", headers=h)).json()
    assert st_["channels"][0]["status"] == "withdrawn" and st_["history"][0]["how"] == "link"
    assert (await client.post(f"{API}/public/unsubscribe/{token}")).status_code == 200
    forged = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")
    assert (await client.get(f"{API}/public/unsubscribe/{forged}")).status_code == 404
    assert (await client.get(f"{API}/public/unsubscribe/nonsense")).status_code == 404
    assert promo["purpose"] == "promotional"


async def test_consent_follows_a_contact_through_a_merge(client, ctx):
    from app.steward.merge import merge_records

    h, tid = ctx["h"], ctx["tid"]
    acc, cid = await _setup(client, ctx)
    other = (await client.post(f"{API}/contacts", headers=h, json={
        "account_id": acc, "full_name": "Anita R Desai", "email": "anita.d@kpl.example.in"})
    ).json()
    await client.post(f"{API}/contacts/{other['id']}/consent", headers=h, json={
        "channel": "all", "status": "withdrawn", "how": "in_person"})
    async with tenant_session(tid) as s:
        await merge_records(s, "contact", uuid.UUID(cid), uuid.UUID(other["id"]),
                            actor="test", automatic=False)
    m = await _compose(client, h, cid, purpose="promotional", subject="Offer", body="Offer.")
    assert m["status"] == "blocked" and "Told us in person" in m["reason"]


# Timing (L8) ------------------------------------------------------------------------------

async def _queue_at(tid, cid, at, purpose="transactional", **kw):
    async with tenant_session(tid) as s:
        msg = await send.draft(s, tid, purpose=purpose, subject="Hello", body="Hello there",
                               created_by="test", contact_id=uuid.UUID(cid), **kw)
        await send.queue(s, msg, actor="test", now=at.astimezone(UTC))
        return msg


async def test_quiet_hours_hold_until_morning_except_replies_just_after_the_customer_wrote(
        client, ctx):
    h, tid = ctx["h"], ctx["tid"]
    _, cid = await _setup(client, ctx, settings={"quiet_start": "21:00", "quiet_end": "09:00"})
    late = await _queue_at(tid, cid, ist(2026, 10, 14, 22, 15))  # a Wednesday
    assert late.status == "held"
    assert late.send_after == ist(2026, 10, 15, 9, 0).astimezone(UTC)
    assert late.reason == "Quiet hours: goes at 09:00 on Thu 15 Oct"
    early = await _queue_at(tid, cid, ist(2026, 10, 15, 6, 0))
    assert early.send_after == ist(2026, 10, 15, 9, 0).astimezone(UTC)

    async with tenant_session(tid) as s:
        inbound = await send.receive(s, tid, from_address="anita@kpl.example.in",
                                     subject="Quote?", body="Can you send the quote?",
                                     provider="simulator", provider_ref=f"<{uuid.uuid4()}>",
                                     source="simulator", at=ist(2026, 10, 14, 21, 50))
        old = await send.receive(s, tid, from_address="anita@kpl.example.in", subject="Hi",
                                 body="Thanks", provider="simulator",
                                 provider_ref=f"<{uuid.uuid4()}>", source="simulator",
                                 at=ist(2026, 10, 12, 20, 0))
    fresh = await _queue_at(tid, cid, ist(2026, 10, 14, 22, 15), purpose="reply",
                            in_reply_to_id=inbound.id)
    assert fresh.status == "queued"
    assert "quiet hours do not apply" in fresh.checks[-1]["detail"]
    stale = await _queue_at(tid, cid, ist(2026, 10, 14, 22, 15), purpose="reply",
                            in_reply_to_id=old.id)
    assert stale.status == "held"

    # The worker sends the reply at once, and the rest once the morning comes.
    assert await send.deliver_due(tid, ist(2026, 10, 14, 22, 16).astimezone(UTC)) == {
        "sent": 1}
    assert await send.deliver_due(tid, ist(2026, 10, 15, 8, 59).astimezone(UTC)) == {}
    assert await send.deliver_due(tid, ist(2026, 10, 15, 9, 1).astimezone(UTC)) == {"sent": 3}
    assert h


async def test_promotional_waits_for_sundays_holidays_and_the_limits(client, ctx):
    h, tid = ctx["h"], ctx["tid"]
    _, cid = await _setup(client, ctx, settings={"quiet_start": "21:00", "quiet_end": "09:00",
                                                 "promo_rest_days": [6]})
    r = await client.put(f"{API}/decisions/settings", headers=h, json={
        "time_zone": "Asia/Kolkata", "work_days": [0, 1, 2, 3, 4, 5], "day_start": "09:30",
        "day_end": "18:30", "holidays": [{"date": "2026-10-20", "name": "Diwali"}],
        "default_sla_hours": 4, "reminder_pct": 75, "escalation_role_id": None,
        "bottleneck_overdue": 5, "bottleneck_wait_ratio": 2})
    assert r.status_code == 200, r.text
    sunday = await _queue_at(tid, cid, ist(2026, 10, 18, 11, 0), purpose="promotional")
    assert sunday.status == "held" and sunday.send_after == ist(2026, 10, 19, 9, 0)
    assert sunday.reason.startswith("Promotional messages wait on Sundays and holidays")
    holiday = await _queue_at(tid, cid, ist(2026, 10, 20, 11, 0), purpose="promotional")
    assert holiday.send_after == ist(2026, 10, 21, 9, 0)
    order = await _queue_at(tid, cid, ist(2026, 10, 18, 11, 0))
    assert order.status == "queued"  # transactional messages go on Sundays

    # Limits: one promotional a day, three in any seven days, across channels.
    async def sent_on(day):
        async with tenant_session(tid) as s:
            s.add(Message(tenant_id=tid, contact_id=uuid.UUID(cid), channel="email",
                          direction="out", status="sent", purpose="promotional", subject="x",
                          body="y", created_by="test", sent_at=ist(2026, 10, day, 10, 0),
                          status_at=ist(2026, 10, day, 10, 0), attempts=1, checks=[]))

    await sent_on(15)
    same_day = await _queue_at(tid, cid, ist(2026, 10, 15, 15, 0), purpose="promotional")
    assert same_day.status == "held" and same_day.reason.startswith("Already had today's")
    assert same_day.send_after == ist(2026, 10, 16, 9, 0)
    await sent_on(13)
    await sent_on(14)
    week = await _queue_at(tid, cid, ist(2026, 10, 16, 10, 0), purpose="promotional")
    assert week.status == "held" and week.reason.startswith("Already had this week's")
    # The 13th's falls out of the seven days at 10:00 on the 20th, a holiday: the 21st.
    assert week.send_after == ist(2026, 10, 21, 9, 0)
    transactional = await _queue_at(tid, cid, ist(2026, 10, 15, 15, 0))
    assert transactional.status == "queued"  # counted, never limited


async def test_send_anyway_is_recorded_and_never_passes_a_block(client, ctx):
    h, tid = ctx["h"], ctx["tid"]
    _, cid = await _setup(client, ctx, settings={"quiet_start": "21:00", "quiet_end": "09:00"})
    held = await _queue_at(tid, cid, ist(2026, 10, 14, 23, 0))
    r = await client.post(f"{API}/messages/{held.id}/send-anyway", headers=h,
                          json={"reason": "Customer asked for it tonight"})
    assert r.status_code == 200 and r.json()["status"] == "queued", r.text
    assert r.json()["override_by"].startswith("user:")
    assert await send.deliver_due(tid, ist(2026, 10, 14, 23, 5).astimezone(UTC)) == {"sent": 1}
    log = (await client.get(f"{API}/audit?kind=message.sent_anyway", headers=h)).json()
    assert len(log) == 1 and log[0]["actor"].startswith("user:")
    blocked = await _compose(client, h, cid, subject="", body="x")
    r = await client.post(f"{API}/messages/{blocked['id']}/send-anyway", headers=h,
                          json={"reason": "please"})
    assert r.status_code == 400


# At most once ------------------------------------------------------------------------------

async def test_a_lost_connection_is_never_retried_on_its_own(client, ctx):
    h, tid = ctx["h"], ctx["tid"]
    _, cid = await _setup(client, ctx, email="lost@simulator.test")
    m = await _compose(client, h, cid)
    assert await send.deliver_due(tid) == {"failed": 1}
    got = (await client.get(f"{API}/messages/{m['id']}", headers=h)).json()
    assert got["status"] == "failed" and got["reason"] == send.LOST and got["attempts"] == 1
    assert await send.deliver_due(tid) == {}  # not again
    again = await client.post(f"{API}/messages/{m['id']}/resend", headers=h)
    assert again.status_code == 200 and again.json()["status"] == "queued"
    log = (await client.get(f"{API}/audit?kind=message.resent", headers=h)).json()
    assert len(log) == 1


async def test_an_unreachable_server_is_tried_again_with_waits_then_given_up(client, ctx):
    h, tid = ctx["h"], ctx["tid"]
    _, cid = await _setup(client, ctx, email="outage@simulator.test")
    m = await _compose(client, h, cid)
    now = datetime.now(UTC)
    for n in range(1, send.MAX_TRIES):
        assert await send.deliver_due(tid, now) == {"retry": 1}
        async with tenant_session(tid) as s:
            msg = await s.get(Message, uuid.UUID(m["id"]))
            assert msg.status == "queued" and msg.attempts == n
            assert msg.reason.startswith("The mail server could not be reached")
            now = msg.send_after + timedelta(seconds=1)
    assert await send.deliver_due(tid, now) == {"failed": 1}
    got = (await client.get(f"{API}/messages/{m['id']}", headers=h)).json()
    assert got["status"] == "failed" and got["reason"].endswith("Tried 5 times.")


async def test_an_adapter_crash_or_a_dead_worker_counts_as_lost(client, ctx, monkeypatch):
    h, tid = ctx["h"], ctx["tid"]
    _, cid = await _setup(client, ctx)

    async def boom(self, mail):
        raise RuntimeError("bug")

    monkeypatch.setattr(SimulatorAdapter, "send", boom)
    m = await _compose(client, h, cid)
    assert await send.deliver_due(tid) == {"failed": 1}
    got = (await client.get(f"{API}/messages/{m['id']}", headers=h)).json()
    assert got["reason"] == send.LOST
    # A message left in "sending" by a worker that died.
    async with tenant_session(tid) as s:
        stuck = await send.draft(s, tid, purpose="transactional", subject="x", body="y",
                                 created_by="test", contact_id=uuid.UUID(cid))
        stuck.status, stuck.status_at = "sending", datetime.now(UTC) - timedelta(minutes=30)
    await send.deliver_due(tid)
    async with tenant_session(tid) as s:
        assert (await s.get(Message, stuck.id)).reason == send.LOST


async def test_receipts_never_move_a_message_backwards(client, ctx):
    tid = ctx["tid"]
    _, cid = await _setup(client, ctx)
    m = await _compose(client, ctx["h"], cid)
    await send.deliver_due(tid)
    async with tenant_session(tid) as s:
        msg = await s.get(Message, uuid.UUID(m["id"]))
        assert await send.record_receipt(s, msg, "read", source="provider")
        assert not await send.record_receipt(s, msg, "delivered", source="provider")
        assert not await send.record_receipt(s, msg, "bounced", source="provider")
        assert msg.status == "read"
    assert (await _events(tid, m["id"]))[-2:] == [("delivered", "provider", False),
                                                  ("bounced", "provider", False)]
    assert st.receipt_applies("sent", "bounced") and not st.receipt_applies("failed", "sent")


async def test_bounce_from_the_test_inbox_and_a_bounced_address(client, ctx):
    h, tid = ctx["h"], ctx["tid"]
    _, cid = await _setup(client, ctx)
    m = await _compose(client, h, cid)
    await send.deliver_due(tid)
    r = await client.post(f"{API}/channels/test-inbox/{m['id']}/bounce", headers=h)
    assert r.json()["status"] == "bounced" and "(simulated)" in r.json()["reason"]
    _, bad = await _setup(client, ctx, settings=False, email="bounce@simulator.test", name="B")
    b = await _compose(client, h, bad)
    await send.deliver_due(tid)
    got = (await client.get(f"{API}/messages/{b['id']}", headers=h)).json()
    assert got["status"] == "bounced"
    assert {r["kind"] for r in (await client.get(f"{API}/audit?kind=message.bounced",
                                                 headers=h)).json()} == {"message.bounced"}


# Incoming --------------------------------------------------------------------------------

async def test_incoming_is_matched_only_on_one_exact_address(client, ctx):
    h, tid = ctx["h"], ctx["tid"]
    acc, cid = await _setup(client, ctx)
    m = await _compose(client, h, cid)
    await send.deliver_due(tid)
    # Two contacts share an address (not merged yet): no guess is made.
    for n in ("Ravi", "Ravi K"):
        await client.post(f"{API}/contacts", headers=h, json={
            "account_id": acc, "full_name": n, "email": "purchase@kpl.example.in"})
    async with tenant_session(tid) as s:
        shared = await send.receive(s, tid, from_address="Purchase@KPL.example.in",
                                    subject="Hi", body="नमस्ते, कृपया कोटेशन भेजें। धन्यवाद",
                                    provider="simulator", provider_ref="<a1>",
                                    source="simulator")
        assert shared.contact_id is None and shared.language == "hi"
        stranger = await send.receive(s, tid, from_address="who@else.in", subject="Hi",
                                      body="Hello", provider="simulator", provider_ref="<a2>",
                                      source="simulator")
        assert stranger.contact_id is None
        sent = await s.get(Message, uuid.UUID(m["id"]))
        answer = await send.receive(s, tid, from_address="anita@kpl.example.in", subject="Re",
                                    body="Thanks!", provider="simulator", provider_ref="<a3>",
                                    in_reply_to_ref=sent.provider_ref, source="simulator")
        assert answer.contact_id == uuid.UUID(cid) and answer.in_reply_to_id == sent.id
        again = await send.receive(s, tid, from_address="anita@kpl.example.in", subject="Re",
                                   body="Thanks!", provider="simulator", provider_ref="<a3>",
                                   source="simulator")
        assert again.id == answer.id  # the same message delivered twice is kept once
    # A reply to someone who is not a contact goes to the address they wrote from.
    r = await client.post(f"{API}/messages", headers=h, json={
        "in_reply_to_id": str(stranger.id), "purpose": "reply", "subject": "Re: Hi",
        "body": "Thank you for writing.", "send": True})
    assert r.json()["status"] == "queued" and r.json()["to_address"] == "who@else.in"


# Agents: controls checked again just before sending ---------------------------------------

async def test_a_kill_switch_or_takeover_stops_an_agents_queued_email(client, ctx):
    h, tid = ctx["h"], ctx["tid"]
    _, cid = await _setup(client, ctx)
    r = await client.post(f"{API}/agents/sandbox/send-test-email", headers=h,
                          json={"contact_id": cid, "subject": "Test", "body": "Hello from "
                                "the sandbox"})
    assert r.status_code == 200 and r.json()["status"] == "done", r.text
    mid = r.json()["message_id"]
    async with tenant_session(tid) as s:
        msg = await s.get(Message, uuid.UUID(mid))
        # Fixed test text, not written by the AI, so no language review is sampled.
        assert msg.status == "queued"
        assert msg.ai_generated and msg.disclosure == "Sent by our AI assistant"
        assert msg.provider == "simulator" and msg.sent_by == "agent:sandbox"
    assert (await client.get(f"{API}/languages/reviews", headers=h)).json() == []
    stop = await client.post(f"{API}/kill-switches", headers=h, json={
        "scope": "agent", "agent_key": "sandbox", "reason": "checking the controls"})
    assert stop.status_code == 201, stop.text
    assert await send.deliver_due(tid) == {"blocked": 1}
    got = (await client.get(f"{API}/messages/{mid}", headers=h)).json()
    assert got["status"] == "blocked" and got["reason"].startswith("Agents are stopped")
    # A person's own message is not an agent's: the switch does not stop it.
    mine = await _compose(client, h, cid)
    assert mine["status"] == "queued"


async def test_the_sandbox_can_only_reach_the_test_inbox_even_when_live(client, ctx,
                                                                        monkeypatch):
    h, tid = ctx["h"], ctx["tid"]
    _, cid = await _setup(client, ctx, settings={"review_first": 0, "review_every": 1000})
    await _live(client, h, monkeypatch)
    real = []

    async def smtp_send(self, mail):
        real.append(mail)
        return SendResult(SENT, provider_ref=f"<{mail.message_id}@kpl.example.in>")

    monkeypatch.setattr(smtp_mod.SmtpAdapter, "send", smtp_send)
    r = await client.post(f"{API}/agents/sandbox/send-test-email", headers=h,
                          json={"contact_id": cid, "body": "Hello"})
    assert r.json()["message_status"] == "queued", r.text
    mine = await _compose(client, h, cid)
    await send.deliver_due(tid)
    sandbox = (await client.get(f"{API}/messages/{r.json()['message_id']}", headers=h)).json()
    assert sandbox["provider"] == "simulator" and sandbox["status"] == "sent"
    person = (await client.get(f"{API}/messages/{mine['id']}", headers=h)).json()
    assert person["provider"] == "smtp" and person["from_address"] == "sales@kpl.example.in"
    assert [m.message_id for m in real] == [uuid.UUID(mine["id"])]


# The email account and live sending (L2) ---------------------------------------------------

async def _live(client, h, monkeypatch, ok=True):
    async def check(self):
        return ok, "Connected and logged in" if ok else "The mail server refused Forge's " \
            "username or password"

    monkeypatch.setattr(smtp_mod.SmtpAdapter, "check", check)
    r = await client.put(f"{API}/channels/email-account", headers=h, json={
        "host": "smtp.gmail.com", "port": 587, "security": "starttls",
        "username": "sales@kpl.example.in", "password": "app-pass-1234-wxyz",
        "from_address": "Sales@KPL.example.in", "from_name": "KPL Sales"})
    assert r.status_code == 200, r.text
    c = await client.post(f"{API}/channels/email-account/check", headers=h)
    assert c.json()["ok"] is ok
    return await client.post(f"{API}/channels/mode", headers=h, json={"mode": "live"})


async def test_the_email_password_is_encrypted_and_never_returned(client, ctx, monkeypatch):
    h, tid = ctx["h"], ctx["tid"]
    failed = await _live(client, h, monkeypatch, ok=False)
    assert failed.status_code == 400  # live needs a passing check
    r = await _live(client, h, monkeypatch)
    assert r.status_code == 200 and r.json()["email_mode"] == "live"
    s_ = (await client.get(f"{API}/channels/settings", headers=h)).json()
    acc = s_["email_account"]
    assert acc["password_hint"] == "…wxyz" and acc["from_address"] == "sales@kpl.example.in"
    assert "app-pass" not in str(s_)
    async with tenant_session(tid) as s:
        row = await s.scalar(select(EmailAccount))
        assert "app-pass" not in row.password_enc and "app-pass" not in repr(row)
        assert accounts.smtp_adapter(row)._password == "app-pass-1234-wxyz"
        trail = list(await s.scalars(select(AuditRecord).where(
            AuditRecord.kind.like("channels.%"))))
    assert {t.kind for t in trail} >= {"channels.email_account_saved",
                                       "channels.email_account_checked",
                                       "channels.mode_changed"}
    assert all("app-pass" not in str(t.data) for t in trail)
    # Changing the account without a password keeps it, and needs a new check.
    r = await client.put(f"{API}/channels/email-account", headers=h, json={
        "host": "smtp.gmail.com", "port": 465, "security": "ssl",
        "username": "sales@kpl.example.in", "from_address": "sales@kpl.example.in"})
    assert r.json()["last_check_ok"] is None and r.json()["password_hint"] == "…wxyz"
    _, viewer = await _user_with_role(client, ctx, "Clerk", ["accounts.read"])
    assert (await client.put(f"{API}/channels/email-account", headers=viewer,
                             json={})).status_code in (403, 422)


async def test_a_refused_login_holds_messages_until_the_account_works(client, ctx, monkeypatch):
    h, tid = ctx["h"], ctx["tid"]
    _, cid = await _setup(client, ctx)
    await _live(client, h, monkeypatch)

    async def refused(self, mail):
        return SendResult("failed", reason="The mail server refused Forge's username or "
                                           "password", account_problem=True)

    monkeypatch.setattr(smtp_mod.SmtpAdapter, "send", refused)
    m = await _compose(client, h, cid)
    assert await send.deliver_due(tid) == {"retry": 1}
    s_ = (await client.get(f"{API}/channels/settings", headers=h)).json()
    assert s_["email_account"]["last_check_ok"] is False
    later = datetime.now(UTC) + timedelta(minutes=2)
    assert await send.deliver_due(tid, later) == {"held": 1}  # account not working: waits
    got = (await client.get(f"{API}/messages/{m['id']}", headers=h)).json()
    assert got["status"] == "held" and got["reason"].startswith("The email account is not")


def test_smtp_says_whether_an_email_could_have_gone(monkeypatch):
    class Fake:
        mode = "ok"

        def __init__(self, *a, **kw):
            if Fake.mode == "connect":
                raise OSError("refused")

        def ehlo(self):
            pass

        def starttls(self, context=None):
            pass

        def login(self, u, p):
            if Fake.mode == "auth":
                raise smtplib.SMTPAuthenticationError(535, b"bad")

        def send_message(self, msg):
            Fake.sent = msg
            if Fake.mode == "refused":
                raise smtplib.SMTPRecipientsRefused({"x@y.in": (550, b"no such user")})
            if Fake.mode == "later":
                raise smtplib.SMTPRecipientsRefused({"x@y.in": (451, b"try later")})
            if Fake.mode == "drop":
                raise smtplib.SMTPServerDisconnected("gone")

        def noop(self):
            pass

        def quit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP", Fake)
    a = smtp_mod.SmtpAdapter("smtp.example.in", 587, "starttls", "u", "p")
    mail = OutgoingEmail(message_id=uuid.uuid4(), to="x@y.in", from_address="s@kpl.in",
                         from_name="KPL Sales", reply_to=None, subject="Hi", text="Hello",
                         headers={"List-Unsubscribe": "<https://f/u>"})
    expected = {"ok": "sent", "connect": "retry", "auth": "failed", "refused": "bounced",
                "later": "retry", "drop": "unknown"}
    for mode, outcome in expected.items():
        Fake.mode = mode
        res = a._send(mail)
        assert res.outcome == outcome, mode
        assert "x@y.in" not in (res.reason or "")
    Fake.mode = "ok"
    assert a._send(mail).provider_ref == f"<{mail.message_id}@kpl.in>"
    assert Fake.sent["From"] == "KPL Sales <s@kpl.in>"
    assert Fake.sent["List-Unsubscribe"] == "<https://f/u>"
    Fake.mode = "auth"
    assert a._send(mail).account_problem and not a._check()[0]
    assert "p" not in repr(a).split()[-1]


# Staff notifications by email (H5) ---------------------------------------------------------

async def test_approval_cards_also_go_by_email_with_a_link_and_no_approving(client, ctx):
    from app.approvals import notify as inbox_notify

    h, tid = ctx["h"], ctx["tid"]
    await _setup(client, ctx)
    inbox_notify.register()
    await checker(client, ctx)
    await _rule(client, h, outcome="ask_person",
                conditions=[{"fact": "amount", "op": "gt", "value": 1000}])
    r = await client.post(f"{API}/agents/sandbox/try", headers=h, json={"amount": 5000})
    assert r.json()["status"] == "waiting", r.text
    await drain()
    staff = (await client.get(f"{API}/messages?audience=staff", headers=h)).json()
    assert len(staff) == 1 and staff[0]["purpose"] == "staff"
    assert staff[0]["subject"].startswith("Waiting for you")
    assert f"/decisions?card={r.json()['decision_id']}" in staff[0]["body"]
    assert "cannot approve anything" in staff[0]["body"]
    assert staff[0]["status"] == "queued"
    await drain()  # handled once
    assert len((await client.get(f"{API}/messages?audience=staff", headers=h)).json()) == 1
    customers = (await client.get(f"{API}/messages", headers=h)).json()
    assert all(m["purpose"] != "staff" for m in customers)
    await send.deliver_due(tid)
    assert (await client.get(f"{API}/messages/{staff[0]['id']}", headers=h)).json()[
        "status"] == "sent"


# Erasure (Step 4) reaches Step 6's data ---------------------------------------------------

async def test_erasure_clears_message_addresses_and_consent_notes(client, ctx):
    from app.privacy.erasure import erase_contact

    h, tid = ctx["h"], ctx["tid"]
    _, cid = await _setup(client, ctx)
    m = await _compose(client, h, cid)
    await send.deliver_due(tid)
    await client.post(f"{API}/contacts/{cid}/consent", headers=h, json={
        "channel": "email", "status": "withdrawn", "how": "phone",
        "note": "Anita called from 9845012345"})
    async with tenant_session(tid) as s:
        await erase_contact(s, tid, uuid.UUID(cid), actor="test", reason="asked",
                            requested_by="test")
    async with tenant_session(tid) as s:
        msg = await s.get(Message, uuid.UUID(m["id"]))
        assert (msg.body, msg.to_address, msg.erased_at is not None) == ("[erased]", None, True)
        consents = list(await s.scalars(select(Consent)))
        assert consents and all(c.note is None for c in consents)
    blocked = await _compose(client, h, cid)
    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "This contact's personal data was erased"


# Nothing else sends -----------------------------------------------------------------------

def test_only_the_channel_package_reaches_adapters_or_moves_a_message_on():
    root = Path(__file__).resolve().parents[1] / "app"
    problems = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel.startswith("channels/"):
            continue
        text = path.read_text(encoding="utf-8")
        if "channels.adapters" in text and rel != "api/v1/channels.py":
            problems.append(f"{rel} imports a channel adapter")
        for m in re.finditer(r"Message\((?:[^()]|\([^()]*\))*\)", text, re.DOTALL):
            status = re.search(r"status=\"(\w+)\"", m.group(0))
            if status and status.group(1) != "draft":
                problems.append(f"{rel} creates a message with status {status.group(1)}")
        if re.search(r"\b(msg|message|m)\.status\s*=(?!=)", text):
            problems.append(f"{rel} changes a message's status")
        if re.search(r"update\(Message\)", text):
            problems.append(f"{rel} updates messages in bulk")
    assert problems == []


async def test_consent_records_cannot_be_changed_or_deleted(ctx):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    tid = ctx["tid"]
    async with tenant_session(tid) as s:
        c = Contact(tenant_id=tid, full_name="Test Person", normalized_name="test person")
        s.add(c)
        await s.flush()
        await consent_mod.record(s, tid, c, channel="email", status="withdrawn", how="phone",
                                 actor="test")
    for sql in ("UPDATE consents SET status = 'granted'", "DELETE FROM consents"):
        with pytest.raises(DBAPIError):
            async with tenant_session(tid) as s:
                await s.execute(text(sql))
    async with tenant_session(tid) as s:
        await s.execute(text("UPDATE consents SET note = NULL"))  # erasure may blank text
    assert chain


async def test_settings_say_when_unsubscribe_links_only_work_on_this_computer(client, ctx,
                                                                             monkeypatch):
    from app.config import get_settings

    r = await client.get(f"{API}/channels/settings", headers=ctx["h"])
    assert r.json()["links_public"] is False  # http://localhost:8000 by default
    monkeypatch.setattr(get_settings(), "public_base_url", "https://crm.kpl.example.in")
    r = await client.get(f"{API}/channels/settings", headers=ctx["h"])
    assert r.json()["links_public"] is True
