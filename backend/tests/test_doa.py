"""The discount approval rulebook (F3.4; Z1-Z10 A, 2026-10-21).

No database: the matrix and the chain it works out are plain arithmetic, so these run in
milliseconds and say exactly what the rulebook means.
"""

from decimal import Decimal

import pytest

from app.core.errors import ValidationFailed
from app.cpq import doa


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
