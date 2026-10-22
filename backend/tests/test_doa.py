"""The discount approval rulebook (F3.4; Z1-Z10 A, 2026-10-21).

Most of these need no database: the matrix and the chain it works out are plain
arithmetic, so they run in milliseconds and say exactly what the rulebook means. The last
few need Postgres, because dated versions, row-level security and the audit chain cannot
be shown without it.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError

from app.audit.chain import verify
from app.core.errors import ValidationFailed
from app.cpq import doa
from app.db import get_sessionmaker, tenant_session
from app.models import AuditRecord, Tenant
from tests.conftest import login
from tests.test_agents import API, _user_with_role, ctx, decide  # noqa: F401


def facts(discount="0", value="100000", margin=None, floor=None) -> doa.Facts:
    return doa.Facts(discount_pct=Decimal(discount), value=Decimal(value),
                     margin_pct=Decimal(margin) if margin is not None else None,
                     floor_margin_pct=Decimal(floor) if floor is not None else None)


def matrix(*rows: dict) -> dict:
    return {"rows": list(rows)}


def row(approvers, *, discount=None, margin_upto=None, below_floor=False, value=None,
        separate=False, note=None) -> dict:
    when: dict = {}
    if discount is not None:
        when["discount_pct_from"] = discount
    if margin_upto is not None:
        when["margin_pct_upto"] = margin_upto
    if below_floor:
        when["below_floor"] = True
    if value is not None:
        when["value_from"] = value
    out: dict = {"when": when, "approvers": list(approvers)}
    if separate:
        out["separate_stages"] = True
    if note:
        out["note"] = note
    return out


def test_a_quote_nothing_matches_needs_no_approval():
    m = matrix(row(["sm"], discount="5"))
    chain = doa.stages_for(m, facts(discount="2"))
    assert chain.needed is False
    assert chain.roles() == []
    assert chain.matched == []


def test_the_threshold_is_met_at_the_number_itself():
    m = matrix(row(["sm"], discount="5"))
    assert doa.stages_for(m, facts(discount="5")).roles() == ["sm"]
    assert doa.stages_for(m, facts(discount="4.99")).roles() == []


def test_several_rows_combine_without_asking_the_same_role_twice():
    """Z3, his words: keep every stage and its order, but no redundant approvals."""
    m = matrix(row(["sm"], discount="5"),
               row(["sm", "fin"], discount="15"),
               row(["fin"], below_floor=True))
    chain = doa.stages_for(m, facts(discount="20", margin="3", floor="15"))
    assert chain.roles() == ["sm", "fin"]
    assert chain.matched == [1, 2, 3]
    assert chain.problems == []
    # Each stage says which rows asked for it, which is what the card shows.
    assert chain.stages[0].rows == [1, 2]
    assert chain.stages[1].rows == [2, 3]


def test_a_rows_order_survives_the_merge():
    """The first row asks for Finance alone; the second wants the manager first."""
    m = matrix(row(["fin"], below_floor=True), row(["sm", "fin"], discount="15"))
    chain = doa.stages_for(m, facts(discount="20", margin="3", floor="15"))
    assert chain.roles() == ["sm", "fin"]
    assert chain.problems == []


def test_a_row_can_insist_on_a_stage_of_its_own():
    m = matrix(row(["fin"], below_floor=True),
               row(["sm", "fin"], discount="15", separate=True))
    chain = doa.stages_for(m, facts(discount="20", margin="3", floor="15"))
    assert chain.roles() == ["fin", "sm", "fin"]


def test_two_rows_that_disagree_about_the_order_are_refused_when_saved():
    m = matrix(row(["sm", "fin"], discount="5"), row(["fin", "sm"], below_floor=True))
    with pytest.raises(ValidationFailed) as e:
        doa.check(m)
    assert any("opposite orders" in p for p in e.value.problems)


def test_a_disagreement_that_got_through_is_reported_not_hidden():
    m = matrix(row(["sm", "fin"], discount="5"), row(["fin", "sm"], below_floor=True))
    chain = doa.stages_for(m, facts(discount="20", margin="3", floor="15"))
    assert chain.problems and "two different orders" in chain.problems[0]
    assert sorted(chain.roles()) == ["fin", "sm"]


def test_a_quote_with_no_margin_counts_as_under_its_floor():
    """A margin that cannot be worked out cannot clear a floor either."""
    m = matrix(row(["fin"], below_floor=True))
    assert doa.stages_for(m, facts(margin=None, floor="15")).roles() == ["fin"]
    assert doa.stages_for(m, facts(margin="20", floor="15")).roles() == []


def test_with_no_floor_set_nothing_is_under_it():
    m = matrix(row(["fin"], below_floor=True))
    assert doa.stages_for(m, facts(margin="1", floor=None)).roles() == []


def test_a_margin_test_catches_a_quote_whose_margin_is_not_known():
    m = matrix(row(["fin"], margin_upto="10"))
    assert doa.stages_for(m, facts(margin=None)).roles() == ["fin"]
    assert doa.stages_for(m, facts(margin="10")).roles() == ["fin"]
    assert doa.stages_for(m, facts(margin="10.01")).roles() == []


def test_a_big_quote_needs_approval_even_at_a_small_discount():
    m = matrix(row(["fin"], value="2500000"))
    assert doa.stages_for(m, facts(discount="0", value="2500000")).roles() == ["fin"]
    assert doa.stages_for(m, facts(discount="0", value="2499999")).roles() == []


def test_every_condition_in_a_row_has_to_hold():
    m = matrix(row(["fin"], discount="10", value="1000000"))
    assert doa.stages_for(m, facts(discount="10", value="1000000")).roles() == ["fin"]
    assert doa.stages_for(m, facts(discount="10", value="999999")).roles() == []
    assert doa.stages_for(m, facts(discount="9", value="1000000")).roles() == []


def test_the_card_can_say_why_in_the_companys_own_words():
    m = matrix(row(["fin"], discount="15", note="Deep discounts go to Finance"))
    chain = doa.stages_for(m, facts(discount="15"))
    assert chain.stages[0].reasons == ["Deep discounts go to Finance"]


def test_without_a_note_the_reason_is_the_row_itself():
    m = matrix(row(["fin"], discount="15", below_floor=True))
    chain = doa.stages_for(m, facts(discount="15", margin="3", floor="15"))
    assert chain.stages[0].reasons == ["a discount of 15% or more and a quote under its floor"]


def test_a_row_that_says_nothing_about_when_is_refused():
    with pytest.raises(ValidationFailed) as e:
        doa.check(matrix({"when": {}, "approvers": ["fin"]}))
    assert any("say when the row applies" in p for p in e.value.problems)


def test_a_row_with_no_approver_is_refused():
    with pytest.raises(ValidationFailed) as e:
        doa.check(matrix(row([], discount="5")))
    assert any("at least one approving role" in p for p in e.value.problems)


def test_the_same_role_twice_in_one_row_is_refused():
    with pytest.raises(ValidationFailed) as e:
        doa.check(matrix(row(["fin", "fin"], discount="5")))
    assert any("twice" in p for p in e.value.problems)


def test_a_percentage_outside_0_to_100_is_refused():
    with pytest.raises(ValidationFailed) as e:
        doa.check(matrix(row(["fin"], discount="120")))
    assert any("percentage from 0 to 100" in p for p in e.value.problems)


def test_a_checked_matrix_comes_back_tidy():
    out = doa.check(matrix(row(["sm"], discount="5.50", value="1,00,000",
                               note="x" * 400)))
    r = out["rows"][0]
    assert r["when"] == {"discount_pct_from": "5.5", "value_from": "100000"}
    assert r["approvers"] == ["sm"]
    assert len(r["note"]) == 200


def test_an_unreadable_row_is_left_out_rather_than_guessed():
    chain = doa.stages_for({"rows": [{"when": {"discount_pct_from": "5"}, "approvers": []}]},
                           facts(discount="20"))
    assert chain.roles() == []


def test_the_clock_is_four_working_hours_with_a_reminder_at_two():
    assert doa.SLA_HOURS * doa.REMINDER_AT == doa.TARGET_MEDIAN_HOURS


def test_the_sample_every_row_says_when_it_applies_and_who_approves():
    for r in doa.SAMPLE_MATRIX["rows"]:
        assert r["when"], r
        assert r["approver_names"], r


# The rulebook in the database --------------------------------------------------------------
# These need Postgres: they are about dated versions, row-level security and the audit
# chain, none of which can be shown without it.

async def test_a_matrix_is_never_edited_and_a_quote_is_judged_by_the_one_of_its_day(
        client, ctx):  # noqa: F811
    tid = ctx["tid"]
    later = date.today() + timedelta(days=30)
    async with tenant_session(tid) as s:
        await doa.save(s, tid, matrix(row(["Sales Manager"], discount="5")), "admin",
                       effective_from=date.today(), note="to start with")
        await doa.save(s, tid, matrix(row(["Sales Manager"], discount="5"),
                                      row(["Finance"], discount="15")), "admin",
                       effective_from=later, note="tighter from next month")
        await s.commit()

    async with tenant_session(tid) as s:
        today = await doa.in_force(s, tid, date.today())
        assert len(today.matrix["rows"]) == 1  # next month's row is not in force yet
        assert [v.effective_from for v in await doa.versions(s, tid)] == [later, date.today()]

        # A quote today needs one approval; the same quote next month needs two.
        now = await doa.quote_chain(s, tid, date.today(), facts(discount="20"))
        then = await doa.quote_chain(s, tid, later, facts(discount="20"))
    assert now.roles() == ["Sales Manager"]
    assert then.roles() == ["Sales Manager", "Finance"]


async def test_with_no_matrix_a_discount_is_not_waved_through(client, ctx):  # noqa: F811
    async with tenant_session(ctx["tid"]) as s:
        chain = await doa.quote_chain(s, ctx["tid"], date.today(), facts(discount="40"))
    assert not chain.needed
    assert any(doa.NO_MATRIX in p for p in chain.problems)


async def test_setting_the_matrix_is_written_into_the_audit_chain(client, ctx):  # noqa: F811
    tid = ctx["tid"]
    async with tenant_session(tid) as s:
        saved = await doa.save(s, tid, matrix(row(["Finance"], below_floor=True)), "admin",
                               effective_from=date.today())
        await s.commit()
    async with tenant_session(tid) as s:
        recs = list(await s.scalars(select(AuditRecord).where(
            AuditRecord.kind == "doa.matrix.set")))
        assert len(recs) == 1
        assert recs[0].record_type == "doa_matrix" and recs[0].record_id == saved.id
        assert (await verify(s, tid)).ok  # the record did not break the chain


async def test_one_company_never_sees_another_companys_rulebook(client, make_tenant):
    a_slug, a_email, a_pw = await make_tenant()
    b_slug, b_email, b_pw = await make_tenant()
    async with get_sessionmaker()() as s:
        a = (await s.scalar(select(Tenant).where(Tenant.slug == a_slug))).id
        b = (await s.scalar(select(Tenant).where(Tenant.slug == b_slug))).id
    async with tenant_session(a) as s:
        await doa.save(s, a, matrix(row(["Finance"], discount="5")), "admin",
                       effective_from=date.today())
        await s.commit()
    async with tenant_session(b) as s:
        assert await doa.in_force(s, b, date.today()) is None
        assert await doa.versions(s, b) == []


async def test_contradictory_rows_are_refused_before_they_reach_the_database(
        client, ctx):  # noqa: F811
    async with tenant_session(ctx["tid"]) as s:
        with pytest.raises(ValidationFailed):
            await doa.save(s, ctx["tid"],
                           matrix(row(["sm", "fin"], discount="5"),
                                  row(["fin", "sm"], discount="10")),
                           "admin", effective_from=date.today())


async def test_the_database_itself_refuses_to_edit_or_remove_a_version(
        client, ctx):  # noqa: F811
    """Z4 A is not left to the code that writes: the app's own login cannot change a row.

    0001 grants update and delete on every new table by default, so 0018 takes both away
    again. Without that, "a matrix is never edited" would hold only as long as nobody
    wrote an UPDATE.
    """
    tid = ctx["tid"]
    async with tenant_session(tid) as s:
        saved = await doa.save(s, tid, matrix(row(["Finance"], discount="5")), "admin",
                               effective_from=date.today())
        await s.commit()
    for sql in (text("update doa_matrices set note = 'edited' where id = :i"),
                text("delete from doa_matrices where id = :i")):
        async with tenant_session(tid) as s:
            with pytest.raises(ProgrammingError) as e:
                await s.execute(sql, {"i": saved.id})
            assert "permission denied" in str(e.value).lower()


# The screen behind it ----------------------------------------------------------------------

D = f"{API}/doa"


async def _role(client, ctx, name: str, permissions=()) -> str:  # noqa: F811
    r = await client.post(f"{API}/roles", headers=ctx["h"],
                          json={"name": name, "permissions": list(permissions)})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def test_the_matrix_screen_shows_what_is_in_force_who_is_in_each_role_and_a_sample(
        client, ctx):  # noqa: F811
    sm = await _role(client, ctx, "Sales Manager")
    fin = await _role(client, ctx, "Finance")
    h = ctx["h"]

    out = (await client.get(f"{D}/matrix", headers=h)).json()
    assert out["in_force"] is None  # nothing written yet
    assert out["sample"]["rows"], "a new company has something to start from"
    assert out["clock"]["target_median_hours"] == "2"

    r = await client.put(f"{D}/matrix", headers=h, json={
        "effective_from": date.today().isoformat(),
        "rows": [{"when": {"discount_pct_from": "5"}, "approvers": [sm]},
                 {"when": {"discount_pct_from": "15"}, "approvers": [sm, fin]}],
        "note": "to start with"})
    assert r.status_code == 200, r.text
    # Nobody is in either role yet, and that is said rather than refused.
    assert any("Sales Manager" in w for w in r.json()["warnings"])

    out = (await client.get(f"{D}/matrix", headers=h)).json()
    names = [[a["name"] for a in row["approver_roles"]]
             for row in out["in_force"]["matrix"]["rows"]]
    assert names == [["Sales Manager"], ["Sales Manager", "Finance"]]
    assert len(out["versions"]) == 1


async def test_a_role_that_is_not_this_companys_is_refused(client, ctx):  # noqa: F811
    r = await client.put(f"{D}/matrix", headers=ctx["h"], json={
        "effective_from": date.today().isoformat(),
        "rows": [{"when": {"discount_pct_from": "5"}, "approvers": [str(uuid.uuid4())]}]})
    assert r.status_code == 422, r.text
    assert "is not a role of this company" in str(r.json())


async def test_a_rulebook_can_be_tried_before_a_real_quote_meets_it(client, ctx):  # noqa: F811
    sm = await _role(client, ctx, "Sales Manager")
    fin = await _role(client, ctx, "Finance")
    h = ctx["h"]
    await client.put(f"{D}/matrix", headers=h, json={
        "effective_from": date.today().isoformat(),
        "rows": [{"when": {"discount_pct_from": "5"}, "approvers": [sm]},
                 {"when": {"below_floor": True}, "approvers": [fin]}]})

    out = (await client.post(f"{D}/matrix/try", headers=h,
                             json={"discount_pct": "2", "value": "50000"})).json()
    assert out["needed"] is False and out["stages"] == []

    out = (await client.post(f"{D}/matrix/try", headers=h, json={
        "discount_pct": "10", "value": "50000",
        "margin_pct": "9", "floor_margin_pct": "15"})).json()
    assert out["below_floor"] is True
    assert [s["role_name"] for s in out["stages"]] == ["Sales Manager", "Finance"]
    assert out["rows"] == [1, 2]
    # The screen reads these keys off each stage by name; renaming one here breaks it.
    assert set(out["stages"][0]) == {"role", "role_name", "rows", "because"}
    assert out["stages"][0]["because"] == ["a discount of 5% or more"]


async def test_setting_the_matrix_needs_its_own_permission(client, ctx):  # noqa: F811
    _, viewer = await _user_with_role(client, ctx, "Quote Reader", ["quotes.read"])
    body = {"effective_from": date.today().isoformat(),
            "rows": [{"when": {"discount_pct_from": "5"},
                      "approvers": [await _role(client, ctx, "Sales Manager")]}]}
    assert (await client.put(f"{D}/matrix", headers=viewer, json=body)).status_code == 403
    assert (await client.get(f"{D}/matrix", headers=viewer)).status_code == 200


# One quote's discount, and the card it raises (Z5 A, Z7 A) ---------------------------------

TARGET, FLOOR = "30", "10"


async def _matrix(client, ctx, *rows: dict, at: str | None = None) -> dict:  # noqa: F811
    r = await client.put(f"{API}/doa/matrix", headers=ctx["h"],
                         json={"rows": list(rows),
                               "effective_from": at or date.today().isoformat()})
    assert r.status_code == 200, r.text
    return r.json()


async def _discounted_quote(client, ctx, ask: str = "20"):  # noqa: F811
    """A quote the rep has asked `ask` percent off, priced and ready to be approved.

    At a 30% target and a 10% floor, 20% off still clears the floor, so the only reason an
    approval is needed is the depth of the discount itself.
    """
    from tests.test_pricing import Q, _flanges_quote, policy, set_policy

    h = ctx["h"]
    await set_policy(client, h, policy(target=TARGET, floor=FLOOR))
    _, q = await _flanges_quote(client, h, [
        ("Weld neck flange", 40, {"size": "150", "class": "300", "material": "F316",
                                  "facing": "RF"})])
    line = q["active"]["lines"][0]
    r = await client.put(f"{Q}/{q['id']}/lines/{line['id']}/ask", headers=h,
                         json={"discount_pct": ask})
    assert r.status_code == 200, r.text
    return q["id"], r.json()


async def _approver(client, ctx, name: str, role_ids: list[str]) -> dict:  # noqa: F811
    """A person in the roles the matrix names, who may decide a card. The chain is by
    role (Z2 A), so being in the role is what makes them an approver, not a permission."""
    email = f"{name.lower().replace(' ', '')}@{ctx['slug']}.example.com"
    r = await client.post(f"{API}/users", headers=ctx["h"], json={
        "email": email, "full_name": name, "password": "Passw0rd!x", "role_ids": role_ids})
    assert r.status_code == 201, r.text
    return await login(client, ctx["slug"], email, "Passw0rd!x")


async def _ask(client, h, quote_id: str, **body) -> dict:
    r = await client.post(f"{API}/doa/quote/{quote_id}/approval", headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def test_a_quotes_discount_raises_one_card_with_the_chain_the_matrix_asks_for(
        client, ctx):  # noqa: F811
    """Z5 A: one card for the quote, not one per discounted line."""
    h = ctx["h"]
    sm = await _role(client, ctx, "Sales Manager")
    fin = await _role(client, ctx, "Finance")
    await _matrix(client, ctx,
                  {"when": {"discount_pct_from": "5"}, "approvers": [sm],
                   "note": "Any discount worth the name"},
                  {"when": {"discount_pct_from": "15"}, "approvers": [sm, fin]})
    quote_id, priced = await _discounted_quote(client, ctx)
    assert priced["active"]["lines"][0]["price_status"] == "ok"  # the floor is not the issue

    got = await _ask(client, h, quote_id, why="Repeat buyer, 3 orders last year",
                     competitor="Tata quoted 8% less")
    assert got["needed"] is True and got["below_floor"] is False
    assert [s["role_name"] for s in got["stages"]] == ["Sales Manager", "Finance"]
    assert got["rows"] == [1, 2]
    assert Decimal(got["discount_pct"]) == Decimal("20")
    assert got["discounted_line_count"] == 1
    assert got["card"]["status"] == "pending" and got["card"]["step"] == 1
    assert got["card"]["of"] == 2 and got["card"]["stale"] is False

    card = (await client.get(f"{API}/decisions/{got['card']['id']}", headers=h)).json()
    assert card["title"] == f"Approve 20% off {priced['number']} R1"
    assert card["reasons"][0].startswith("The rulebook asks for this: Any discount")
    shown = {f["label"]: f["value"] for f in card["facts"]}
    assert Decimal(shown["Discount %"]) == Decimal("20")
    assert shown["Amount (INR)"].startswith("₹")
    assert Decimal(shown["Margin %"]) > Decimal(shown["Floor margin %"])
    # The rep's words travel with the card, as the rep's words (Z7 A).
    assert card["args"]["why"] == "Repeat buyer, 3 orders last year"
    assert card["args"]["competitor"] == "Tata quoted 8% less"
    assert card["card_hash"]  # fingerprinted, so the card is checked on submit (H7)
    assert "their own words, not something Forge checked" in card["recommendation"]


async def test_the_same_quote_asked_twice_does_not_collect_two_cards(client, ctx):  # noqa: F811
    h = ctx["h"]
    sm = await _role(client, ctx, "Sales Manager")
    await _matrix(client, ctx, {"when": {"discount_pct_from": "5"}, "approvers": [sm]})
    quote_id, _ = await _discounted_quote(client, ctx)
    first = await _ask(client, h, quote_id, why="Asked once")
    again = await _ask(client, h, quote_id, why="Asked twice")
    assert again["card"]["id"] == first["card"]["id"]
    assert again["card"]["why"] == "Asked once"  # the card a person may already have read


async def test_a_discount_the_rulebook_says_nothing_about_needs_no_approval(
        client, ctx):  # noqa: F811
    h = ctx["h"]
    sm = await _role(client, ctx, "Sales Manager")
    await _matrix(client, ctx, {"when": {"discount_pct_from": "25"}, "approvers": [sm]})
    quote_id, _ = await _discounted_quote(client, ctx, ask="20")
    got = await _ask(client, h, quote_id)
    assert got["needed"] is False and got["card"] is None and got["stages"] == []


async def test_with_no_rulebook_the_discount_still_goes_to_a_person(client, ctx):  # noqa: F811
    """A company that has written no matrix has not thereby approved every discount."""
    h = ctx["h"]
    quote_id, _ = await _discounted_quote(client, ctx)
    got = await _ask(client, h, quote_id)
    assert got["needed"] is True and got["no_matrix"] is True
    assert got["stages"] == [] and got["card"]["status"] == "pending"
    card = (await client.get(f"{API}/decisions/{got['card']['id']}", headers=h)).json()
    assert "no delegation of authority has been set" in card["reasons"][0]
    assert "Quotes → Approval rules" in card["recommendation"]


async def test_the_rep_who_asked_can_never_sign_their_own_discount_off(client, ctx):  # noqa: F811
    """Four eyes: the admin who asked holds discounts.approve and still cannot decide."""
    h = ctx["h"]
    sm = await _role(client, ctx, "Sales Manager")
    await _matrix(client, ctx, {"when": {"discount_pct_from": "5"}, "approvers": [sm]})
    quote_id, _ = await _discounted_quote(client, ctx)
    got = await _ask(client, h, quote_id)
    mine = (await client.get(f"{API}/decisions/{got['card']['id']}", headers=h)).json()
    assert not mine["can_decide"] and "you asked for it" in str(mine["why_not"])


async def test_a_countered_discount_is_chained_by_its_own_numbers(client, ctx):  # noqa: F811
    """The safety property behind Z3: countering 20% down to 6% must not keep Finance's
    stage just because the first ask needed it, nor drop a stage 6% still needs."""
    h = ctx["h"]
    can = ["quotes.read", "costs.view", "discounts.approve", "decisions.decide"]
    sm = await _role(client, ctx, "Sales Manager", can)
    fin = await _role(client, ctx, "Finance", can)
    await _matrix(client, ctx,
                  {"when": {"discount_pct_from": "5"}, "approvers": [sm]},
                  {"when": {"discount_pct_from": "15"}, "approvers": [sm, fin]})
    quote_id, priced = await _discounted_quote(client, ctx)
    got = await _ask(client, h, quote_id)
    assert [s["role_name"] for s in got["stages"]] == ["Sales Manager", "Finance"]

    manager = await _approver(client, ctx, "Sales Manager Person", [sm])
    finance = await _approver(client, ctx, "Finance Person", [fin])

    # The manager approves their step; Finance is offered the counter and takes it.
    assert (await decide(client, manager, got["card"]["id"], "approve")).status_code == 200
    mine = (await client.get(f"{API}/decisions/{got['card']['id']}", headers=finance)).json()
    assert mine["can_decide"] and mine["step"] == 2
    assert mine["counter_fields"][0]["key"] == "discount_pct_allowed"
    r = await decide(client, finance, got["card"]["id"], "counter",
                     values={"discount_pct_allowed": "6"}, note="6% is as far as we go")
    assert r.status_code == 200, r.text
    assert r.json()["counter_result"]["status"] == "waiting", r.text

    # 6% is not 20%: the chain is worked out again from the matrix, so Finance's own stage
    # falls away and the quote goes back to the Sales Manager, who alone has to see it.
    new_id = r.json()["counter_result"]["decision_id"]
    new = (await client.get(f"{API}/decisions/{new_id}", headers=finance)).json()
    assert new["status"] == "pending"
    assert [x["role"] for x in new["steps"]] == ["Sales Manager"]
    assert new["args"]["discount_pct_allowed"] == "6.00"
    shown = {f["label"]: f["value"] for f in new["facts"]}
    assert Decimal(shown["Discount %"]) == Decimal("6")
    # The margin on the card is the margin 6% off would leave, not the one 20% off left.
    assert Decimal(shown["Margin %"]) > Decimal(priced["active"]["lines"][0]["margin_pct"])


async def test_an_approved_discount_writes_what_was_allowed_into_the_audit_chain(
        client, ctx):  # noqa: F811
    h, tid = ctx["h"], ctx["tid"]
    sm = await _role(client, ctx, "Sales Manager", [
        "quotes.read", "costs.view", "discounts.approve", "decisions.decide"])
    await _matrix(client, ctx, {"when": {"discount_pct_from": "5"}, "approvers": [sm]})
    quote_id, _ = await _discounted_quote(client, ctx)
    got = await _ask(client, h, quote_id, why="Repeat buyer")
    approver = await _approver(client, ctx, "Discount Approver", [sm])
    r = await decide(client, approver, got["card"]["id"], "approve", note="Fine at 20%")
    assert r.status_code == 200, r.text

    async with tenant_session(tid) as s:
        recs = list(await s.scalars(select(AuditRecord).where(
            AuditRecord.kind == "quote.discount.approved")))
        assert len(recs) == 1
        # The numbers stay in the chain's plain skeleton; the rep's words do not (Z10 A).
        data = recs[0].data["discount_approval"]
        assert Decimal(data["discount_pct"]) == Decimal("20")
        assert data["allowed_pct"] is None and Decimal(data["given_away"]) > 0
        assert "why" not in recs[0].data
        assert (await verify(s, tid)).ok

    after = (await client.get(f"{API}/doa/quote/{quote_id}", headers=h)).json()
    assert after["settled"] is True and after["card"]["status"] == "approved"


async def test_a_quote_changed_after_the_card_was_raised_is_asked_again(client, ctx):  # noqa: F811
    h = ctx["h"]
    from tests.test_pricing import Q

    sm = await _role(client, ctx, "Sales Manager")
    await _matrix(client, ctx, {"when": {"discount_pct_from": "5"}, "approvers": [sm]})
    quote_id, priced = await _discounted_quote(client, ctx)
    first = await _ask(client, h, quote_id)

    line = priced["active"]["lines"][0]["id"]
    r = await client.put(f"{Q}/{quote_id}/lines/{line}/ask", headers=h,
                         json={"discount_pct": "25"})
    assert r.status_code == 200, r.text
    stale = (await client.get(f"{API}/doa/quote/{quote_id}", headers=h)).json()
    assert stale["card"]["id"] == first["card"]["id"] and stale["card"]["stale"] is True

    again = await _ask(client, h, quote_id)
    assert again["card"]["id"] != first["card"]["id"]
    assert Decimal(again["discount_pct"]) == Decimal("25")
    gone = (await client.get(f"{API}/decisions/{first['card']['id']}", headers=h)).json()
    assert gone["status"] == "superseded"


async def test_looking_at_a_quotes_discount_raises_nothing(client, ctx):  # noqa: F811
    """The rep can see who will have to sign a discount off before asking anyone."""
    h = ctx["h"]
    sm = await _role(client, ctx, "Sales Manager")
    await _matrix(client, ctx, {"when": {"discount_pct_from": "5"}, "approvers": [sm],
                               "note": "Any discount worth the name"})
    quote_id, _ = await _discounted_quote(client, ctx)
    got = (await client.get(f"{API}/doa/quote/{quote_id}", headers=h)).json()
    assert got["needed"] is True and got["card"] is None
    assert got["stages"][0]["role_name"] == "Sales Manager"
    assert got["stages"][0]["because"] == ["Any discount worth the name"]
    assert got["stages"][0]["people"] == 0  # nobody is in it yet, and it says so


async def test_the_quotes_discount_is_the_whole_quotes_not_its_deepest_line():
    """Z5 A: five lines at 20% off and one at nothing come to the quote's real depth."""
    from app.cpq import doa_agent

    lines = [doa_agent._AsIf(position=1, description="Deep", qty=Decimal(1), unit="no",
                             list_price=Decimal(100), unit_price=Decimal(50),
                             margin_pct=None, price=None, ask=None),
             doa_agent._AsIf(position=2, description="Full price", qty=Decimal(3),
                             unit="no", list_price=Decimal(100), unit_price=Decimal(100),
                             margin_pct=None, price=None, ask=None)]
    got = doa_agent.quote_facts(lines, {"total": "350.00", "margin_pct": None,
                                        "floor_margin_pct": None})
    assert Decimal(got["discount_pct"]) == Decimal("12.5")  # 50 off 400, not 50% off one line
    assert got["given_away"] == "50.00" and got["discounted_line_count"] == 1
