"""Data Steward: review queue, merges and undo, imports, enrichment, hygiene."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import runtime
from app.api.v1.accounts import MergeSummary
from app.audit import chain
from app.core.deps import CurrentUser, require_permission
from app.core.errors import AppError, NotFound
from app.db import get_db
from app.events.outbox import publish
from app.models import ImportBatch, MatchCandidate, MergeEvent
from app.steward import enrichment, importer, merge
from app.steward.candidates import decide
from app.steward.hygiene import tenant_dashboard
from app.steward.records import ALL_FIELDS, MODELS, current_provenance, to_json
from app.steward.service import process_record

router = APIRouter(prefix="/steward", tags=["data steward"])


def _actor(u: CurrentUser) -> str:
    return f"user:{u.user.id}"


def _summary(rec) -> dict[str, Any]:
    keys = ("id", "name", "full_name", "kind", "gstin", "email", "phone", "city", "status",
            "hygiene_score")
    return {k: to_json(getattr(rec, k)) for k in keys if hasattr(rec, k)}


class CandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    record_type: str
    left_id: uuid.UUID
    right_id: uuid.UUID
    score: float
    band: str
    reasons: list[dict[str, Any]]
    status: str
    decided_by: str | None
    decided_at: datetime | None
    created_at: datetime


class CandidateRow(CandidateOut):
    left: dict[str, Any]
    right: dict[str, Any]


@router.get("/candidates", response_model=list[CandidateRow])
async def list_candidates(
    record_type: str | None = None, status: str = "open", limit: int = Query(50, le=200),
    offset: int = 0, _: CurrentUser = Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> list[CandidateRow]:
    stmt = select(MatchCandidate).where(MatchCandidate.status == status)
    if record_type:
        stmt = stmt.where(MatchCandidate.record_type == record_type)
    cands = await db.scalars(stmt.order_by(MatchCandidate.score.desc(),
                                           MatchCandidate.created_at).limit(limit).offset(offset))
    out = []
    for c in cands:
        model = MODELS[c.record_type]
        left, right = await db.get(model, c.left_id), await db.get(model, c.right_id)
        out.append(CandidateRow(**CandidateOut.model_validate(c).model_dump(),
                                left=_summary(left) if left else {},
                                right=_summary(right) if right else {}))
    return out


@router.get("/candidates/{candidate_id}")
async def get_candidate(
    candidate_id: uuid.UUID, _: CurrentUser = Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Side-by-side view for the review screen, with a suggested survivor and the
    value each field would keep."""
    c = await db.get(MatchCandidate, candidate_id)
    if c is None:
        raise NotFound("Candidate not found")
    model = MODELS[c.record_type]
    a, b = await db.get(model, c.left_id), await db.get(model, c.right_id)
    if a is None or b is None:
        raise NotFound("One of the records no longer exists")
    survivor, other = merge.suggest_survivor(a, b, c.record_type)
    choices = merge.choose_fields(survivor, other, c.record_type,
                                  await current_provenance(db, survivor.id),
                                  await current_provenance(db, other.id))
    if c.record_type == "account":
        merge.apply_branch_rule(survivor, other, choices)
    fields = [f for f in ALL_FIELDS[c.record_type] if f not in merge.NOT_MERGED_FIELDS]
    return {
        "candidate": CandidateOut.model_validate(c).model_dump(mode="json"),
        "suggested_survivor_id": str(survivor.id),
        "records": {str(r.id): {f: to_json(getattr(r, f)) for f in
                                (*fields, "status", "hygiene_score", "source", "created_at")}
                    for r in (a, b)},
        "fields": fields,
        "suggested_choices": choices,
    }


class MergeIn(BaseModel):
    survivor_id: uuid.UUID
    field_choices: dict[str, str] = {}


@router.post("/candidates/{candidate_id}/merge", response_model=MergeSummary)
async def merge_candidate(
    candidate_id: uuid.UUID, body: MergeIn,
    current: CurrentUser = Depends(require_permission("steward.review")),
    db: AsyncSession = Depends(get_db),
) -> MergeEvent:
    c = await db.get(MatchCandidate, candidate_id)
    if c is None or c.status != "open":
        raise NotFound("Candidate not found or already decided")
    if body.survivor_id not in (c.left_id, c.right_id):
        raise AppError("survivor_id must be one of the two records")
    merged_id = c.right_id if body.survivor_id == c.left_id else c.left_id
    event = await merge.merge_records(db, c.record_type, body.survivor_id, merged_id,
                                      actor=_actor(current), automatic=False,
                                      overrides=body.field_choices)
    await runtime.supersede(db, merge.merge_dedupe_key(c.id), _actor(current),
                            "A person merged this pair from the review queue")
    return event


@router.post("/candidates/{candidate_id}/not-duplicate", response_model=CandidateOut)
async def reject_candidate(
    candidate_id: uuid.UUID, current: CurrentUser = Depends(require_permission("steward.review")),
    db: AsyncSession = Depends(get_db),
) -> MatchCandidate:
    c = await db.get(MatchCandidate, candidate_id)
    if c is None or c.status != "open":
        raise NotFound("Candidate not found or already decided")
    await decide(c, "not_duplicate", _actor(current))
    await runtime.supersede(db, merge.merge_dedupe_key(c.id), _actor(current),
                            "A person marked this pair as not a duplicate")
    await chain.append(db, current.tenant_id, "candidate.not_duplicate", actor=_actor(current),
                       record_type=c.record_type, record_id=c.left_id,
                       data={"candidate_id": c.id, "left_id": c.left_id, "right_id": c.right_id,
                             "score": float(c.score)})
    model = MODELS[c.record_type]
    from app.steward.hygiene import refresh_hygiene

    for rid in (c.left_id, c.right_id):
        rec = await db.get(model, rid)
        if rec is not None and rec.status == "active":
            await db.flush()
            await refresh_hygiene(db, rec, c.record_type)
    return c


class MergeRow(MergeSummary):
    survivor_name: str | None
    merged_name: str | None


def _name(snap: dict[str, Any]) -> str | None:
    return snap.get("name") or snap.get("full_name")


@router.get("/merges", response_model=list[MergeRow])
async def list_merges(
    record_type: str | None = None, limit: int = Query(50, le=200), offset: int = 0,
    _: CurrentUser = Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> list[MergeRow]:
    stmt = select(MergeEvent)
    if record_type:
        stmt = stmt.where(MergeEvent.record_type == record_type)
    events = await db.scalars(stmt.order_by(MergeEvent.created_at.desc())
                              .limit(limit).offset(offset))
    return [MergeRow(**MergeSummary.model_validate(ev).model_dump(),
                     survivor_name=_name(ev.survivor_before), merged_name=_name(ev.merged_before))
            for ev in events]


@router.get("/merges/{merge_id}")
async def get_merge(
    merge_id: uuid.UUID, _: CurrentUser = Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ev = await db.get(MergeEvent, merge_id)
    if ev is None:
        raise NotFound("Merge not found")
    return {**MergeSummary.model_validate(ev).model_dump(mode="json"),
            "survivor_name": _name(ev.survivor_before), "merged_name": _name(ev.merged_before),
            "field_choices": ev.field_choices, "repointed": ev.repointed,
            "survivor_before": ev.survivor_before, "merged_before": ev.merged_before,
            "undo_notes": ev.undo_notes, "undone_by": ev.undone_by}


@router.post("/merges/{merge_id}/undo")
async def undo_merge(
    merge_id: uuid.UUID, current: CurrentUser = Depends(require_permission("steward.undo")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ev = await merge.undo_merge(db, merge_id, actor=_actor(current))
    return {**MergeSummary.model_validate(ev).model_dump(mode="json"),
            "undo_notes": ev.undo_notes}


class ImportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    record_type: str
    filename: str
    status: str
    total_rows: int
    created_count: int
    auto_merged_count: int
    review_count: int
    error_count: int
    errors: list[dict[str, Any]]
    created_at: datetime
    finished_at: datetime | None


@router.post("/imports", response_model=ImportOut, status_code=202)
async def start_import(
    record_type: str = Form(...), file: UploadFile = File(...),
    current: CurrentUser = Depends(require_permission("steward.import")),
    db: AsyncSession = Depends(get_db),
) -> ImportBatch:
    if record_type not in ("account", "contact"):
        raise AppError("record_type must be 'account' or 'contact'")
    rows = importer.parse_csv(await file.read(), record_type)
    batch = ImportBatch(tenant_id=current.tenant_id, record_type=record_type,
                        filename=file.filename or "upload.csv", rows=rows,
                        total_rows=len(rows), created_by=_actor(current))
    db.add(batch)
    await db.flush()
    # The worker picks this up after commit (outbox -> Celery task), so a large file
    # never holds the request open.
    await publish(db, current.tenant_id, "import.requested", {"batch_id": str(batch.id)})
    return batch


@router.get("/imports", response_model=list[ImportOut])
async def list_imports(
    _: CurrentUser = Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> list[ImportBatch]:
    return list(await db.scalars(select(ImportBatch).order_by(ImportBatch.created_at.desc())
                                 .limit(50)))


@router.get("/imports/{batch_id}", response_model=ImportOut)
async def get_import(
    batch_id: uuid.UUID, _: CurrentUser = Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> ImportBatch:
    b = await db.get(ImportBatch, batch_id)
    if b is None:
        raise NotFound("Import not found")
    return b


class EnrichIn(BaseModel):
    record_type: str
    record_id: uuid.UUID
    field: str
    value: str
    source: str
    source_ref: str | None = None
    confidence: float = 1.0


@router.post("/enrich")
async def enrich(
    body: EnrichIn, current: CurrentUser = Depends(require_permission("steward.review")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    if body.record_type not in MODELS:
        raise AppError("record_type must be 'account' or 'contact'")
    result = await enrichment.enrich(db, body.record_type, body.record_id, body.field,
                                     body.value, source=body.source, actor=_actor(current),
                                     source_ref=body.source_ref, confidence=body.confidence)
    return {"result": result}


@router.post("/proposals/{proposal_id}/{decision}")
async def decide_proposal(
    proposal_id: uuid.UUID, decision: str,
    current: CurrentUser = Depends(require_permission("steward.review")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    if decision not in ("accept", "reject"):
        raise NotFound("Unknown decision")
    p = await enrichment.decide_proposal(db, proposal_id, accept=decision == "accept",
                                         actor=_actor(current))
    return {"status": p.status}


@router.get("/hygiene")
async def hygiene(
    _: CurrentUser = Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await tenant_dashboard(db)


@router.post("/rescan")
async def rescan(
    record_type: str = "account", limit: int = Query(500, le=5000),
    current: CurrentUser = Depends(require_permission("steward.review")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Re-run duplicate detection over existing records (e.g. after tuning thresholds)."""
    model = MODELS.get(record_type)
    if model is None:
        raise AppError("record_type must be 'account' or 'contact'")
    ids = (await db.scalars(select(model.id).where(model.status == "active").limit(limit))).all()
    totals = {"checked": 0, "auto_merged": 0, "queued": 0}
    for rid in ids:
        s = await process_record(db, record_type, rid)
        totals["checked"] += 1
        totals["auto_merged"] += s["auto_merged"]
        totals["queued"] += s["queued"]
    return totals
