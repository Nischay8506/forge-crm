"""Passive enrichment with provenance (F12.1).

Other features (email signature parsing, document extraction, ERP sync) call
`enrich()` with a value they found. The steward fills empty fields, confirms
matching ones, and turns conflicting ones into proposals for a person rather
than overwriting what is there.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFound
from app.models import FieldProvenance
from app.steward import records
from app.steward.hygiene import refresh_hygiene

FILLED, CONFIRMED, PROPOSED, INVALID = "filled", "confirmed", "proposed", "invalid"


async def enrich(
    session: AsyncSession, record_type: str, record_id: uuid.UUID, field: str, value: str, *,
    source: str, actor: str, source_ref: str | None = None, confidence: float = 1.0,
) -> str:
    if field not in records.KEY_FIELDS[record_type]:
        raise AppError(f"'{field}' cannot be enriched")
    rec = await session.get(records.MODELS[record_type], record_id)
    if rec is None or rec.status != "active":
        raise NotFound("Record not found")
    clean = records.clean_account if record_type == "account" else records.clean_contact
    values, errors, _ = clean({field: value})
    if errors or values.get(field) is None:
        return INVALID
    new = values[field]
    current = getattr(rec, field)
    update = records.update_account if record_type == "account" else records.update_contact

    if current in (None, ""):
        await update(session, rec, {field: value}, source=source, actor=actor)
        return FILLED
    if current == new:
        prov = (await records.current_provenance(session, rec.id)).get(field)
        if prov is not None and (source in records.VERIFYING_SOURCES or confidence >= 0.9):
            prov.verified_at = datetime.now(UTC)
            await refresh_hygiene(session, rec, record_type)
        return CONFIRMED
    existing = await session.scalar(select(FieldProvenance).where(
        FieldProvenance.record_id == rec.id, FieldProvenance.field == field,
        FieldProvenance.status == "proposed", FieldProvenance.value == str(new)))
    if existing is None:
        session.add(FieldProvenance(
            tenant_id=rec.tenant_id, record_type=record_type, record_id=rec.id, field=field,
            value=str(new), source=source, source_ref=source_ref,
            confidence=Decimal(str(confidence)), status="proposed", recorded_by=actor))
    return PROPOSED


async def decide_proposal(session: AsyncSession, proposal_id: uuid.UUID, *, accept: bool,
                          actor: str) -> FieldProvenance:
    prop = await session.get(FieldProvenance, proposal_id)
    if prop is None or prop.status != "proposed":
        raise NotFound("Proposal not found or already decided")
    if accept:
        rec = await session.get(records.MODELS[prop.record_type], prop.record_id)
        if rec is None or rec.status != "active":
            raise AppError("The record is no longer active")
        update = records.update_account if prop.record_type == "account" else (
            records.update_contact)
        # The person accepting it verifies the value, so it is recorded as a manual edit.
        await update(session, rec, {prop.field: prop.value}, source="manual", actor=actor)
    prop.status = "accepted" if accept else "rejected"
    return prop
