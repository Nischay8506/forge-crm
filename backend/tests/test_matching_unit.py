"""Pure matching rules: no database needed."""

from app.steward.identifiers import check_cin, check_gstin, check_pan, gstin_check_digit
from app.steward.matching import (
    AUTO_MERGE,
    DISTINCT,
    REVIEW,
    AccountRec,
    ContactRec,
    compare_accounts,
    compare_contacts,
)


def gstin(state: str, pan: str, n: str = "1") -> str:
    first = f"{state}{pan}{n}Z"
    return first + gstin_check_digit(first)


def test_gstin_checks():
    assert check_gstin("27AAPFU0939F1ZV").valid
    assert not check_gstin("27AAPFU0939F1ZX").valid  # wrong check digit
    assert not check_gstin("27AAPFU0939").valid
    assert check_gstin(" 27aapfu0939f1zv ").value == "27AAPFU0939F1ZV"
    assert check_gstin(None).valid and check_gstin(None).value is None
    assert check_pan("AAPFU0939F").valid and not check_pan("AAPXU0939F").valid
    assert check_cin("U29100MH2005PTC123456").valid and not check_cin("U2910").valid


def test_same_gstin_and_close_name_auto_merges():
    g = gstin("27", "AAACS1234C")
    a = AccountRec(1, "entity", "Shree Ganesh Engineering Works Pvt Ltd", gstin=g)
    b = AccountRec(2, "entity", "Shri Ganesh Engg. Works", gstin=g)
    assert compare_accounts(a, b).band == AUTO_MERGE


def test_same_pan_other_state_is_same_entity():
    a = AccountRec(1, "entity", "Kirloskar Pumps Ltd", gstin=gstin("27", "AAACK1111K"))
    b = AccountRec(2, "entity", "Kirloskar Pumps Limited", gstin=gstin("29", "AAACK1111K"))
    assert compare_accounts(a, b).band == AUTO_MERGE


def test_different_pan_never_merges_even_with_identical_names():
    a = AccountRec(1, "entity", "Precision Engineering Works", gstin=gstin("27", "AAACP1111P"))
    b = AccountRec(2, "entity", "Precision Engineering Works", gstin=gstin("33", "AAACP2222P"))
    res = compare_accounts(a, b)
    assert res.band == DISTINCT
    assert any(x["signal"] == "pan_conflict" for x in res.reasons)


def test_close_names_without_ids_go_to_review():
    a = AccountRec(1, "entity", "Bharat Forge Limited", city="Pune")
    b = AccountRec(2, "entity", "Bharath Forge Ltd", city="Pune")
    assert compare_accounts(a, b).band == REVIEW


def test_same_id_but_very_different_name_goes_to_review():
    g = gstin("24", "AAACZ5555Z")
    a = AccountRec(1, "entity", "Zenith Polymers", gstin=g)
    b = AccountRec(2, "entity", "ZP Industrial Solutions", gstin=g)  # maybe a trade name
    assert compare_accounts(a, b).band == REVIEW


def test_acronym_goes_to_review():
    a = AccountRec(1, "entity", "BHEL")
    b = AccountRec(2, "entity", "Bharat Heavy Electricals Limited")
    assert compare_accounts(a, b).band == REVIEW


def test_unrelated_companies_are_distinct():
    a = AccountRec(1, "entity", "Tata Steel", pincode="831001")
    b = AccountRec(2, "entity", "Tata Motors", pincode="411018")
    assert compare_accounts(a, b).band == DISTINCT


def test_sites_match_on_address_not_name():
    g = gstin("27", "AAACK1111K")
    a = AccountRec(1, "site", "Kirloskar Kagal Plant", gstin=g, pincode="416216",
                   address="Plot 5, Kagal Industrial Estate, Kolhapur")
    b = AccountRec(2, "site", "KPL Kagal", gstin=g, pincode="416216",
                   address="Plot No 5 Kagal Indl Estate Kolhapur")
    assert compare_accounts(a, b).band == AUTO_MERGE


def test_contacts():
    a = ContactRec(1, "Rajesh Kumar", email="rajesh.kumar@acme.in")
    b = ContactRec(2, "Rajesh Kumar Sharma", email="rajesh.kumar@acme.in")
    assert compare_contacts(a, b).band == AUTO_MERGE
    shared = ContactRec(3, "Purchase Dept", email="purchase@acme.in")
    other = ContactRec(4, "Purchase Department", email="purchase@acme.in")
    assert compare_contacts(shared, other).band == REVIEW
