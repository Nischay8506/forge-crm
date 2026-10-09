"""Authenticated encryption for stored credentials (decision E8, safeguard 4).

AES-256-GCM, with keys from CREDENTIAL_KEYS in backend/.env:

    CREDENTIAL_KEYS=k2:<base64 32 bytes>,k1:<base64 32 bytes>

The first key encrypts; every key in the list can decrypt (found by its id), which is
what makes rotation safe. A stored value looks like "v1.<key id>.<nonce>.<ciphertext>".
Each value is bound to where it is stored (tenant, connection, field) through the
GCM associated data, so a value copied into another row or field fails to decrypt,
and any change to a stored value is detected.

Rotating keys:
  1. put a new key first:   uv run python -m app.cli new-credential-key
  2. re-encrypt everything: uv run python -m app.cli rotate-credentials
  3. remove the old key from CREDENTIAL_KEYS once step 2 reports nothing left on it.

Decrypted values are only held long enough to call the ERP. They are never logged,
audited or returned by the API; hint() gives the last few characters for screens.
"""

import base64
import os
import re
import secrets as _random
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings

VERSION = "v1"
KID_RE = re.compile(r"^[a-z0-9]{1,16}$")


class CredentialKeyError(Exception):
    """CREDENTIAL_KEYS is missing or malformed, or a stored value cannot be decrypted.
    Messages never include key material or the stored value."""


@dataclass(frozen=True)
class KeyRing:
    current: str
    keys: dict[str, AESGCM]

    def __repr__(self) -> str:
        return f"<KeyRing current={self.current} ids={sorted(self.keys)}>"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@lru_cache
def _parse(spec: str) -> KeyRing:
    keys: dict[str, AESGCM] = {}
    current = None
    for n, part in enumerate(p.strip() for p in spec.split(",") if p.strip()):
        kid, sep, material = part.partition(":")
        if not sep or not KID_RE.match(kid):
            raise CredentialKeyError(
                f"CREDENTIAL_KEYS entry {n + 1} must look like id:base64key "
                "(id: lowercase letters and digits)")
        try:
            raw = base64.b64decode(material + "=" * (-len(material) % 4), altchars=b"-_")
        except ValueError:
            raw = b""
        if len(raw) != 32:
            raise CredentialKeyError(f"CREDENTIAL_KEYS key {kid!r} must be 32 bytes, base64")
        if kid in keys:
            raise CredentialKeyError(f"CREDENTIAL_KEYS lists key {kid!r} twice")
        keys[kid] = AESGCM(raw)
        current = current or kid
    if current is None:
        raise CredentialKeyError(
            "CREDENTIAL_KEYS is not set. Generate one with "
            "`uv run python -m app.cli new-credential-key` and add it to backend/.env")
    return KeyRing(current=current, keys=keys)


def key_ring() -> KeyRing:
    return _parse(get_settings().credential_keys or "")


def configured() -> bool:
    try:
        key_ring()
    except CredentialKeyError:
        return False
    return True


def new_key_spec(kid: str | None = None) -> str:
    """A fresh key entry for CREDENTIAL_KEYS, e.g. "k20261009:...". """
    kid = kid or "k" + datetime.now(UTC).strftime("%Y%m%d")
    return f"{kid}:{base64.urlsafe_b64encode(os.urandom(32)).decode()}"


def encrypt(plaintext: str, *, context: str) -> str:
    ring = key_ring()
    nonce = _random.token_bytes(12)
    ct = ring.keys[ring.current].encrypt(nonce, plaintext.encode(), context.encode())
    return ".".join((VERSION, ring.current, _b64(nonce), _b64(ct)))


def key_id(token: str) -> str:
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != VERSION:
        raise CredentialKeyError("stored credential is not in a known format")
    return parts[1]


def decrypt(token: str, *, context: str) -> str:
    kid = key_id(token)
    _, _, nonce, ct = token.split(".")
    ring = key_ring()
    if kid not in ring.keys:
        raise CredentialKeyError(
            f"stored credential was encrypted with key {kid!r}, which is no longer in "
            "CREDENTIAL_KEYS")
    try:
        return ring.keys[kid].decrypt(_unb64(nonce), _unb64(ct), context.encode()).decode()
    except (InvalidTag, ValueError) as e:
        raise CredentialKeyError(
            "stored credential failed its integrity check (changed, or moved from "
            "another record)") from e


def needs_rotation(token: str) -> bool:
    return key_id(token) != key_ring().current


def rotate(token: str, *, context: str) -> str:
    """Re-encrypt under the current key (a no-op for values already on it)."""
    if not needs_rotation(token):
        return token
    return encrypt(decrypt(token, context=context), context=context)


def hint(secret: str) -> str:
    """What screens show instead of a credential: its last four characters."""
    return "…" + secret[-4:] if len(secret) > 8 else "…"
