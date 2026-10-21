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
from tests.test_agents import API, _user_with_role, ctx  # noqa: F401


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
