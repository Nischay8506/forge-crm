"""The synthetic enquiry set (decision N6 A) read by the rules alone: the checks that do
not need the AI or a database. The full run is `uv run python -m evals.enquiries`."""

from evals.enquiries import generate, run_rules, summarise


def test_the_synthetic_set_reads_by_rules_without_losing_an_enquiry():
    cases = generate(12)
    assert len(cases) == 200
    s = summarise(run_rules(cases))
    a = s["groups"]["all"]
    assert a["enquiry_found"] == a["enquiries"]  # every enquiry is kept as one
    assert s["bom40"]["lines_read"] == 40 and s["bom40"]["field_accuracy"] >= 90
    assert a["seconds_max"] < 60_000
