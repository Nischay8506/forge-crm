"""Hygiene score (0-100) per record, explainable, plus the tenant dashboard.

  completeness 40  key fields filled (weights depend on the record kind)
  validity     30  identifiers, phone, email, pincode pass their checks
  freshness    20  share of filled key fields verified in the last 365 days
  duplicates   10  no open duplicate candidate involving this record

Scores are worked out on every write, and again each night (`rescore_all`) so that
freshness follows the calendar for records nobody touches.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Contact, MatchCandidate

log = logging.getLogger(__name__)

WEIGHTS: dict[str, dict[str, int]] = {
    "entity": {"name": 4, "gstin": 10, "pan": 3, "phone": 5, "email": 4, "website": 3,
               "address_line1": 4, "city": 2, "state": 2, "pincode": 4, "industry": 3},
    "site": {"name": 4, "gstin": 6, "address_line1": 8, "city": 3, "state": 3, "pincode": 8,
             "phone": 4, "parent_id": 6},
    "group": {"name": 6, "website": 4, "industry": 4},
    "contact": {"full_name": 6, "email": 10, "phone": 8, "designation": 5, "account_id": 8},
}
FRESH_DAYS = 365
NIGHTLY_BATCH = 500


async def _open_candidates(session: AsyncSession, record_id: uuid.UUID) -> int:
    return await session.scalar(
        select(func.count()).select_from(MatchCandidate).where(
            MatchCandidate.status == "open",
            or_(MatchCandidate.left_id == record_id, MatchCandidate.right_id == record_id),
        )
    ) or 0


def score(
    record: Account | Contact, record_type: str, prov: dict[str, Any], open_candidates: int,
    problems: list[str], warnings: list[str],
) -> tuple[int, dict[str, Any]]:
    kind = "contact" if record_type == "contact" else record.kind
    weights = WEIGHTS[kind]
    filled = {f: getattr(record, f) not in (None, "") for f in weights}
    completeness = 40 * sum(w for f, w in weights.items() if filled[f]) / sum(weights.values())

    validity = max(0, 30 - 10 * len(problems) - 5 * len(warnings))

    cutoff = datetime.now(UTC) - timedelta(days=FRESH_DAYS)
    tracked = [f for f in weights if filled[f] and f in prov]
    fresh = [f for f in tracked if prov[f].verified_at and prov[f].verified_at >= cutoff]
    freshness = 20 * len(fresh) / len(tracked) if tracked else 0

    duplicates = 10 if open_candidates == 0 else 0
    total = round(completeness + validity + freshness + duplicates)
    details = {
        "score": total,
        "completeness": {"points": round(completeness, 1), "of": 40,
                         "missing": [f for f, ok in filled.items() if not ok]},
        "validity": {"points": validity, "of": 30, "problems": problems, "warnings": warnings},
        "freshness": {"points": round(freshness, 1), "of": 20, "verified": len(fresh),
                      "tracked": len(tracked),
                      "unverified": [f for f in tracked if f not in fresh]},
        "duplicates": {"points": duplicates, "of": 10, "open_candidates": open_candidates},
        "computed_at": datetime.now(UTC).isoformat(),
    }
    return total, details


async def _compute(
    session: AsyncSession, record: Account | Contact, record_type: str,
    extra_warnings: list[str] | None = None,
) -> tuple[int, dict[str, Any]]:
    from app.steward.records import clean_account, clean_contact, current_provenance

    if record_type == "account":
        current = {f: getattr(record, f) for f in ("name", "gstin", "pan", "cin", "phone",
                                                   "email", "pincode", "state", "website")}
        # Re-run the checks on stored values so problems persist until fixed.
        _, problems, warnings = clean_account({k: v for k, v in current.items()
                                               if v is not None})
    else:
        _, problems, warnings = clean_contact({"email": record.email, "phone": record.phone})
    for w in extra_warnings or []:
        if w not in problems and w not in warnings:
            warnings.append(w)
    prov = await current_provenance(session, record.id)
    return score(record, record_type, prov, await _open_candidates(session, record.id),
                 problems, warnings)


async def refresh_hygiene(
    session: AsyncSession, record: Account | Contact, record_type: str,
    extra_warnings: list[str] | None = None,
) -> int:
    total, details = await _compute(session, record, record_type, extra_warnings)
    record.hygiene_score = total
    record.hygiene_details = details
    return total


def _same(a: dict[str, Any] | None, b: dict[str, Any]) -> bool:
    """Equal apart from when they were worked out."""
    if a is None:
        return False
    return ({k: v for k, v in a.items() if k != "computed_at"}
            == {k: v for k, v in b.items() if k != "computed_at"})


async def rescore_tenant(session: AsyncSession) -> dict[str, int]:
    """Score every active record of the session's tenant again (the nightly job).

    Only the passing of time is applied: warnings recorded at the last write are kept,
    and a record whose score and reasons did not change is not written, so its
    updated_at stays as it was.
    """
    out = {"checked": 0, "changed": 0, "lowered": 0}
    for record_type, model in (("account", Account), ("contact", Contact)):
        last: uuid.UUID | None = None
        while True:
            stmt = (select(model).where(model.status == "active").order_by(model.id)
                    .limit(NIGHTLY_BATCH))
            if last is not None:
                stmt = stmt.where(model.id > last)
            rows = list(await session.scalars(stmt))
            if not rows:
                break
            for rec in rows:
                old = rec.hygiene_details or {}
                kept = list((old.get("validity") or {}).get("warnings") or [])
                total, details = await _compute(session, rec, record_type, kept)
                out["checked"] += 1
                if rec.hygiene_score == total and _same(rec.hygiene_details, details):
                    continue
                out["changed"] += 1
                if rec.hygiene_score is not None and total < rec.hygiene_score:
                    out["lowered"] += 1
                rec.hygiene_score, rec.hygiene_details = total, details
            last = rows[-1].id
            await session.flush()
            session.expunge_all()  # keep memory flat on large tenants
    return out


async def rescore_all() -> dict[str, int]:
    """The nightly job across tenants; one tenant's failure does not stop the others."""
    from app.db import get_sessionmaker, tenant_session
    from app.models import Tenant

    async with get_sessionmaker()() as s:
        tenant_ids = list(await s.scalars(select(Tenant.id)))
    total = {"tenants": len(tenant_ids), "checked": 0, "changed": 0, "lowered": 0}
    for tid in tenant_ids:
        try:
            async with tenant_session(tid) as s:
                for k, v in (await rescore_tenant(s)).items():
                    total[k] += v
        except Exception:  # noqa: BLE001 - logged, the next tenant still runs
            log.exception("hygiene re-score for tenant %s failed", tid)
    log.info("hygiene re-score: %s", total)
    return total


async def tenant_dashboard(session: AsyncSession) -> dict[str, Any]:
    """Aggregates for the hygiene dashboard (RLS limits it to the caller's tenant)."""
    out: dict[str, Any] = {}
    for record_type, model in (("account", Account), ("contact", Contact)):
        bucket = case(
            (model.hygiene_score >= 90, "90-100"), (model.hygiene_score >= 70, "70-89"),
            (model.hygiene_score >= 50, "50-69"), else_="0-49",
        )
        rows = (await session.execute(
            select(bucket, func.count()).where(model.status == "active").group_by(bucket)
        )).all()
        avg = await session.scalar(
            select(func.avg(model.hygiene_score)).where(model.status == "active"))
        total = await session.scalar(
            select(func.count()).select_from(model).where(model.status == "active"))
        merged = await session.scalar(
            select(func.count()).select_from(model).where(model.status == "merged"))
        invalid = await session.scalar(
            select(func.count()).select_from(model).where(
                model.status == "active",
                func.jsonb_array_length(model.hygiene_details["validity"]["problems"]) > 0))
        open_cands = await session.scalar(
            select(func.count()).select_from(MatchCandidate).where(
                MatchCandidate.record_type == record_type, MatchCandidate.status == "open"))
        worst = (await session.scalars(
            select(model).where(model.status == "active")
            .order_by(model.hygiene_score.asc().nulls_first()).limit(10))).all()
        out[record_type + "s"] = {
            "active": total or 0,
            "merged": merged or 0,
            "average_score": round(float(avg), 1) if avg is not None else None,
            "distribution": {k: v for k, v in rows},
            "with_invalid_values": invalid or 0,
            "open_duplicate_candidates": open_cands or 0,
            "lowest": [{"id": str(r.id), "name": getattr(r, "name", None)
                        or getattr(r, "full_name", None), "score": r.hygiene_score}
                       for r in worst],
        }
    entities_without_gstin = await session.scalar(
        select(func.count()).select_from(Account).where(
            Account.status == "active", Account.kind == "entity", Account.gstin.is_(None)))
    out["accounts"]["entities_without_gstin"] = entities_without_gstin or 0
    return out
