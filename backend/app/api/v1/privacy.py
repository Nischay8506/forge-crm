"""Erasure requests (decision R6 A): an admin asks, a second person approves on a
decision card, the Privacy agent erases."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import runtime
from app.core.deps import CurrentUser, require_permission
from app.db import get_db
from app.models import Account, Contact, Decision
from app.privacy import erasure
from app.privacy.agent import AGENT_KEY, ERASE_TOOL

router = APIRouter(prefix="/privacy", tags=["privacy"])


def _actor(u: CurrentUser) -> str:
    return f"user:{u.user.id}"


class EraseIn(BaseModel):
    contact_id: uuid.UUID
    reason: str = Field(min_length=5, max_length=500)


def _request(d: Decision, names: dict[uuid.UUID, str]) -> dict[str, Any]:
    return {"decision_id": str(d.id), "status": d.status, "title": d.title,
            "contact_id": d.args.get("contact_id"), "requested_by": d.args.get("requested_by"),
            "requested_at": d.created_at, "decided_by": d.decided_by,
            "decided_at": d.decided_at, "run_id": str(d.run_id) if d.run_id else None,
            "account_name": names.get(d.record_id) if d.record_id else None,
            "result": d.result}


@router.post("/erasures", status_code=201)
async def request_erasure(
    body: EraseIn, current: CurrentUser = Depends(require_permission("privacy.erase")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Ask for a contact's personal data to be erased. A second person approves."""
    await erasure.check_allowed(db, body.contact_id)
    actor = _actor(current)
    ctx = await runtime.start_run(db, current.tenant_id, AGENT_KEY, "erasure_request", actor)
    out = await runtime.call_tool(
        db, ctx, ERASE_TOOL, {"contact_id": body.contact_id, "reason": body.reason,
                              "requested_by": actor},
        recommendation="Erase, unless the law or an open dispute needs this person's data",
        dedupe_key=f"privacy.erase:{body.contact_id}")
    await runtime.finish_run(db, ctx, summary={"status": out.status})
    return {"status": out.status, "decision_id": str(out.decision_id) if out.decision_id
            else None, "reasons": out.reasons, "error": out.error}


@router.get("/erasures")
async def list_erasures(
    _: CurrentUser = Depends(require_permission("privacy.erase")),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    cards = list(await db.scalars(select(Decision).where(Decision.tool_key == ERASE_TOOL)
                                  .order_by(Decision.created_at.desc()).limit(200)))
    contact_ids = [d.record_id for d in cards if d.record_type == "contact" and d.record_id]
    accounts = dict((await db.execute(
        select(Contact.id, Account.name).join(Account, Account.id == Contact.account_id)
        .where(Contact.id.in_(contact_ids)))).all()) if contact_ids else {}
    return [_request(d, accounts) for d in cards]
