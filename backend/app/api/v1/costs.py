"""Costs (F3.2, decisions C3 A, K8 to K13 A): material and machine-hour rates with the
date they apply from, and each product family's cost model.

A new rate never changes an old one. When it applies from today or earlier, open draft
quotes that used (or missed) it are worked out again at once and sent ones are marked
"reprice recommended" (K9 A); the answer lists them for the person who changed the rate.
A cost model is checked against every valid configuration before it is saved, and each
save is a new version (K13 A)."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_permission
from app.core.errors import NotFound, ValidationFailed
from app.cpq import costing, costmodel, products, quotes, rates, units
from app.cpq.rates import RateBook
from app.cpq.samples import COST_SAMPLES
from app.db import get_db
from app.models import Configuration, CostModel, ProductFamily

router = APIRouter(prefix="/costs", tags=["costs"])

VIEW = require_permission("costs.view")
MANAGE = require_permission("costs.manage")


def _actor(u: CurrentUser) -> str:
    return f"user:{u.user.id}"


class MaterialIn(BaseModel):
    code: str = Field(max_length=40)
    name: str = Field(max_length=120)
    unit: str = Field(default="kg", max_length=20)
    rate: str = Field(max_length=30)
    yield_pct: str | None = Field(default=None, max_length=12)
    scrap_rate: str | None = Field(default=None, max_length=30)
    effective_from: str = Field(max_length=10)
    note: str | None = Field(default=None, max_length=300)


class MachineIn(BaseModel):
    code: str = Field(max_length=140)
    name: str = Field(max_length=140)
    hour_rate: str = Field(max_length=30)
    effective_from: str = Field(max_length=10)
    note: str | None = Field(default=None, max_length=300)


@router.get("/materials")
async def materials(at: date | None = Query(None), current: CurrentUser = Depends(VIEW),
                    db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    day = at or await quotes.today(db, current.tenant_id)
    return {"at": day, "materials": await rates.material_list(db, day)}


@router.post("/materials", status_code=201)
async def add_material(body: MaterialIn, current: CurrentUser = Depends(MANAGE),
                       db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    day = await quotes.today(db, current.tenant_id)
    row = await rates.add_material(db, current.tenant_id, body.model_dump(), _actor(current),
                                   day)
    return {"rate": rates.material_out(row),
            **await _changed(db, current, "material", row.code, row.effective_from, day)}


async def _changed(db: AsyncSession, current: CurrentUser, kind: str, code: str,
                   effective_from: date, day: date) -> dict[str, Any]:
    """K9 A: a rate in force now works drafts out again and flags sent quotes at once;
    one dated ahead does so on its date (the hourly cost check)."""
    done = {"recosted": [], "flagged": []}
    if effective_from <= day:
        done = await costing.refresh(db, current.tenant_id, _actor(current),
                                     rate=(kind, code))
    return {"affected_quotes": await rates.affected_quotes(db, kind, code), **done,
            "applies_now": effective_from <= day}


@router.get("/machines")
async def machines(at: date | None = Query(None), current: CurrentUser = Depends(VIEW),
                   db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    day = at or await quotes.today(db, current.tenant_id)
    return {"at": day, "machines": await rates.machine_list(db, day)}


@router.post("/machines", status_code=201)
async def add_machine(body: MachineIn, current: CurrentUser = Depends(MANAGE),
                      db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    day = await quotes.today(db, current.tenant_id)
    row = await rates.add_machine(db, current.tenant_id, body.model_dump(), _actor(current),
                                  day)
    return {"rate": rates.machine_out(row),
            **await _changed(db, current, "machine", row.code, row.effective_from, day)}


@router.get("/units")
async def unit_list(_: CurrentUser = Depends(VIEW)) -> dict[str, Any]:
    """The units Forge converts between (K12 A), for the rate and model forms."""
    return {"canonical": [{"unit": u, "label": units.LABEL[u], "kind": units.KIND[u]}
                          for u in units.CANONICAL],
            "material_units": list(rates.MATERIAL_UNITS),
            "common": ["kg", "g", "tonne", "quintal", "m", "mm", "ft", "m2", "sq ft", "m3",
                       "litre", "nos"]}


# Cost models (K8, K13 A) -----------------------------------------------------------------

class ModelIn(BaseModel):
    model: dict[str, Any]
    note: str | None = Field(default=None, max_length=300)


class TryIn(BaseModel):
    model: dict[str, Any]
    values: dict[str, Any] | None = None  # a configuration to work out; else the first
    qty: Decimal | None = Field(default=None, ge=0, le=Decimal("1e9"))


async def _family(db: AsyncSession, family_id: uuid.UUID) -> ProductFamily:
    f = await db.get(ProductFamily, family_id)
    if f is None:
        raise NotFound("Product family not found")
    return f


def _version_out(r: CostModel, with_model: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"id": str(r.id), "version": r.version, "current": r.current,
                           "note": r.note, "created_by": r.created_by,
                           "created_at": r.created_at,
                           "full": bool(r.model.get("materials") or r.model.get("operations")),
                           "rate_card": bool(r.model.get("rate_card"))}
    if with_model:
        out["model"] = r.model
    return out


SAMPLE_KEYS = {"size", "class", "material", "facing"}


@router.get("/models")
async def list_models(_: CurrentUser = Depends(VIEW),
                      db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    fams = list(await db.scalars(select(ProductFamily).where(ProductFamily.active.is_(True))
                                 .order_by(ProductFamily.name)))
    cms = {r.family_id: r for r in await db.scalars(select(CostModel).where(
        CostModel.current.is_(True)))}
    pms = await products.with_models(db)
    return {"families": [{
        "id": str(f.id), "name": f.name, "template": f.template,
        "options_version": pms[f.id].version if f.id in pms else None,
        "cost_model": _version_out(cms[f.id]) if f.id in cms else None,
    } for f in fams]}


@router.get("/models/{family_id}")
async def get_model(family_id: uuid.UUID, current: CurrentUser = Depends(VIEW),
                    db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    f = await _family(db, family_id)
    rows = list(await db.scalars(select(CostModel).where(CostModel.family_id == f.id)
                                 .order_by(CostModel.version.desc()).limit(50)))
    pm_row = await products.current(db, f.id)
    attrs = []
    if pm_row is not None:
        pm = products.model_of(pm_row)
        for a in pm.attributes:
            raw = next((x for x in pm_row.attributes if x.get("key") == a.key), {})
            attrs.append({"key": a.key, "label": a.label, "kind": raw.get("kind"),
                          "values": [str(o.get("value")) for o in raw.get("options") or []]})
    keys = {a["key"] for a in attrs}
    cur = next((r for r in rows if r.current), None)
    return {"family": {"id": str(f.id), "name": f.name, "template": f.template},
            "attributes": attrs, "options_version": pm_row.version if pm_row else None,
            "current": _version_out(cur, with_model=True) if cur else None,
            "versions": [_version_out(r, with_model=True) for r in rows],
            "sample": "flanges" if SAMPLE_KEYS <= keys and "flanges" in COST_SAMPLES else None}


@router.post("/models/{family_id}/check")
async def check_model(family_id: uuid.UUID, body: TryIn, current: CurrentUser = Depends(VIEW),
                      db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """The checks saving does, without saving, and what one configuration would cost
    with today's rates (for the setup screen as you type)."""
    f = await _family(db, family_id)
    result = await costing.check_model(db, f, body.model)
    out: dict[str, Any] = {"problems": result.problems, "warnings": result.warnings,
                           "tried": result.tried, "preview": None}
    if result.problems:
        return out
    pm_row = await products.current(db, f.id)
    values = body.values
    if values is None and pm_row is not None:
        examples = costmodel._examples(products.model_of(pm_row))  # noqa: SLF001
        values = examples[0] if examples else {}
    day = await quotes.today(db, current.tenant_id)
    book = costing.Book(day, await RateBook.load(db, day))
    draft = CostModel(id=uuid.uuid4(), tenant_id=f.tenant_id, family_id=f.id,
                      version=(await db.scalar(select(CostModel.version).where(
                          CostModel.family_id == f.id, CostModel.current.is_(True))) or 0) + 1,
                      current=False, model=costmodel.dump(body.model), created_by="draft")
    book.models, book.families = {f.id: draft}, {f.id: f.name}
    cfg = Configuration(id=uuid.uuid4(), values={k: str(v) for k, v in (values or {}).items()})
    book.configs = {cfg.id: cfg}
    preview = costing.work_out(book, family_id=f.id, configuration_id=cfg.id, item_code=None,
                               unit=None, qty=body.qty or Decimal(1))
    preview["inputs"]["configuration_id"] = None
    out["preview"] = preview
    return out


@router.put("/models/{family_id}")
async def save_model(family_id: uuid.UUID, body: ModelIn, current: CurrentUser = Depends(MANAGE),
                     db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    f = await _family(db, family_id)
    try:
        row = await costing.save_model(db, f, body.model, _actor(current), body.note)
    except costmodel.CostModelProblems as exc:
        raise ValidationFailed(exc.problems) from exc
    return _version_out(row, with_model=True)


@router.post("/models/{family_id}/sample")
async def use_sample(family_id: uuid.UUID, current: CurrentUser = Depends(MANAGE),
                     db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """The flanges sample cost model and the sample rates it needs (C18 A)."""
    f = await _family(db, family_id)
    try:
        done = await costing.add_sample(db, f, _actor(current))
    except costmodel.CostModelProblems as exc:
        raise ValidationFailed(exc.problems) from exc
    return {**_version_out(done["model"], with_model=True), "sample_rates": done["sample_rates"]}
