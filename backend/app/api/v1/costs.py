"""Costs (F3.2, decision C3 A): material and machine-hour rates with the date they apply
from. A new rate never changes an old one; the answer lists the open quotes costed with
the rate it replaces, so they can be looked at again."""

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_permission
from app.cpq import quotes, rates
from app.db import get_db

router = APIRouter(prefix="/costs", tags=["costs"])

VIEW = require_permission("costs.view")
MANAGE = require_permission("costs.manage")


def _actor(u: CurrentUser) -> str:
    return f"user:{u.user.id}"


class MaterialIn(BaseModel):
    code: str = Field(max_length=40)
    name: str = Field(max_length=120)
    unit: str = Field(default="kg", max_length=10)
    rate: str = Field(max_length=30)
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
            "affected_quotes": await rates.affected_quotes(db, "material", row.code)}


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
            "affected_quotes": await rates.affected_quotes(db, "machine", row.code)}
