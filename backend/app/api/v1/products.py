"""Products: each family's options and rules, and the guided configurator (F3.1,
decision C4 A). The rules are checked for conflicts and dead ends before they are
saved; the configurator offers only choices that still lead to a valid product, and a
configuration that breaks a rule is refused with the rule's reason, here and in the UI."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_permission
from app.core.errors import AppError, Forbidden, NotFound, ValidationFailed
from app.core.permissions import has_permission
from app.cpq import costing, products, quotes
from app.cpq.configurator import ModelProblems, TooComplex
from app.cpq.samples import SAMPLES
from app.db import get_db
from app.enquiries import records
from app.models import Configuration, Enquiry, EnquiryLine, ProductFamily, ProductModel
from app.models.enquiries import OPEN_STATUSES

router = APIRouter(prefix="/products", tags=["products"])

USE = require_permission("products.configure")
MANAGE = require_permission("products.manage")


def _actor(u: CurrentUser) -> str:
    return f"user:{u.user.id}"


async def _family(db: AsyncSession, family_id: uuid.UUID) -> ProductFamily:
    f = await db.get(ProductFamily, family_id)
    if f is None:
        raise NotFound("Product family not found")
    return f


class OptionsIn(BaseModel):
    attributes: list[dict[str, Any]] = Field(default_factory=list, max_length=30)
    rules: list[dict[str, Any]] = Field(default_factory=list, max_length=300)
    code_pattern: str = Field(default="", max_length=100)
    note: str | None = Field(default=None, max_length=300)


class ValuesIn(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class ConfigIn(ValuesIn):
    family_id: uuid.UUID
    enquiry_line_id: uuid.UUID | None = None


@router.get("/families")
async def list_families(_: CurrentUser = Depends(USE),
                        db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    fams = list(await db.scalars(select(ProductFamily).where(ProductFamily.active.is_(True))
                                 .order_by(ProductFamily.name)))
    models = await products.with_models(db)
    return {"families": [
        {"id": str(f.id), "name": f.name, "make_ability": f.make_ability,
         "template": f.template,
         "options": {"version": m.version, "attributes": len(m.attributes),
                     "rules": len(m.rules), "code_pattern": m.code_pattern}
         if (m := models.get(f.id)) else None}
        for f in fams]}


@router.get("/families/{family_id}")
async def get_family(family_id: uuid.UUID, _: CurrentUser = Depends(USE),
                     db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    f = await _family(db, family_id)
    versions = list(await db.scalars(select(ProductModel).where(
        ProductModel.family_id == f.id).order_by(ProductModel.version.desc()).limit(20)))
    cur = next((v for v in versions if v.current), None)
    return {"family": {"id": str(f.id), "name": f.name, "make_ability": f.make_ability},
            "options": products.model_out(cur),
            "versions": [{"version": v.version, "note": v.note, "created_by": v.created_by,
                          "created_at": v.created_at} for v in versions],
            "samples": sorted(SAMPLES)}


@router.post("/families/{family_id}/check")
async def check_options(family_id: uuid.UUID, body: OptionsIn,
                        _: CurrentUser = Depends(MANAGE),
                        db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """The same checks as saving, without saving (for the setup screen as you type)."""
    await _family(db, family_id)
    _, problems = products.check(body.model_dump())
    return {"problems": problems}


@router.put("/families/{family_id}/options")
async def save_options(family_id: uuid.UUID, body: OptionsIn,
                       current: CurrentUser = Depends(MANAGE),
                       db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    f = await _family(db, family_id)
    try:
        row = await products.save(db, f, body.model_dump(exclude={"note"}), _actor(current),
                                  body.note)
    except ModelProblems as exc:
        raise ValidationFailed(exc.problems) from exc
    return products.model_out(row) or {}


@router.post("/families/{family_id}/sample")
async def use_sample(family_id: uuid.UUID, current: CurrentUser = Depends(MANAGE),
                     db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Start a family's options from the flanges sample (C4 A), to edit from there."""
    f = await _family(db, family_id)
    if await products.current(db, f.id) is not None:
        raise AppError(f"{f.name} already has options")
    row = await products.save(db, f, SAMPLES["flanges"], _actor(current),
                              note="Started from the flanges sample")
    return products.model_out(row) or {}


@router.post("/families/{family_id}/configure")
async def guided(family_id: uuid.UUID, body: ValuesIn, _: CurrentUser = Depends(USE),
                 db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """One step of the guided configurator: what can still be picked, and why not."""
    f = await _family(db, family_id)
    row = await products.current(db, f.id)
    if row is None:
        raise AppError(f"{f.name} has no options set up")
    try:
        st = products.model_of(row).state(body.values)
    except TooComplex as exc:
        raise AppError("The rules are too tangled to work out; ask for them to be "
                       "simplified") from exc
    r = st.result
    return {"family": {"id": str(f.id), "name": f.name}, "version": row.version,
            "attributes": st.attributes, "complete": st.complete, "valid": st.valid,
            "problems": st.problems,
            "result": {"values": r.values, "labels": r.labels, "variant_code": r.variant_code,
                       "fingerprint": r.fingerprint} if r else None}


@router.post("/configurations", status_code=201)
async def save_configuration(body: ConfigIn, current: CurrentUser = Depends(USE),
                             db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    f = await _family(db, body.family_id)
    line = enq = None
    if body.enquiry_line_id:
        if not has_permission(current.permissions, "enquiries.work"):
            raise Forbidden("Changing an enquiry's lines needs the permission to work on "
                            "enquiries")
        line = await db.get(EnquiryLine, body.enquiry_line_id)
        enq = await db.get(Enquiry, line.enquiry_id, with_for_update=True) if line else None
        if line is None or line.removed_at is not None or enq is None \
                or enq.erased_at is not None:
            raise NotFound("Enquiry line not found")
        if enq.status not in OPEN_STATUSES:
            raise AppError("The enquiry is closed; reopen it to change its lines")
    try:
        cfg = await products.configure(db, f, body.values, _actor(current))
    except ModelProblems as exc:
        raise ValidationFailed(exc.problems) from exc
    if line is not None and enq is not None:
        await products.link(db, line, cfg)
        await records.event(db, enq, "line_configured", _actor(current), line=line.position,
                            family=f.name, variant_code=cfg.variant_code)
        # Its draft quote, if one was started already, follows the new configuration.
        changed = await quotes.follow_configuration(db, line, cfg, _actor(current))
        if changed is not None:
            await costing.cost_revision(db, *changed, _actor(current), why="line configured")
    return products.config_out(cfg) or {}


@router.get("/configurations/{config_id}")
async def get_configuration(config_id: uuid.UUID, _: CurrentUser = Depends(USE),
                            db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    cfg = await db.get(Configuration, config_id)
    if cfg is None:
        raise NotFound("Configuration not found")
    return products.config_out(cfg) or {}
