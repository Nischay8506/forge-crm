"""Name and address normalisation for matching.

Company names: lower-case, drop punctuation and legal suffixes (Pvt Ltd, LLP...),
expand common abbreviations, then build phonetic keys that tolerate Indian
transliteration variants (Shree/Shri/Sri, Bharat/Bharath, Laxmi/Lakshmi).
"""

import re
import unicodedata

import jellyfish

LEGAL_SUFFIXES = frozenset({
    "pvt", "private", "ltd", "limited", "llp", "inc", "incorporated", "co", "company",
    "corp", "corporation", "plc", "opc", "the", "m/s", "ms", "and", "&",
})

ABBREVIATIONS = {
    "intl": "international", "int'l": "international", "mfg": "manufacturing",
    "mfrs": "manufacturers", "engg": "engineering", "eng": "engineering",
    "inds": "industries", "ind": "industries", "indus": "industries", "ent": "enterprises",
    "entp": "enterprises", "tech": "technologies", "techs": "technologies",
    "sys": "systems", "elec": "electricals", "elect": "electricals", "mech": "mechanical",
    "auto": "automotive", "chem": "chemicals", "pharma": "pharmaceuticals",
    "bros": "brothers", "svcs": "services", "svc": "services", "solns": "solutions",
}

# Words so common in Indian manufacturer names that they say little about identity.
GENERIC_WORDS = frozenset({
    "industries", "enterprises", "engineering", "engineers", "works", "manufacturing",
    "india", "indian", "products", "systems", "solutions", "services", "technologies",
    "international", "group", "trading", "traders", "agencies", "corporation",
})

_ADDRESS_ABBR = {
    "rd": "road", "st": "street", "ngr": "nagar", "nr": "near", "opp": "opposite",
    "indl": "industrial", "ind": "industrial", "estt": "estate", "est": "estate",
    "midc": "midc", "plot": "plot", "no": "", "sec": "sector", "ph": "phase",
}


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def name_tokens(name: str | None) -> list[str]:
    text = _ascii(name or "").lower().replace("&", " and ")
    text = re.sub(r"^\s*m\s*/\s*s\.?\s*", " ", text)  # "M/s." prefix
    text = re.sub(r"\(.*?\)", " ", text)  # "(India)" etc. rarely identifies a company
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    out = []
    for tok in text.split():
        tok = ABBREVIATIONS.get(tok, tok).replace("'", "")
        if tok and tok not in LEGAL_SUFFIXES:
            out.append(tok)
    return out


def normalize_name(name: str | None) -> str:
    return " ".join(name_tokens(name))


def indic_fold(token: str) -> str:
    """Collapse common romanisation variants before phonetic coding."""
    t = token
    for a, b in (("shree", "sri"), ("shri", "sri"), ("sree", "sri"), ("lakshmi", "laxmi"),
                 ("ksh", "x"), ("aa", "a"), ("ee", "i"), ("oo", "u"), ("ph", "f"),
                 ("w", "v"), ("z", "j")):
        t = t.replace(a, b)
    t = re.sub(r"([bcdgjkpt])h", r"\1", t)  # bharath -> barat, dhanraj -> danraj
    t = re.sub(r"(.)\1+", r"\1", t)  # double letters
    return t


def phonetic_keys(name: str | None) -> list[str]:
    """Phonetic code per distinctive token (generic words dropped), used for blocking."""
    keys = []
    for tok in name_tokens(name):
        if tok in GENERIC_WORDS or tok.isdigit() or len(tok) < 2:
            continue
        code = jellyfish.metaphone(indic_fold(tok))
        if code and code not in keys:
            keys.append(code)
    return keys


def distinctive_name(name: str | None) -> str:
    """Normalised name without generic words; falls back to the full name if nothing is left."""
    toks = name_tokens(name)
    core = [t for t in toks if t not in GENERIC_WORDS]
    return " ".join(core or toks)


def normalize_address(address: str | None) -> str:
    text = _ascii(address or "").lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(_ADDRESS_ABBR.get(t, t) for t in text.split() if _ADDRESS_ABBR.get(t, t))
