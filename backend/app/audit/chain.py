"""The audit chain (F12.4, decisions 6A, T6 and R1).

One hash chain per tenant: every record stores the hash of the tenant's previous
record, so changing or removing any record breaks every hash after it. The
database refuses UPDATE/DELETE on the table (migrations 0004 and 0006); verify()
finds tampering that bypassed that (for example by someone with owner rights).

Records come in two formats:

- format 1 (Step 2 records): the hash covers everything, including `data`.
- format 2 (R1, from Step 4): `data` keeps only a short skeleton (ids, kinds,
  verdicts, counts); everything else (inputs, prompts, notes, personal data) goes
  to `audit_evidence`, and the hash covers the evidence's fingerprint instead of
  the evidence itself. Evidence can then be removed (retention R5, erasure R6)
  without breaking the chain, but not changed: a changed detail no longer matches
  its fingerprint. Each evidence row carries a random `_nonce`, so the fingerprint
  left behind after removal does not let anyone confirm a guess of what was there.

Step 4 safeguard 1: secrets (passwords, keys, tokens) are removed from everything
before it is stored, evidence is encrypted (app/audit/evidence.py), and a
skeleton value that looks like personal data is moved into the evidence.

Records are appended inside the caller's transaction: if the action rolls back,
so does its audit record. A per-tenant advisory lock keeps the sequence gap-free.
"""

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import evidence as ev_crypto
from app.audit.redact import Redactor, strip_secrets
from app.models import AuditEvidence, AuditRecord, AuditSeal

GENESIS = "0" * 64
FORMAT = 2

# Keys a format-2 record keeps in its skeleton. They say what happened and to
# which ids, never who a person is or what anyone wrote. Keys ending in _id/_ids
# are kept too, except those that can hold a name (an ERP's customer id often is
# the customer's name).
SKELETON_KEYS = frozenset({
    "tool", "version", "outcome", "level", "rule_ids", "approved_by", "agent_version",
    "automatic", "repointed", "attempt", "via_agent", "kind", "from", "to", "scope",
    "entity", "entities", "status", "result_ids", "permissions", "reconcile",
    "ok", "enabled", "allow_write", "adopted", "count", "list", "score", "problems",
    "model", "provider", "tier", "task", "input_tokens", "output_tokens", "removed",
    "classes", "periods", "profile", "channel", "ai_generated", "stores", "kept",
    "requested_by", "direction",
    # F3.2: the fingerprint of each costed quote line, so a cost can be shown years
    # later to be the one Forge worked out, even once the details have been trimmed.
    "cost_seals",
    # F3.3: the same for each line's price, the number the customer is given.
    "price_seals",
    # Step 5 (F12.3): a decision's place in its chain, timing and card fingerprint.
    "approval_steps", "sla_hours", "recommended", "counter_of", "step", "steps",
    "on_behalf_of", "card_hash", "decision_ms", "fields", "option",
})
NOT_SKELETON = frozenset({"external_id", "erp_customer", "idempotency_key"})

# Evidence classes (R5): what customers were told and money is kept 8 years by
# default, everything else 3 years. Callers can name the class; these kinds are
# always customer/money.
CUSTOMER_MONEY = "customer_money"
OTHER = "other"
CUSTOMER_MONEY_KINDS = ("erp.write.", "message.", "contact.erased", "consent.")


def _plain(data: dict[str, Any] | None) -> dict[str, Any]:
    """JSON-safe copy (UUIDs, dates, Decimals become strings) so the stored JSONB
    and the hashed form are identical."""
    return json.loads(json.dumps(data or {}, default=str))


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def in_skeleton(key: str) -> bool:
    if key in NOT_SKELETON:
        return False
    return key in SKELETON_KEYS or key.endswith("_id") or key.endswith("_ids")


def _looks_personal(value: Any) -> bool:
    r = Redactor()
    r.apply(value)
    return r.hidden > 0


def split(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(skeleton, evidence) for a record's data, both already JSON-safe. A skeleton
    key whose value looks like personal data goes to the evidence instead."""
    skeleton, evidence = {}, {}
    for k, v in data.items():
        (skeleton if in_skeleton(k) and not _looks_personal(v) else evidence)[k] = v
    return skeleton, evidence


async def lock(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """The tenant's chain lock, held to the end of the transaction."""
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                          {"k": f"audit:{tenant_id}"})


def _payload(rec: AuditRecord) -> str:
    body = {
        "tenant_id": str(rec.tenant_id), "seq": rec.seq,
        "created_at": rec.created_at.astimezone(UTC).isoformat(timespec="microseconds"),
        "actor": rec.actor, "kind": rec.kind, "agent_key": rec.agent_key,
        "run_id": str(rec.run_id) if rec.run_id else None, "record_type": rec.record_type,
        "record_id": str(rec.record_id) if rec.record_id else None, "data": rec.data,
        "prev_hash": rec.prev_hash,
    }
    if (rec.format or 1) >= 2:
        body["format"] = rec.format
        body["evidence_digest"] = rec.evidence_digest
    return canonical(body)


def compute_hash(rec: AuditRecord) -> str:
    return hashlib.sha256(_payload(rec).encode()).hexdigest()


def evidence_class_for(kind: str) -> str:
    return CUSTOMER_MONEY if kind.startswith(CUSTOMER_MONEY_KINDS) else OTHER


async def append(
    session: AsyncSession, tenant_id: uuid.UUID, kind: str, *, actor: str,
    data: dict[str, Any] | None = None, agent_key: str | None = None,
    run_id: uuid.UUID | None = None, record_type: str | None = None,
    record_id: uuid.UUID | None = None, evidence_class: str | None = None,
    account_id: uuid.UUID | None = None, contact_id: uuid.UUID | None = None,
) -> AuditRecord:
    """Append one record. `evidence_class`, `account_id` and `contact_id` only
    label the evidence (for retention, legal holds and erasure); the subject
    record fills in the account or contact when not given."""
    await lock(session, tenant_id)
    last = (await session.execute(
        select(AuditRecord.seq, AuditRecord.hash).where(AuditRecord.tenant_id == tenant_id)
        .order_by(AuditRecord.seq.desc()).limit(1))).first()
    skeleton, evidence = split(strip_secrets(_plain(data)))
    plaintext = None
    if evidence:
        evidence["_nonce"] = secrets.token_hex(16)
        plaintext = canonical(evidence)
    seq = (last.seq + 1) if last else 1
    rec = AuditRecord(
        tenant_id=tenant_id, seq=seq,
        created_at=datetime.now(UTC), actor=actor, kind=kind, agent_key=agent_key,
        run_id=run_id, record_type=record_type, record_id=record_id, data=skeleton,
        prev_hash=last.hash if last else GENESIS, format=FORMAT,
        evidence_digest=hashlib.sha256(plaintext.encode()).hexdigest() if plaintext else None)
    rec.hash = compute_hash(rec)
    session.add(rec)
    if evidence:
        if record_type == "account" and account_id is None:
            account_id = record_id
        if record_type == "contact" and contact_id is None:
            contact_id = record_id
        session.add(AuditEvidence(
            tenant_id=tenant_id, seq=rec.seq,
            retention_class=evidence_class or evidence_class_for(kind),
            account_id=account_id, contact_id=contact_id,
            data_enc=ev_crypto.encrypt(tenant_id, seq, plaintext),
            digest=rec.evidence_digest, created_at=rec.created_at))
    await session.flush()
    return rec


def evidence_problem(rec: AuditRecord, ev: AuditEvidence | None) -> str | None:
    """Why a record's evidence does not match its fingerprint, if it doesn't."""
    if (rec.format or 1) < 2 or rec.evidence_digest is None:
        return None
    if ev is None:
        return f"the details of record {rec.seq} are missing"
    changed = f"the details of record {rec.seq} were changed after they were written"
    if ev.digest != rec.evidence_digest:
        return changed
    if ev.data_enc is None:
        if ev.removed_at is None:
            return f"the details of record {rec.seq} were removed without a record of why"
        return None
    try:
        plaintext = ev_crypto.decrypt(ev.tenant_id, ev.seq, ev.data_enc)
    except ev_crypto.EvidenceKeyError as e:
        return changed if "integrity" in str(e) else f"the details of record {rec.seq} " \
            f"cannot be checked: {e}"
    if hashlib.sha256(plaintext.encode()).hexdigest() != rec.evidence_digest:
        return changed
    return None


async def evidence_for(session: AsyncSession, tenant_id: uuid.UUID,
                       seqs: list[int]) -> dict[int, AuditEvidence]:
    if not seqs:
        return {}
    rows = await session.scalars(
        select(AuditEvidence).where(AuditEvidence.tenant_id == tenant_id,
                                    AuditEvidence.seq.in_(seqs))
        .execution_options(populate_existing=True))
    return {e.seq: e for e in rows}


@dataclass
class ChainCheck:
    ok: bool
    checked: int
    first_bad_seq: int | None = None
    problem: str | None = None
    starts_at: int = 1  # above 1 once retention has trimmed the oldest records (R5)
    details_removed: int = 0  # evidence removed by retention or erasure, as allowed
    seals_checked: int = 0
    sealed_upto: int | None = None  # the newest record a valid seal covers (R3)
    sealed_at: datetime | None = None
    unknown_seal_keys: list[str] = field(default_factory=list)
    warning: str | None = None  # e.g. sealing stopped; not proof of tampering


async def verify(session: AsyncSession, tenant_id: uuid.UUID, batch: int = 5000) -> ChainCheck:
    """Walk the tenant's chain from its start: recompute every hash, check every
    record's details against their fingerprint and every seal against its key."""
    from app.audit import seals as seal_mod  # seals imports this module

    seal_list = list(await session.scalars(
        select(AuditSeal).where(AuditSeal.tenant_id == tenant_id).order_by(AuditSeal.seq)
        .execution_options(populate_existing=True)))
    seal_rows = {s.seq: s for s in seal_list}
    result = ChainCheck(True, 0)

    def fail(seq: int, problem: str) -> ChainCheck:
        result.ok, result.first_bad_seq, result.problem = False, seq, problem
        return result

    # The seals form their own chain: each one names the signature before it.
    prev_seal: AuditSeal | None = None
    for seal in seal_list:
        if seal.prev_signature != (prev_seal.signature if prev_seal else None):
            after = f"after the seal on record {prev_seal.seq} " if prev_seal else ""
            return fail(seal.seq, f"a seal {after}before the seal on record {seal.seq} is "
                                  "missing, or the seals were reordered")
        if prev_seal and seal.sealed_at <= prev_seal.sealed_at:
            return fail(seal.seq, f"the seals on records {prev_seal.seq} and {seal.seq} are "
                                  "out of order")
        prev_seal = seal

    def check_seal(seal: AuditSeal, record_hash: str | None) -> str | None:
        verdict = seal_mod.check(tenant_id, seal)
        if verdict == "unknown_key":
            if seal.key_id not in result.unknown_seal_keys:
                result.unknown_seal_keys.append(seal.key_id)
        elif verdict != "ok":
            return f"the seal on record {seal.seq} is not genuine"
        if record_hash is not None and seal.hash != record_hash:
            return (f"record {seal.seq} no longer matches its seal from "
                    f"{seal.sealed_at:%d %b %Y %H:%M} UTC")
        result.seals_checked += 1
        if verdict == "ok":
            result.sealed_upto, result.sealed_at = seal.seq, seal.sealed_at
        return None

    first = await session.scalar(select(AuditRecord.seq).where(AuditRecord.tenant_id == tenant_id)
                                 .order_by(AuditRecord.seq).limit(1))
    prev, expected_seq = GENESIS, 1
    if first is not None and first > 1:
        # Trimmed by retention: the chain must start right after a sealed record.
        anchor = seal_rows.get(first - 1)
        if anchor is None:
            return fail(1, f"records 1 to {first - 1} are missing")
        if problem := check_seal(anchor, None):
            return fail(first - 1, problem)
        prev, expected_seq, result.starts_at = anchor.hash, first, first

    while True:
        rows = (await session.scalars(
            select(AuditRecord).where(AuditRecord.tenant_id == tenant_id,
                                      AuditRecord.seq >= expected_seq)
            .order_by(AuditRecord.seq).limit(batch)
            .execution_options(populate_existing=True))).all()
        if not rows:
            break
        evidence = await evidence_for(session, tenant_id,
                                      [r.seq for r in rows if r.evidence_digest])
        for rec in rows:
            if rec.seq != expected_seq:
                return fail(expected_seq, f"record {expected_seq} is missing")
            if rec.prev_hash != prev:
                return fail(rec.seq, f"record {rec.seq} does not point at record {rec.seq - 1}")
            if compute_hash(rec) != rec.hash:
                return fail(rec.seq, f"record {rec.seq} was changed after it was written")
            ev = evidence.get(rec.seq)
            if problem := evidence_problem(rec, ev):
                return fail(rec.seq, problem)
            if ev is not None and ev.data_enc is None:
                result.details_removed += 1
            if rec.seq in seal_rows and (problem := check_seal(seal_rows[rec.seq], rec.hash)):
                return fail(rec.seq, problem)
            prev, expected_seq, result.checked = rec.hash, expected_seq + 1, result.checked + 1

    # A seal on a record past the end means records were removed from the end.
    later = [s for seq, s in seal_rows.items() if seq >= expected_seq]
    if later:
        return fail(expected_seq, f"records {expected_seq} to {later[-1].seq} are missing "
                                  "(a seal shows they existed)")
    # Seals are hourly: records older than two hours past the last seal mean sealing
    # stopped (or the newest seals were removed; the outside copy, R3 B, closes that).
    if seal_mod.state() == "on" and expected_seq > 1:
        upto = seal_list[-1].seq if seal_list else 0
        stale = await session.scalar(
            select(AuditRecord.seq).where(AuditRecord.tenant_id == tenant_id,
                                          AuditRecord.seq > upto,
                                          AuditRecord.created_at
                                          < datetime.now(UTC) - timedelta(hours=2))
            .order_by(AuditRecord.seq).limit(1))
        if stale is not None:
            result.warning = (f"records from {stale} on are more than two hours old and not "
                              "sealed yet: check that the background worker is running")
    return result
