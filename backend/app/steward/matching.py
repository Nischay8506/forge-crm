"""Pure matching logic (no database): compare two records and decide a band.

Stages, as F12.1 asks: deterministic keys (GSTIN/PAN, CIN, domain, phone) ->
fuzzy + phonetic name/address -> (optional) embedding similarity.

Bands (decision S3, default "a" until confirmed):
  auto_merge  a hard ID matches and nothing contradicts it
  review      looks like the same company/person; a person decides
  distinct    not a duplicate (or a hard ID contradicts it)
Embedding similarity can only ever lift a pair to "review", never to auto-merge.
Every decision carries its reasons so a reviewer can see why.
"""

import re
from dataclasses import dataclass, field
from typing import Any

import jellyfish
from rapidfuzz import fuzz

from app.steward.identifiers import check_gstin, pan_from_gstin
from app.steward.normalize import (
    distinctive_name,
    indic_fold,
    name_tokens,
    normalize_address,
    normalize_name,
)

AUTO_MERGE, REVIEW, DISTINCT = "auto_merge", "review", "distinct"

# Thresholds in one place so they can be tuned against the duplicate test set.
NAME_AUTO_WITH_ID = 85  # same PAN/CIN/GSTIN and names at least this similar -> auto-merge
NAME_AUTO_WITH_DOMAIN = 95  # same company email domain and near-identical name -> auto-merge
NAME_REVIEW = 80
NAME_REVIEW_OTHER_CITY = 95  # names alone, different cities: only near-identical names
NAME_REVIEW_SAME_PINCODE = 60
NAME_ONLY_AUTO = 92  # only used when name-only auto-merge is switched on (decision S3 "b")
EMBEDDING_REVIEW = 0.90
# Leading words that are honorifics rather than the brand ("Shree Ganesh" ~ "Ganesh").
HONORIFICS = frozenset({"sri", "shree", "shri", "sree", "om", "jai", "new", "the", "m", "s"})
CITY_ALIASES = {
    "bangalore": "bengaluru", "bombay": "mumbai", "madras": "chennai", "calcutta": "kolkata",
    "poona": "pune", "gurgaon": "gurugram", "baroda": "vadodara", "mysore": "mysuru",
    "cochin": "kochi", "trivandrum": "thiruvananthapuram", "vizag": "visakhapatnam",
}
GENERIC_MAILBOXES = frozenset({
    "info", "sales", "purchase", "purchases", "accounts", "admin", "contact", "enquiry",
    "enquiries", "office", "hr", "support", "marketing", "procurement", "mail", "export",
})


@dataclass
class AccountRec:
    id: Any
    kind: str
    name: str
    gstin: str | None = None
    pan: str | None = None
    cin: str | None = None
    domain: str | None = None
    phone: str | None = None
    pincode: str | None = None
    city: str | None = None
    address: str | None = None
    parent_id: Any = None

    @property
    def gstin_valid(self) -> bool:
        return bool(self.gstin) and check_gstin(self.gstin).valid

    @property
    def effective_pan(self) -> str | None:
        """PAN from the record, or from its GSTIN when that GSTIN passes its check digit.
        An invalid GSTIN (likely a typo) is never used to prove or rule out a match."""
        return self.pan or (pan_from_gstin(self.gstin) if self.gstin_valid else None)


@dataclass
class ContactRec:
    id: Any
    full_name: str
    email: str | None = None
    phone: str | None = None
    account_id: Any = None


@dataclass
class MatchResult:
    band: str
    score: float
    reasons: list[dict[str, Any]] = field(default_factory=list)

    def add(self, signal: str, detail: str, effect: str) -> None:
        self.reasons.append({"signal": signal, "detail": detail, "effect": effect})


def full_name_similarity(a: str, b: str) -> float:
    """Whole normalised names. Used for automatic decisions."""
    na, nb = normalize_name(a), normalize_name(b)
    return float(fuzz.token_sort_ratio(na, nb)) if na and nb else 0.0


def name_similarity(a: str, b: str) -> float:
    """The better of whole-name and core-name (generic words removed) similarity.
    Generous on purpose: used to decide what a person should look at, never to merge."""
    full = full_name_similarity(a, b)
    if not full:
        return 0.0
    core = fuzz.token_sort_ratio(distinctive_name(a), distinctive_name(b))
    return float(max(full, core))


def _initials(name: str) -> set[str]:
    """Initials with and without the legal suffix: 'Bharat Heavy Electricals Limited'
    -> {'bhel', 'bhe'}."""
    raw = [t for t in re.sub(r"[^a-z0-9 ]+", " ", name.lower()).split()
           if t not in ("the", "m", "s", "and")]
    core = normalize_name(name).split()
    return {"".join(t[0] for t in toks) for toks in (raw, core) if len(toks) >= 2}


def is_acronym(a: str, b: str) -> bool:
    """'BHEL' vs 'Bharat Heavy Electricals Ltd'."""
    for short, long in ((a, b), (b, a)):
        toks = normalize_name(short).split()
        if len(toks) == 1 and len(toks[0]) >= 2 and toks[0] in _initials(long):
            return True
    return False


def _lead_token(name: str) -> str | None:
    toks = [t for t in name_tokens(name) if t not in HONORIFICS]
    return toks[0] if toks else None


def lead_tokens_match(a: str, b: str) -> bool:
    """Indian company names usually lead with the brand word ('Kaveri', 'Vardhman').
    Two names with different brand words are different companies, however similar
    the rest ('... Fasteners Engineers') is."""
    x, y = _lead_token(a), _lead_token(b)
    if not x or not y:
        return False
    if x == y or fuzz.ratio(x, y) >= 85:
        return True
    return jellyfish.metaphone(indic_fold(x)) == jellyfish.metaphone(indic_fold(y))


def _city(c: str | None) -> str | None:
    if not c:
        return None
    c = c.strip().lower()
    return CITY_ALIASES.get(c, c)


def _hamming(a: str, b: str) -> int:
    return sum(x != y for x, y in zip(a, b, strict=True)) if len(a) == len(b) else 99


def _eq(x: str | None, y: str | None) -> bool:
    return bool(x) and bool(y) and x == y


def _conflict(x: str | None, y: str | None) -> bool:
    return bool(x) and bool(y) and x != y


def compare_accounts(
    a: AccountRec, b: AccountRec, embedding_similarity: float | None = None,
    name_only_auto_merge: bool = False,
) -> MatchResult:
    r = MatchResult(DISTINCT, 0.0)
    sim = name_similarity(a.name, b.name)
    full = full_name_similarity(a.name, b.name)
    r.add("name_similarity", f"Name similarity {full:.0f}/100 (core words {sim:.0f}/100)",
          "info")

    if a.kind == "site" and b.kind == "site":
        return _compare_sites(a, b, sim, r)

    pan_a, pan_b = a.effective_pan, b.effective_pan
    same_pan, pan_conflict = _eq(pan_a, pan_b), _conflict(pan_a, pan_b)
    same_cin, cin_conflict = _eq(a.cin, b.cin), _conflict(a.cin, b.cin)
    same_domain, same_phone = _eq(a.domain, b.domain), _eq(a.phone, b.phone)
    same_pincode = _eq(a.pincode, b.pincode)
    city_a, city_b = _city(a.city), _city(b.city)
    other_city = bool(city_a and city_b and city_a != city_b)
    lead = lead_tokens_match(a.name, b.name)
    gstin_typo = (bool(a.gstin and b.gstin) and a.gstin_valid != b.gstin_valid
                  and _hamming(a.gstin, b.gstin) <= 2)

    if pan_conflict:
        r.add("pan_conflict", f"PAN {pan_a} vs {pan_b}: different legal entities", "distinct")
    if cin_conflict:
        r.add("cin_conflict", f"CIN {a.cin} vs {b.cin}: different companies", "distinct")
    if same_pan:
        src = "GSTIN" if (a.gstin and b.gstin and a.gstin == b.gstin) else "PAN"
        r.add("same_pan", f"Same {src} ({pan_a})", "strong")
    if same_cin:
        r.add("same_cin", f"Same CIN ({a.cin})", "strong")
    if gstin_typo:
        r.add("gstin_typo", f"GSTINs {a.gstin} and {b.gstin} differ by one or two characters "
              "and one fails its check digit: probably a typo", "medium")
    if same_domain:
        r.add("same_domain", f"Same company domain ({a.domain})", "medium")
    if same_phone:
        r.add("same_phone", f"Same phone ({a.phone})", "medium")
    if same_pincode:
        r.add("same_pincode", f"Same pincode ({a.pincode})", "weak")
    if other_city:
        r.add("other_city", f"Different cities ({a.city} / {b.city})", "weak-negative")
    if not lead:
        r.add("brand_word_differs", "The leading brand words differ", "weak-negative")
    acronym = is_acronym(a.name, b.name)
    if acronym:
        r.add("acronym", "One name is the initials of the other", "medium")
    if embedding_similarity is not None and embedding_similarity >= EMBEDDING_REVIEW:
        r.add("embedding", f"Meaning similarity {embedding_similarity:.2f}", "medium")

    r.score = min(100.0, round(
        sim * 0.5 + 35 * (same_pan or same_cin) + 10 * same_domain + 10 * same_phone
        + 10 * gstin_typo + 5 * same_pincode + 10 * acronym - 10 * other_city - 10 * (not lead),
        2))

    name_only = lead and (sim >= (NAME_REVIEW_OTHER_CITY if other_city else NAME_REVIEW))
    if pan_conflict or cin_conflict:
        r.band = DISTINCT
    elif (same_pan or same_cin) and full >= NAME_AUTO_WITH_ID:
        r.band = AUTO_MERGE
    elif same_domain and full >= NAME_AUTO_WITH_DOMAIN and not (pan_a and pan_b):
        r.band = AUTO_MERGE
    elif (name_only_auto_merge and not (pan_a and pan_b) and lead and not other_city
          and full >= NAME_ONLY_AUTO and (same_pincode or (city_a and city_a == city_b))):
        r.add("name_only_auto_merge", "Name-only auto-merge is switched on", "strong")
        r.band = AUTO_MERGE
    elif (
        same_pan or same_cin or same_domain or acronym or gstin_typo
        or (same_phone and (lead or sim >= NAME_REVIEW_SAME_PINCODE))
        or name_only
        or (same_pincode and lead and sim >= NAME_REVIEW_SAME_PINCODE)
        or (embedding_similarity is not None and embedding_similarity >= EMBEDDING_REVIEW)
    ):
        r.band = REVIEW
    return r


def _compare_sites(a: AccountRec, b: AccountRec, sim: float, r: MatchResult) -> MatchResult:
    """Two sites are the same place if they share a GST registration or an address."""
    addr = fuzz.token_set_ratio(normalize_address(a.address), normalize_address(b.address))
    same_gstin, gstin_conflict = _eq(a.gstin, b.gstin), _conflict(a.gstin, b.gstin)
    same_pincode = _eq(a.pincode, b.pincode)
    r.add("address_similarity", f"Address similarity {addr:.0f}/100", "info")
    if gstin_conflict:
        r.add("gstin_conflict", f"GSTIN {a.gstin} vs {b.gstin}", "distinct")
    if same_gstin:
        r.add("same_gstin", f"Same GSTIN ({a.gstin})", "strong")
    if same_pincode:
        r.add("same_pincode", f"Same pincode ({a.pincode})", "medium")
    r.score = min(100.0, round(addr * 0.5 + sim * 0.2 + 25 * same_gstin + 10 * same_pincode, 2))
    if gstin_conflict:
        r.band = DISTINCT
    elif same_gstin and same_pincode and addr >= 85:
        r.band = AUTO_MERGE
    elif same_gstin or (same_pincode and addr >= 70):
        r.band = REVIEW
    return r


def compare_contacts(a: ContactRec, b: ContactRec) -> MatchResult:
    r = MatchResult(DISTINCT, 0.0)
    # token_set: "Rajesh Kumar" and "Rajesh Kumar Sharma" are the same person's name
    sim = float(fuzz.token_set_ratio(normalize_name(a.full_name), normalize_name(b.full_name)))
    r.add("name_similarity", f"Name similarity {sim:.0f}/100", "info")
    same_email, same_phone = _eq(a.email, b.email), _eq(a.phone, b.phone)
    same_account = _eq(str(a.account_id or ""), str(b.account_id or ""))
    shared_mailbox = bool(a.email) and a.email.split("@")[0] in GENERIC_MAILBOXES
    if same_email:
        r.add("same_email", f"Same email ({a.email})"
              + (" - a shared mailbox, so not proof" if shared_mailbox else ""), "strong")
    if same_phone:
        r.add("same_phone", f"Same phone ({a.phone})", "medium")
    if same_account:
        r.add("same_account", "Same account", "weak")
    r.score = min(100.0, round(sim * 0.5 + 40 * same_email + 15 * same_phone
                               + 5 * same_account, 2))
    if same_email and not shared_mailbox and sim >= 80:
        r.band = AUTO_MERGE
    elif same_email or (same_phone and sim >= 70) or (same_account and sim >= 90):
        r.band = REVIEW
    return r
