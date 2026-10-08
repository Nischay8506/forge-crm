"""The audit chain (minimal F12.4, decisions 6A and T6).

One hash chain per tenant: every record stores the hash of the tenant's previous
record, so changing or removing any record breaks every hash after it. The
database refuses UPDATE/DELETE on the table (migration 0004); verify() finds
tampering that bypassed that (for example by someone with owner rights).

Records are appended inside the caller's transaction: if the action rolls back,
so does its audit record. A per-tenant advisory lock keeps the sequence gap-free.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditRecord

GENESIS = "0" * 64


def _plain(data: dict[str, Any] | None) -> dict[str, Any]:
    """JSON-safe copy (UUIDs, dates, Decimals become strings) so the stored JSONB
    and the hashed form are identical."""
    return json.loads(json.dumps(data or {}, default=str))


def _payload(rec: AuditRecord) -> str:
    body = {
        "tenant_id": str(rec.tenant_id), "seq": rec.seq,
        "created_at": rec.created_at.astimezone(UTC).isoformat(timespec="microseconds"),
        "actor": rec.actor, "kind": rec.kind, "agent_key": rec.agent_key,
        "run_id": str(rec.run_id) if rec.run_id else None, "record_type": rec.record_type,
        "record_id": str(rec.record_id) if rec.record_id else None, "data": rec.data,
        "prev_hash": rec.prev_hash,
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(rec: AuditRecord) -> str:
    return hashlib.sha256(_payload(rec).encode()).hexdigest()


async def append(
    session: AsyncSession, tenant_id: uuid.UUID, kind: str, *, actor: str,
    data: dict[str, Any] | None = None, agent_key: str | None = None,
    run_id: uuid.UUID | None = None, record_type: str | None = None,
    record_id: uuid.UUID | None = None,
) -> AuditRecord:
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                          {"k": f"audit:{tenant_id}"})
    last = (await session.execute(
        select(AuditRecord.seq, AuditRecord.hash).where(AuditRecord.tenant_id == tenant_id)
        .order_by(AuditRecord.seq.desc()).limit(1))).first()
    rec = AuditRecord(
        tenant_id=tenant_id, seq=(last.seq + 1) if last else 1,
        created_at=datetime.now(UTC), actor=actor, kind=kind, agent_key=agent_key,
        run_id=run_id, record_type=record_type, record_id=record_id, data=_plain(data),
        prev_hash=last.hash if last else GENESIS)
    rec.hash = compute_hash(rec)
    session.add(rec)
    await session.flush()
    return rec


@dataclass
class ChainCheck:
    ok: bool
    checked: int
    first_bad_seq: int | None = None
    problem: str | None = None


async def verify(session: AsyncSession, tenant_id: uuid.UUID, batch: int = 5000) -> ChainCheck:
    """Walk the tenant's chain from the start and recompute every hash."""
    prev, expected_seq, checked = GENESIS, 1, 0
    while True:
        rows = (await session.scalars(
            select(AuditRecord).where(AuditRecord.tenant_id == tenant_id,
                                      AuditRecord.seq >= expected_seq)
            .order_by(AuditRecord.seq).limit(batch)
            .execution_options(populate_existing=True))).all()
        if not rows:
            return ChainCheck(True, checked)
        for rec in rows:
            if rec.seq != expected_seq:
                return ChainCheck(False, checked, expected_seq,
                                  f"record {expected_seq} is missing")
            if rec.prev_hash != prev:
                return ChainCheck(False, checked, rec.seq,
                                  f"record {rec.seq} does not point at record {rec.seq - 1}")
            if compute_hash(rec) != rec.hash:
                return ChainCheck(False, checked, rec.seq,
                                  f"record {rec.seq} was changed after it was written")
            prev, expected_seq, checked = rec.hash, expected_seq + 1, checked + 1
