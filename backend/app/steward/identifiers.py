"""Indian business identifiers: GSTIN, PAN, CIN, plus phone/email/domain normalisation.

Validation here is offline (format + check digit + internal consistency). A live
registry lookup can be added behind `GstinLookup` later without changing callers.
"""

import re
from dataclasses import dataclass

import phonenumbers

_GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_CIN_RE = re.compile(r"^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$")

# GST state codes (first two digits of a GSTIN).
GST_STATES: dict[str, str] = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan",
    "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh",
    "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura", "17": "Meghalaya",
    "18": "Assam", "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "25": "Daman and Diu", "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra", "28": "Andhra Pradesh (old)", "29": "Karnataka", "30": "Goa",
    "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh",
    "38": "Ladakh", "97": "Other Territory", "99": "Centre Jurisdiction",
}

# PAN 4th character: holder type.
PAN_HOLDER_TYPES = {
    "C": "Company", "P": "Person", "H": "HUF", "F": "Firm", "A": "Association of Persons",
    "T": "Trust", "B": "Body of Individuals", "L": "Local Authority",
    "J": "Artificial Juridical Person", "G": "Government",
}

# Email domains that say nothing about which company a person works for.
FREE_EMAIL_DOMAINS = frozenset({
    "gmail.com", "yahoo.com", "yahoo.co.in", "rediffmail.com", "hotmail.com", "outlook.com",
    "live.com", "icloud.com", "protonmail.com", "aol.com", "zoho.com", "ymail.com",
    "msn.com", "mail.com",
})


@dataclass(frozen=True)
class IdCheck:
    value: str | None  # normalised value, or None if empty
    valid: bool
    problems: tuple[str, ...] = ()


def _clean(value: str | None) -> str:
    return re.sub(r"[\s\-./]", "", (value or "")).upper()


def gstin_check_digit(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        product = _GSTIN_CHARS.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _GSTIN_CHARS[(36 - total % 36) % 36]


def check_gstin(value: str | None) -> IdCheck:
    v = _clean(value)
    if not v:
        return IdCheck(None, True)
    problems = []
    if not _GSTIN_RE.match(v):
        return IdCheck(v, False, ("GSTIN format is invalid (expected 15 characters like "
                                  "27AAPFU0939F1ZV)",))
    if v[:2] not in GST_STATES:
        problems.append(f"Unknown GST state code {v[:2]}")
    if gstin_check_digit(v[:14]) != v[14]:
        problems.append("GSTIN check digit does not match (likely a typo)")
    return IdCheck(v, not problems, tuple(problems))


def pan_from_gstin(gstin: str | None) -> str | None:
    v = _clean(gstin)
    return v[2:12] if len(v) == 15 else None


def gst_state(gstin: str | None) -> str | None:
    v = _clean(gstin)
    return GST_STATES.get(v[:2]) if len(v) == 15 else None


def check_pan(value: str | None) -> IdCheck:
    v = _clean(value)
    if not v:
        return IdCheck(None, True)
    if not _PAN_RE.match(v):
        return IdCheck(v, False, ("PAN format is invalid (expected like AAPFU0939F)",))
    if v[3] not in PAN_HOLDER_TYPES:
        return IdCheck(v, False, (f"PAN holder type '{v[3]}' is not a known type",))
    return IdCheck(v, True)


def check_cin(value: str | None) -> IdCheck:
    v = _clean(value)
    if not v:
        return IdCheck(None, True)
    if not _CIN_RE.match(v):
        return IdCheck(v, False, ("CIN format is invalid (expected 21 characters like "
                                  "U29100MH2005PTC123456)",))
    return IdCheck(v, True)


def normalize_phone(value: str | None, region: str = "IN") -> str | None:
    """E.164 (+919876543210) when parseable, else None."""
    if not value or not value.strip():
        return None
    try:
        num = phonenumbers.parse(value, region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(num):
        return None
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


def normalize_email(value: str | None) -> str | None:
    v = (value or "").strip().lower()
    return v if re.match(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", v) else None


def normalize_domain(value: str | None) -> str | None:
    """Accepts a website or an email; returns the bare company domain, or None for free mail."""
    v = (value or "").strip().lower()
    if not v:
        return None
    if "@" in v:
        v = v.split("@", 1)[1]
    v = re.sub(r"^[a-z]+://", "", v).split("/", 1)[0].split(":", 1)[0]
    v = v.removeprefix("www.")
    if "." not in v or v in FREE_EMAIL_DOMAINS:
        return None
    return v


def normalize_pincode(value: str | None) -> str | None:
    v = re.sub(r"\s", "", value or "")
    return v if re.fullmatch(r"[1-9][0-9]{5}", v) else None
