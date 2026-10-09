"""Retention of the audit trail and logs (decision R5 A; Step 4 safeguard 4).

Every company starts on an editable profile; India is the only one so far. Each
record category has its own period and the law it follows, both editable by a
person with "audit.manage". The periods are a suggested starting point, not legal
advice: until someone records who confirmed them (a CA or lawyer), the settings
screen says they are unconfirmed, and legal validation is on the list of things to
do before production.

Each night, per company:
- evidence older than its category's period is removed (the record's skeleton and
  fingerprint stay, so the chain still verifies);
- ERP sync history and model-call logs older than their period are deleted;
- skeleton records older than the skeleton period (never under 8 years) are
  trimmed by audit_trim(), only up to a sealed record, so the chain keeps verifying
  from that seal;
- nothing about an account under a legal hold (or an account merged into it) is
  removed or trimmed;
- the run itself is recorded in the trail ("retention.run").
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import chain
from app.core.errors import ValidationFailed
from app.models import (
    Account,
    AuditEvidence,
    AuditRecord,
    AuditSeal,
    Contact,
    LegalHold,
    LlmCall,
    RetentionSettings,
    SyncRun,
)

log = logging.getLogger(__name__)

SYSTEM = "system:retention"
SKELETON_FLOOR = 96  # months; also enforced inside audit_trim()
MINIMUM = 12  # months for every other category, so a slip cannot wipe recent history

CATEGORIES: dict[str, str] = {
    "skeleton": "Audit skeleton (who, what, when, which record, gate result)",
    "customer_money": "Details behind customer-facing and money actions",
    "other": "Other audit details (inputs, notes, prompts)",
    "sync_logs": "ERP sync history",
    "model_logs": "Model-call logs",
}

PROFILES: dict[str, dict[str, Any]] = {
    "india": {
        "label": "India (suggested starting point)",
        "periods": {"skeleton": 96, "customer_money": 96, "other": 36, "sync_logs": 12,
                    "model_logs": 12},
        "basis": {
            "skeleton": "Companies Act 2013 s.128(5): books of account kept 8 financial years",
            "customer_money": "Companies Act 2013 s.128(5) (8 years); CGST Act 2017 s.36 "
                              "(72 months after the annual return is due)",
            "other": "No specific rule; 3 years follows the usual limitation period for "
                     "claims (Limitation Act 1963). DPDP Act 2023 s.8(7): erase personal data "
                     "once no longer needed",
            "sync_logs": "Operational records; no specific rule",
            "model_logs": "Operational records; no specific rule",
        },
    },
}
DEFAULT_PROFILE = "india"


def defaults(profile: str = DEFAULT_PROFILE) -> dict[str, Any]:
    p = PROFILES[profile]
    return {"profile": profile, "periods": dict(p["periods"]), "basis": dict(p["basis"])}


async def current(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    row = await session.get(RetentionSettings, tenant_id)
    if row is None:
        return {**defaults(), "legal_review": None, "updated_by": None, "updated_at": None,
                "is_default": True}
    return {"profile": row.profile, "periods": {**PROFILES[row.profile]["periods"],
                                                **row.periods},
            "basis": {**PROFILES[row.profile]["basis"], **row.basis},
            "legal_review": row.legal_review, "updated_by": row.updated_by,
            "updated_at": row.updated_at, "is_default": False}


def check_periods(periods: dict[str, int]) -> None:
    problems = []
    for key, months in periods.items():
        if key not in CATEGORIES:
            problems.append(f"Unknown category: {key}")
        elif key == "skeleton" and months < SKELETON_FLOOR:
            problems.append("The audit skeleton is kept at least 8 years (96 months)")
        elif months < MINIMUM:
            problems.append(f"{CATEGORIES[key]}: at least {MINIMUM} months")
        elif months > 600:
            problems.append(f"{CATEGORIES[key]}: at most 600 months")
    if problems:
        raise ValidationFailed("; ".join(problems))


async def change(session: AsyncSession, tenant_id: uuid.UUID, actor: str, *,
                 profile: str | None = None, periods: dict[str, int] | None = None,
                 basis: dict[str, str] | None = None) -> RetentionSettings:
    before = await current(session, tenant_id)
    profile = profile or before["profile"]
    if profile not in PROFILES:
        raise ValidationFailed(f"Unknown retention profile: {profile}")
    new_periods = {**(PROFILES[profile]["periods"] if profile != before["profile"]
                      else before["periods"]), **(periods or {})}
    new_basis = {**(PROFILES[profile]["basis"] if profile != before["profile"]
                    else before["basis"]), **(basis or {})}
    check_periods(new_periods)
    row = await session.get(RetentionSettings, tenant_id)
    if row is None:
        row = RetentionSettings(tenant_id=tenant_id, profile=profile, periods=new_periods,
                                basis=new_basis, updated_by=actor)
        session.add(row)
    else:
        # Changing a period invalidates an earlier legal confirmation.
        if row.periods != new_periods or row.basis != new_basis or row.profile != profile:
            row.legal_review = None
        row.profile, row.periods, row.basis = profile, new_periods, new_basis
        row.updated_by, row.updated_at = actor, datetime.now(UTC)
    await session.flush()
    await chain.append(session, tenant_id, "retention.changed", actor=actor, data={
        "profile": profile, "periods": new_periods,
        "before": {"profile": before["profile"], "periods": before["periods"]},
        "basis": new_basis})
    return row


async def record_review(session: AsyncSession, tenant_id: uuid.UUID, actor: str, *,
                        reviewed_by: str, note: str | None) -> RetentionSettings:
    row = await session.get(RetentionSettings, tenant_id)
    if row is None:
        row = await change(session, tenant_id, actor)
    row.legal_review = {"reviewed_by": reviewed_by.strip(), "note": (note or "").strip() or None,
                        "recorded_by": actor, "recorded_at": datetime.now(UTC).isoformat(),
                        "periods": row.periods}
    await chain.append(session, tenant_id, "retention.reviewed", actor=actor, data={
        "periods": row.periods, "reviewed_by": reviewed_by.strip(), "note": note})
    return row


# Legal holds ------------------------------------------------------------------------

async def place_hold(session: AsyncSession, tenant_id: uuid.UUID, account_id: uuid.UUID,
                     reason: str, actor: str) -> LegalHold:
    if await session.scalar(select(LegalHold.id).where(LegalHold.account_id == account_id,
                                                       LegalHold.lifted_at.is_(None))):
        raise ValidationFailed("This account is already under a legal hold")
    hold = LegalHold(tenant_id=tenant_id, account_id=account_id, reason=reason.strip(),
                     placed_by=actor)
    session.add(hold)
    await session.flush()
    await chain.append(session, tenant_id, "legal_hold.placed", actor=actor,
                       record_type="account", record_id=account_id,
                       evidence_class="customer_money",
                       data={"hold_id": hold.id, "reason": hold.reason})
    return hold


async def lift_hold(session: AsyncSession, hold: LegalHold, actor: str,
                    note: str | None) -> LegalHold:
    hold.lifted_by, hold.lifted_at = actor, datetime.now(UTC)
    await chain.append(session, hold.tenant_id, "legal_hold.lifted", actor=actor,
                       record_type="account", record_id=hold.account_id,
                       evidence_class="customer_money",
                       data={"hold_id": hold.id, "note": note})
    return hold


async def held_accounts(session: AsyncSession) -> set[uuid.UUID]:
    """Accounts under an open hold, plus every account merged into one of them."""
    held = set(await session.scalars(select(LegalHold.account_id).where(
        LegalHold.lifted_at.is_(None))))
    frontier = set(held)
    while frontier:
        more = set(await session.scalars(select(Account.id).where(
            Account.merged_into_id.in_(frontier)))) - held
        held |= more
        frontier = more
    return held


async def is_held(session: AsyncSession, account_id: uuid.UUID | None) -> bool:
    return account_id is not None and account_id in await held_accounts(session)


# The nightly run --------------------------------------------------------------------

def _older_than(months: int):
    return text(f"now() - interval '{int(months)} months'")


async def run_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    settings = await current(session, tenant_id)
    periods = settings["periods"]
    held = await held_accounts(session)
    held_contacts = select(Contact.id).where(Contact.account_id.in_(held)) if held else None
    removed: dict[str, int] = {}

    for cls in ("customer_money", "other"):
        stmt = (update(AuditEvidence)
                .where(AuditEvidence.retention_class == cls,
                       AuditEvidence.data_enc.is_not(None),
                       AuditEvidence.created_at < _older_than(periods[cls]))
                .values(data_enc=None, removed_at=func.now(),
                        removed_reason=f"retention: {cls} older than {periods[cls]} months")
                .execution_options(synchronize_session=False))
        if held:
            stmt = stmt.where(or_(AuditEvidence.account_id.is_(None),
                                  AuditEvidence.account_id.not_in(held)),
                              or_(AuditEvidence.contact_id.is_(None),
                                  AuditEvidence.contact_id.not_in(held_contacts)))
        removed[cls] = (await session.execute(stmt)).rowcount or 0

    removed["sync_runs"] = (await session.execute(
        delete(SyncRun).where(SyncRun.started_at < _older_than(periods["sync_logs"]))
        .execution_options(synchronize_session=False))).rowcount or 0
    removed["llm_calls"] = (await session.execute(
        delete(LlmCall).where(LlmCall.created_at < _older_than(periods["model_logs"]))
        .execution_options(synchronize_session=False))).rowcount or 0

    # Skeleton: up to the newest seal that is old enough and before anything held.
    months = max(SKELETON_FLOOR, periods["skeleton"])
    limit = select(AuditRecord.seq).where(AuditRecord.tenant_id == tenant_id,
                                          AuditRecord.created_at < _older_than(months))
    candidate = select(func.max(AuditSeal.seq)).where(AuditSeal.tenant_id == tenant_id,
                                                      AuditSeal.seq.in_(limit))
    if held:
        first_held = await session.scalar(select(func.min(AuditRecord.seq)).where(
            AuditRecord.tenant_id == tenant_id, AuditRecord.record_id.in_(held)))
        if first_held is not None:
            candidate = candidate.where(AuditSeal.seq < first_held)
    upto = await session.scalar(candidate)
    first = await session.scalar(select(func.min(AuditRecord.seq)).where(
        AuditRecord.tenant_id == tenant_id))
    removed["skeleton_records"] = 0
    if upto is not None and first is not None and upto >= first:
        removed["skeleton_records"] = await session.scalar(
            text("SELECT audit_trim(:t, :q)"), {"t": tenant_id, "q": upto})

    await chain.append(session, tenant_id, "retention.run", actor=SYSTEM, data={
        "removed": removed, "periods": periods, "held_accounts": len(held)})
    return removed


async def run_all() -> dict[str, Any]:
    from app.db import get_sessionmaker, tenant_session
    from app.models import Tenant

    async with get_sessionmaker()() as s:
        tenant_ids = list(await s.scalars(select(Tenant.id)))
    totals: dict[str, int] = {}
    failed = 0
    for tid in tenant_ids:
        try:
            async with tenant_session(tid) as s:
                for k, v in (await run_tenant(s, tid)).items():
                    totals[k] = totals.get(k, 0) + v
        except Exception:  # one company's failure must not stop the others
            failed += 1
            log.exception("retention run for tenant %s failed", tid)
    return {"tenants": len(tenant_ids), "failed": failed, **totals}
