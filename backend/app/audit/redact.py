"""Hiding personal data in the audit trail (decision R4 A).

Personal data is hidden by default for everyone who reads the trail. It is found
two ways:

- known fields: values under keys such as full_name, email, phone or address are
  hidden whole, and the same values are then hidden wherever else they appear in
  what is being shown (so a contact's name in a decision's title is hidden too).
  Each part of a person's name is hidden on its own as well ("Meera" in a note
  about Meera Sharma), except inside a company name on the same page ("Sharma
  Traders" stays readable);
- patterns in any text: email addresses, Indian and international phone numbers,
  an individual's PAN (4th letter P), a GSTIN built on an individual's PAN, and
  Aadhaar numbers (12 digits that pass the Aadhaar check digit).

Company names, cities and ids are not personal data here. A person with the
permission "See personal data in the audit trail" can reveal one replay after
typing a reason; the reveal itself is recorded (app/api/v1/audit.py).

Use: r = Redactor(); r.collect(payload); shown = r.apply(payload); r.hidden.
"""

import re
from dataclasses import dataclass, field
from typing import Any

NAME_KEYS = {"full_name", "contact_name", "first_name", "last_name", "middle_name",
             "person_name", "salutation_name"}
EMAIL_KEYS = {"email", "email_id", "email_address", "personal_email", "emails"}
PHONE_KEYS = {"phone", "mobile", "mobile_no", "phone_no", "whatsapp", "whatsapp_number",
              "landline", "phones", "alt_phone", "alternate_phone"}
ADDRESS_KEYS = {"address", "address_line", "address_line1", "address_line2", "street",
                "address_display", "primary_address"}
OTHER_KEYS = {"aadhaar": "Aadhaar", "aadhaar_number": "Aadhaar", "date_of_birth":
              "date of birth", "dob": "date of birth"}

LABELS: dict[str, str] = {
    **dict.fromkeys(NAME_KEYS, "name"), **dict.fromkeys(EMAIL_KEYS, "email"),
    **dict.fromkeys(PHONE_KEYS, "phone"), **dict.fromkeys(ADDRESS_KEYS, "address"),
    **OTHER_KEYS,
}

# Company names: never hidden, even when they contain a person's name part.
ORG_KEYS = {"account_name", "legal_name", "company_name", "trading_name"}
# Titles that are not part of anyone's name.
_TITLES = {"mr", "mrs", "ms", "miss", "dr", "shri", "smt", "kumari", "sri", "prof"}

# Keys whose values are ids, fingerprints or counters: never personal, never touched.
_SKIP = re.compile(r"(^|_)(id|ids|hash|seq|digest)$|^_nonce$")

_W = r"(?<![\w@.+])"  # not glued to a word, an email or a longer number
_E = r"(?![\w])"
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
GSTIN_INDIVIDUAL = re.compile(_W + r"\d{2}[A-Z]{3}P[A-Z]\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]" + _E)
PAN_INDIVIDUAL = re.compile(_W + r"[A-Z]{3}P[A-Z]\d{4}[A-Z]" + _E)
AADHAAR = re.compile(_W + r"[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}" + _E)
PHONE_IN = re.compile(_W + r"(?:\+?91[ -]?|0)?[6-9]\d{4}[ -]?\d{5}" + _E)
PHONE_INTL = re.compile(r"(?<![\w+])\+\d{1,3}[ -]?\d(?:[ -]?\d){6,13}" + _E)

# Verhoeff check digit tables (Aadhaar's last digit).
_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
      [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
      [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
      [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
      [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
      [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
      [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
      [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


def verhoeff_ok(digits: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def hidden(label: str) -> str:
    return f"[{label} hidden]"


@dataclass
class Redactor:
    terms: set[str] = field(default_factory=set)
    parts: set[str] = field(default_factory=set)
    orgs: set[str] = field(default_factory=set)
    hidden: int = 0
    _res: tuple[Any, Any, Any] | None = None

    def collect(self, value: Any, key: str | None = None) -> None:
        """Remember personal values found under known keys, to hide them elsewhere,
        and company names, to leave them readable."""
        if isinstance(value, dict):
            for k, v in value.items():
                self.collect(v, str(k))
        elif isinstance(value, list):
            for v in value:
                self.collect(v, key)
        elif isinstance(value, str) and key:
            k, v = key.lower(), value.strip()
            if k in ORG_KEYS:
                self.keep(v)
            elif k in LABELS and len(v) >= 3:
                self.terms.add(v)
                if k in NAME_KEYS:
                    words = [w for w in re.findall(r"[^\W\d_][\w'-]*", v)
                             if w.lower().rstrip(".") not in _TITLES]
                    if len(words) > 1:
                        self.terms.add(" ".join(words))  # "Dr Meera Sharma" → "Meera Sharma"
                    self.parts.update(w for w in words if len(w) >= 3)
                self._res = None

    def keep(self, org_name: str | None) -> None:
        """A company name to leave readable, even if it contains part of a person's name."""
        if org_name and len(org_name.strip()) >= 3:
            self.orgs.add(org_name.strip())
            self._res = None

    def _patterns(self) -> tuple[Any, Any, Any]:
        def alt(words: set[str], flags: int = 0) -> re.Pattern[str] | None:
            if not words:
                return None
            body = "|".join(re.escape(t) for t in sorted(words, key=len, reverse=True))
            return re.compile(r"(?<!\w)(?:" + body + r")(?!\w)", flags)
        if self._res is None:
            # Name parts are matched with their capital letter, so "Rose" hides a name
            # but not "rose" in a sentence; whole values match in any case.
            self._res = (alt(self.terms, re.IGNORECASE), alt(self.parts - self.terms),
                         alt(self.orgs, re.IGNORECASE))
        return self._res

    def text(self, s: str) -> str:
        n = 0

        def sub(pattern: re.Pattern[str], label: str, s: str, check=None) -> str:
            nonlocal n

            def repl(m: re.Match[str]) -> str:
                nonlocal n
                if check and not check(m.group(0)):
                    return m.group(0)
                n += 1
                return hidden(label)
            return pattern.sub(repl, s)

        terms, parts, orgs = self._patterns()
        if terms or parts:
            # Company names are set aside first so a person's name part inside one
            # ("Sharma Traders") stays readable; they are put back after.
            kept: list[str] = []
            if orgs:
                def park(m: re.Match[str]) -> str:
                    kept.append(m.group(0))
                    return f"\x00{len(kept) - 1}\x00"
                s = orgs.sub(park, s)
            if terms:
                s = sub(terms, "personal data", s)
            if parts:
                s = sub(parts, "name", s)
            if kept:
                s = re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], s)
        s = sub(EMAIL, "email", s)
        s = sub(GSTIN_INDIVIDUAL, "GSTIN", s)
        s = sub(PAN_INDIVIDUAL, "PAN", s)
        s = sub(AADHAAR, "Aadhaar", s, lambda m: verhoeff_ok(re.sub(r"\D", "", m)))
        s = sub(PHONE_IN, "phone", s)
        s = sub(PHONE_INTL, "phone", s)
        self.hidden += n
        return s

    def apply(self, value: Any, key: str | None = None) -> Any:
        k = key.lower() if key else None
        if k and _SKIP.search(k):
            return value
        if k in LABELS and value not in (None, "", [], {}):
            if isinstance(value, list):
                self.hidden += len(value)
                return [hidden(LABELS[k]) for _ in value]
            self.hidden += 1
            return hidden(LABELS[k])
        if isinstance(value, dict):
            return {kk: self.apply(v, str(kk)) for kk, v in value.items()}
        if isinstance(value, list):
            return [self.apply(v, key) for v in value]
        if isinstance(value, str):
            return self.text(value)
        return value


def redact(value: Any) -> tuple[Any, int]:
    r = Redactor()
    r.collect(value)
    out = r.apply(value)
    return out, r.hidden


# Secrets never reach the trail at all (Step 4 safeguard 1): they are removed before
# a record is stored, not just hidden when it is read.
SECRET_KEYS = re.compile(
    r"(^|_)(password|passwd|passphrase|secret|api_?key|api_?secret|token|access_token|"
    r"refresh_token|authorization|auth|cookie|private_key|credentials?|client_secret)$|_enc$",
    re.IGNORECASE)
SECRET_PATTERNS = (
    re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\btoken\s+[A-Za-z0-9]{6,}:[A-Za-z0-9]{6,}", re.IGNORECASE),  # ERPNext
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWT
    re.compile(r"\b(?:fmcp|fwh)_[A-Za-z0-9_-]{20,}"),  # Forge's own tokens
    re.compile(r"\b(?:sk|gsk|sk-ant|xox[abp])[-_][A-Za-z0-9_-]{16,}"),  # model and chat APIs
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),  # Google
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
)
SECRET_REMOVED = "[secret removed]"


def strip_secrets(value: Any, key: str | None = None) -> Any:
    if key and SECRET_KEYS.search(key) and value not in (None, "", [], {}):
        return SECRET_REMOVED
    if isinstance(value, dict):
        return {k: strip_secrets(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [strip_secrets(v, key) for v in value]
    if isinstance(value, str):
        for p in SECRET_PATTERNS:
            value = p.sub(SECRET_REMOVED, value)
    return value
