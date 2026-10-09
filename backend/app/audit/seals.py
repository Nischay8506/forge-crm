"""Hourly signed seals of each tenant's audit chain (decision R3 A).

Every hour Forge signs the newest record's hash with a key kept outside the
database, in AUDIT_SEAL_KEYS in backend/.env:

    AUDIT_SEAL_KEYS=s2:<base64 32 bytes>,s1:<base64 32 bytes>

The first key signs; every key in the list can check (found by its id), so a key
can be rotated by putting a new one first and keeping the old one after it for as
long as its seals should still be checkable. Someone who edits the database and
recomputes every hash still cannot make the seals match without this key.

A seal is HMAC-SHA256 over one line, `message()` below, which includes the
previous seal's signature: the seals form their own chain, so a removed, changed
or reordered seal is caught too (Step 4 safeguard 2). `export()` gives a seal as
plain data, and every store in ANCHORS receives each new seal: that is where the
planned outside write-once copy (R3 B, before the first paying customer) plugs in.

    uv run python -m app.cli new-seal-key     # prints a key for AUDIT_SEAL_KEYS
"""

import hashlib
import hmac
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.keyring import KeyRingError, Ring, new_key_spec, parse
from app.models import AuditRecord, AuditSeal

log = logging.getLogger(__name__)

VERSION = "forge-seal-v1"
SETTING = "AUDIT_SEAL_KEYS"


def ring() -> Ring | None:
    """None when no key is set (seals are off); KeyRingError when the setting is wrong."""
    return parse(SETTING, get_settings().audit_seal_keys)


def state() -> Literal["on", "off", "misconfigured"]:
    try:
        return "on" if ring() else "off"
    except KeyRingError:
        return "misconfigured"


def new_seal_key(kid: str | None = None) -> str:
    return new_key_spec("s", kid)


def message(tenant_id: uuid.UUID, seq: int, record_hash: str, sealed_at: datetime,
            key_id: str, prev_signature: str | None) -> bytes:
    at = sealed_at.astimezone(UTC).isoformat(timespec="microseconds")
    return "|".join((VERSION, str(tenant_id), str(seq), record_hash, at, key_id,
                     prev_signature or "")).encode()


def _sign(key: bytes, msg: bytes) -> str:
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def check(tenant_id: uuid.UUID, seal: AuditSeal) -> Literal["ok", "bad", "unknown_key"]:
    try:
        r = ring()
    except KeyRingError:
        r = None
    key = r.keys.get(seal.key_id) if r else None
    if key is None:
        return "unknown_key"
    expected = _sign(key, message(tenant_id, seal.seq, seal.hash, seal.sealed_at, seal.key_id,
                                  seal.prev_signature))
    return "ok" if hmac.compare_digest(expected, seal.signature) else "bad"


def export(seal: AuditSeal) -> dict[str, Any]:
    """A seal as plain data: everything needed to check it again elsewhere."""
    return {"version": VERSION, "tenant_id": str(seal.tenant_id), "seq": seal.seq,
            "hash": seal.hash,
            "sealed_at": seal.sealed_at.astimezone(UTC).isoformat(timespec="microseconds"),
            "key_id": seal.key_id, "prev_signature": seal.prev_signature,
            "signature": seal.signature}


class SealAnchor(Protocol):
    """An outside store that keeps a copy of every seal (R3 B, not built yet)."""

    name: str

    async def publish(self, seal: dict[str, Any]) -> None: ...


ANCHORS: list[SealAnchor] = []


async def seal_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> AuditSeal | None:
    """Seal the tenant's newest record, unless it is already sealed or seals are off."""
    r = ring()
    if r is None:
        return None
    # The same lock as appending: the head cannot move while it is being sealed.
    from app.audit.chain import lock

    await lock(session, tenant_id)
    head = (await session.execute(
        select(AuditRecord.seq, AuditRecord.hash).where(AuditRecord.tenant_id == tenant_id)
        .order_by(AuditRecord.seq.desc()).limit(1))).first()
    if head is None:
        return None
    last = await session.scalar(select(AuditSeal).where(AuditSeal.tenant_id == tenant_id)
                                .order_by(AuditSeal.seq.desc()).limit(1))
    if last is not None and last.seq >= head.seq:
        return None
    at = datetime.now(UTC)
    prev = last.signature if last else None
    seal = AuditSeal(tenant_id=tenant_id, seq=head.seq, hash=head.hash, sealed_at=at,
                     key_id=r.current, prev_signature=prev,
                     signature=_sign(r.keys[r.current], message(
                         tenant_id, head.seq, head.hash, at, r.current, prev)))
    session.add(seal)
    await session.flush()
    for anchor in ANCHORS:
        try:
            await anchor.publish(export(seal))
        except Exception:  # the seal stands; R3 B adds retries and an alert
            log.exception("copying seal %s/%s to %s failed", tenant_id, seal.seq, anchor.name)
    return seal


async def seal_all() -> dict[str, int]:
    """The hourly job: one seal per tenant whose chain moved since its last seal."""
    from app.db import get_sessionmaker, tenant_session
    from app.models import Tenant

    if state() != "on":
        if state() == "misconfigured":
            log.error("AUDIT_SEAL_KEYS is malformed; the audit chain is not being sealed")
        return {"sealed": 0, "tenants": 0}
    async with get_sessionmaker()() as s:
        tenant_ids = list(await s.scalars(select(Tenant.id)))
    sealed = 0
    for tid in tenant_ids:
        try:
            async with tenant_session(tid) as s:
                sealed += 1 if await seal_tenant(s, tid) else 0
        except Exception:  # one tenant's failure must not stop the others
            log.exception("sealing the audit chain of tenant %s failed", tid)
    return {"sealed": sealed, "tenants": len(tenant_ids)}
