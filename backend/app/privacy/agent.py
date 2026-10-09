"""The Privacy agent: carries out erasure requests (decision R6 A).

It works at L0 and can never go higher, so every erasure waits on a decision
card. Only people with "privacy.erase" can decide it, and never the person who
asked (four eyes). The erasure itself is app/privacy/erasure.py.
"""

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import (
    L0,
    AgentContext,
    AgentDef,
    ToolDef,
    Touches,
    register_agent,
    register_tool,
)
from app.models import Account, Contact
from app.privacy import erasure

AGENT_KEY = "privacy"
ERASE_TOOL = "privacy.erase_contact"


class EraseArgs(BaseModel):
    contact_id: uuid.UUID
    reason: str = Field(min_length=5, max_length=500)
    requested_by: str


async def _touches(session: AsyncSession, a: EraseArgs) -> Touches:
    c = await session.get(Contact, a.contact_id)
    return Touches(accounts=(c.account_id,) if c and c.account_id else (),
                   records=(("contact", a.contact_id),))


async def _describe(session: AsyncSession, a: EraseArgs, facts: dict[str, Any]) -> str:
    # The card never names the person: it outlives their data.
    c = await session.get(Contact, a.contact_id)
    acc = await session.get(Account, c.account_id) if c and c.account_id else None
    return f"Erase a contact's personal data{f' (contact at {acc.name})' if acc else ''}"


async def _run(session: AsyncSession, ctx: AgentContext, a: EraseArgs) -> dict[str, Any]:
    return await erasure.erase_contact(session, ctx.tenant_id, a.contact_id,
                                       actor=ctx.approved_by or ctx.actor, reason=a.reason,
                                       requested_by=a.requested_by, run_id=ctx.run_id)


register_tool(ToolDef(
    key=ERASE_TOOL,
    title="Erase a contact's personal data",
    description="Removes a contact's name, email, phone and other personal data from Forge "
                "and from the audit trail's details, keeping only what the law requires and "
                "a stub with the contact's id. A second person approves.",
    args=EraseArgs, run=_run, touches=_touches, describe=_describe,
    permission="privacy.erase", decider_permission="privacy.erase", four_eyes=True,
))

register_agent(AgentDef(
    key=AGENT_KEY,
    name="Privacy",
    purpose="Carries out requests to erase a contact's personal data, after a second person "
            "approves. It never acts on its own.",
    tools=(ERASE_TOOL,),
    default_level=L0, max_level=L0,
    triggers=("erasure request",),
    permissions=("privacy.erase",),
))
