"""Encrypting the audit trail's evidence (Step 4 safeguard 1).

Evidence (what an agent was given, prompts, answers, notes, personal data) is
stored encrypted with AES-256-GCM under EVIDENCE_KEYS in backend/.env:

    EVIDENCE_KEYS=e2:<base64 32 bytes>,e1:<base64 32 bytes>

The first key encrypts; every key listed can decrypt, so rotation is: put a new
key first, run `uv run python -m app.cli rotate-evidence`, then remove the old key
once nothing is left on it. Each value is bound to its tenant and record number
(GCM associated data), so a value copied to another row fails to decrypt.

Losing these keys makes the evidence unreadable (the chain still verifies its
skeleton), so keep a copy of them somewhere other than this server.

The chain checks the plaintext's fingerprint, so re-encryption under a new key
does not change any record's hash.
"""

import base64
import json
import secrets as _random
import uuid
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select, text

from app.config import get_settings
from app.core.keyring import KeyRingError, Ring, new_key_spec, parse
from app.models import AuditEvidence

VERSION = "v1"
SETTING = "EVIDENCE_KEYS"


class EvidenceKeyError(KeyRingError):
    """EVIDENCE_KEYS is missing or malformed, or a value cannot be decrypted."""


def ring() -> Ring:
    try:
        r = parse(SETTING, get_settings().evidence_keys)
    except KeyRingError as e:
        raise EvidenceKeyError(str(e)) from e
    if r is None:
        raise EvidenceKeyError(
            "EVIDENCE_KEYS is not set, so the audit trail cannot store its evidence. "
            "Generate a key with `uv run python -m app.cli new-evidence-key` and add it "
            "to backend/.env")
    return r


def configured() -> bool:
    try:
        ring()
    except EvidenceKeyError:
        return False
    return True


def new_evidence_key(kid: str | None = None) -> str:
    return new_key_spec("e", kid)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _aad(tenant_id: uuid.UUID, seq: int) -> bytes:
    return f"audit_evidence:{tenant_id}:{seq}".encode()


def encrypt(tenant_id: uuid.UUID, seq: int, plaintext: str) -> str:
    r = ring()
    nonce = _random.token_bytes(12)
    ct = AESGCM(r.keys[r.current]).encrypt(nonce, plaintext.encode(), _aad(tenant_id, seq))
    return ".".join((VERSION, r.current, _b64(nonce), _b64(ct)))


def key_id(token: str) -> str:
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != VERSION:
        raise EvidenceKeyError("stored evidence is not in a known format")
    return parts[1]


def decrypt(tenant_id: uuid.UUID, seq: int, token: str) -> str:
    kid = key_id(token)
    _, _, nonce, ct = token.split(".")
    r = ring()
    if kid not in r.keys:
        raise EvidenceKeyError(f"evidence of record {seq} was encrypted with key {kid!r}, "
                               "which is no longer in EVIDENCE_KEYS")
    try:
        return AESGCM(r.keys[kid]).decrypt(_unb64(nonce), _unb64(ct),
                                           _aad(tenant_id, seq)).decode()
    except (InvalidTag, ValueError) as e:
        raise EvidenceKeyError(f"the evidence of record {seq} failed its integrity check") from e


def read(ev: AuditEvidence) -> dict[str, Any] | None:
    """The evidence as stored (with its _nonce), or None once it was removed."""
    if ev.data_enc is None:
        return None
    return json.loads(decrypt(ev.tenant_id, ev.seq, ev.data_enc))


async def rotate_all() -> dict[str, Any]:
    """Re-encrypt every tenant's evidence under the current key."""
    from app.db import get_sessionmaker, tenant_session
    from app.models import Tenant

    r = ring()
    async with get_sessionmaker()() as s:
        tenant_ids = list(await s.scalars(select(Tenant.id)))
    rotated = already = 0
    for tid in tenant_ids:
        async with tenant_session(tid) as s:
            await s.execute(text("SELECT set_config('forge.evidence_rekey', 'on', true)"))
            for ev in await s.scalars(select(AuditEvidence).where(
                    AuditEvidence.data_enc.is_not(None))):
                if key_id(ev.data_enc) == r.current:
                    already += 1
                    continue
                ev.data_enc = encrypt(tid, ev.seq, decrypt(tid, ev.seq, ev.data_enc))
                rotated += 1
    return {"current": r.current, "rotated": rotated, "already": already}
