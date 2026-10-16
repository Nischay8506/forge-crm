"""Key rings kept outside the database, in backend/.env, as "id:base64key,...".

The first key is current (it encrypts or signs); every key listed can still
decrypt or check, which is what makes rotation safe (Step 4 safeguard 2: old keys
stay usable for checking). Used by the audit seals (AUDIT_SEAL_KEYS) and the
audit evidence (EVIDENCE_KEYS); stored ERP credentials have their own copy of
this in app/core/secrets.py.
"""

import base64
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime

KID_RE = re.compile(r"^[a-z0-9]{1,16}$")


class KeyRingError(Exception):
    """A key setting is missing or malformed. Messages never include key material."""


@dataclass(frozen=True)
class Ring:
    setting: str
    current: str
    keys: dict[str, bytes]

    def __repr__(self) -> str:
        return f"<Ring {self.setting} current={self.current} ids={sorted(self.keys)}>"


def parse(setting: str, spec: str | None) -> Ring | None:
    """None when the setting is empty; KeyRingError when it is malformed."""
    keys: dict[str, bytes] = {}
    current = None
    for n, part in enumerate(p.strip() for p in (spec or "").split(",") if p.strip()):
        kid, sep, material = part.partition(":")
        if not sep or not KID_RE.match(kid):
            raise KeyRingError(f"{setting} entry {n + 1} must look like id:base64key "
                               "(id: lowercase letters and digits)")
        try:
            raw = base64.b64decode(material + "=" * (-len(material) % 4), altchars=b"-_")
        except ValueError:
            raw = b""
        if len(raw) != 32:
            raise KeyRingError(f"{setting} key {kid!r} must be 32 bytes, base64")
        if kid in keys:
            raise KeyRingError(f"{setting} lists key {kid!r} twice")
        keys[kid] = raw
        current = current or kid
    return Ring(setting, current, keys) if current else None


def new_key_spec(prefix: str, kid: str | None = None) -> str:
    kid = kid or prefix + datetime.now(UTC).strftime("%Y%m%d")
    if not KID_RE.match(kid):
        raise KeyRingError("key id must be lowercase letters and digits, at most 16")
    return f"{kid}:{base64.urlsafe_b64encode(os.urandom(32)).decode()}"


# What each key setting is for, and the command that makes one, so a server can say at
# startup what it cannot do yet (the fresh-install trap: backend/.env copied from
# .env.example has all three empty, so the app reads everything and saves nothing).
NEEDED = {
    "EVIDENCE_KEYS": ("the audit trail's details, so saving anything with customer "
                      "details in it fails", "new-evidence-key"),
    "AUDIT_SEAL_KEYS": ("the hourly signatures over the audit trail", "new-seal-key"),
    "CREDENTIAL_KEYS": ("stored ERPNext and mailbox sign-ins", "new-credential-key"),
}


def unusable() -> list[str]:
    """The key settings that are empty or malformed, with what each one costs."""
    from app.config import get_settings

    s = get_settings()
    out = []
    for setting, (what, command) in NEEDED.items():
        spec = getattr(s, setting.lower(), None)
        try:
            if parse(setting, spec) is not None:
                continue
            why = "is not set"
        except KeyRingError as e:
            why = str(e)
        out.append(f"{setting} {why}, so {what}. Make one with "
                   f"`uv run python -m app.cli {command}` and put it in backend/.env")
    return out
