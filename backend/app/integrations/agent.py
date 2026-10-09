"""The ERP Connector agent: every change the integration makes to Forge's accounts or
to the ERP goes through the policy gate as one of its tools, so a tenant can stop it,
lower its level ("ask before every link") or write rules ("ask a person before
creating strategic accounts in ERPNext"), and every action is in the audit chain.

Tools:
  erp.link_customer   link an ERP customer to the account sharing its verified tax ID
  erp.new_account     create an account for an ERP customer that matches nothing
  erp.update_account  bring ERP-owned fields (legal name, GSTIN, PAN, billing address)
                      onto a linked account
  erp.create_customer create an account as a customer in the ERP (the one write, E3)
"""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.controls import account_tiers
from app.agents.registry import (
    L2,
    L3,
    AgentContext,
    AgentDef,
    ToolDef,
    Touches,
    register_agent,
    register_tool,
)
from app.integrations import linking, writes
from app.models import Account, ErpConnection, ErpCustomer, ErpWrite
from app.steward.agent import top_tier


async def _tier(session: AsyncSession, account_id: uuid.UUID | None) -> dict[str, Any]:
    if account_id is None:
        return {}
    tiers = await account_tiers(session, [account_id])
    return {"account_tier": top_tier([tiers.get(account_id)])}


async def _who(session: AsyncSession, ec_id: uuid.UUID) -> tuple[str, str]:
    ec = await session.get(ErpCustomer, ec_id)
    conn = await session.get(ErpConnection, ec.connection_id) if ec else None
    return (ec.name if ec else "?", conn.name if conn else "the ERP")


# --- link -----------------------------------------------------------------------------------

class LinkArgs(BaseModel):
    erp_customer_id: uuid.UUID
    account_id: uuid.UUID
    method: Literal["gstin", "pan", "cin", "manual"]
    score: float | None = None
    reasons: list[str] = Field(default_factory=list)


async def _link_facts(session: AsyncSession, a: LinkArgs) -> dict[str, Any]:
    facts = await _tier(session, a.account_id)
    if a.score is not None:
        facts["match_score"] = a.score
    return facts


async def _link_describe(session: AsyncSession, a: LinkArgs, facts: dict[str, Any]) -> str:
    name, conn = await _who(session, a.erp_customer_id)
    acc = await session.get(Account, a.account_id)
    return f"Link {conn} customer '{name}' to account '{acc.name if acc else '?'}'"


async def _link_run(session: AsyncSession, ctx: AgentContext, a: LinkArgs) -> dict[str, Any]:
    return await linking.link(session, a.erp_customer_id, a.account_id, method=a.method,
                              actor=ctx.approved_by or ctx.actor, reasons=a.reasons, ctx=ctx)


register_tool(ToolDef(
    key=linking.LINK_TOOL,
    title="Link an ERP customer to an account",
    description="Joins a customer in the ERP to the Forge account that shares its verified "
                "tax ID (GSTIN, PAN or CIN), and brings over the ERP's legal and billing "
                "details. Anything that contradicts the account goes to a person.",
    args=LinkArgs, run=_link_run, facts=("match_score", "account_tier"),
    compute_facts=_link_facts,
    touches=lambda s, a: _touch_account(a.account_id), describe=_link_describe,
    permission="accounts.write",
))


async def _touch_account(account_id: uuid.UUID | None) -> Touches:
    return Touches(accounts=(account_id,) if account_id else ())


# --- new account ------------------------------------------------------------------------------

class NewAccountArgs(BaseModel):
    erp_customer_id: uuid.UUID


async def _new_describe(session: AsyncSession, a: NewAccountArgs, facts: dict[str, Any]) -> str:
    name, conn = await _who(session, a.erp_customer_id)
    return f"Create account '{name}' from {conn}"


async def _new_run(session: AsyncSession, ctx: AgentContext, a: NewAccountArgs
                   ) -> dict[str, Any]:
    return await linking.new_account(session, a.erp_customer_id,
                                     actor=ctx.approved_by or ctx.actor, ctx=ctx)


async def _no_touch(session: AsyncSession, a: Any) -> Touches:
    return Touches()


register_tool(ToolDef(
    key=linking.NEW_ACCOUNT_TOOL,
    title="Create an account from an ERP customer",
    description="Creates a Forge account, marked as coming from the ERP, for an ERP "
                "customer that looks like no existing account.",
    args=NewAccountArgs, run=_new_run, touches=_no_touch, describe=_new_describe,
    permission="accounts.write",
))


# --- update account ---------------------------------------------------------------------------

class UpdateArgs(BaseModel):
    erp_customer_id: uuid.UUID
    account_id: uuid.UUID
    changes: dict[str, str | None]


async def _update_describe(session: AsyncSession, a: UpdateArgs, facts: dict[str, Any]) -> str:
    acc = await session.get(Account, a.account_id)
    _, conn = await _who(session, a.erp_customer_id)
    labels = sorted({linking.GROUP_LABELS[g] for g, fields in linking.GROUPS.items()
                     if set(fields) & set(a.changes)})
    return f"Update {', '.join(labels)} of '{acc.name if acc else '?'}' from {conn}"


async def _update_run(session: AsyncSession, ctx: AgentContext, a: UpdateArgs
                      ) -> dict[str, Any]:
    return await linking.update_from_erp(session, a.erp_customer_id, a.account_id, a.changes,
                                         actor=ctx.approved_by or ctx.actor)


register_tool(ToolDef(
    key=linking.UPDATE_TOOL,
    title="Update an account from the ERP",
    description="Brings the ERP's legal name, GSTIN, PAN or billing address onto a linked "
                "account, recording the ERP record as the source.",
    args=UpdateArgs, run=_update_run, facts=("account_tier",),
    compute_facts=lambda s, a: _tier(s, a.account_id),
    touches=lambda s, a: _touch_account(a.account_id), describe=_update_describe,
    permission="accounts.write",
))


# --- create customer in the ERP ---------------------------------------------------------------

class CreateArgs(BaseModel):
    write_id: uuid.UUID
    requested_by: str


async def _write_account(session: AsyncSession, a: CreateArgs) -> uuid.UUID | None:
    return await session.scalar(select(ErpWrite.account_id).where(ErpWrite.id == a.write_id))


async def _create_facts(session: AsyncSession, a: CreateArgs) -> dict[str, Any]:
    return await _tier(session, await _write_account(session, a))


async def _create_touches(session: AsyncSession, a: CreateArgs) -> Touches:
    return await _touch_account(await _write_account(session, a))


async def _create_describe(session: AsyncSession, a: CreateArgs, facts: dict[str, Any]) -> str:
    w = await session.get(ErpWrite, a.write_id)
    acc = await session.get(Account, w.account_id) if w else None
    conn = await session.get(ErpConnection, w.connection_id) if w else None
    return (f"Create '{acc.name if acc else '?'}' as a customer in "
            f"{conn.name if conn else 'the ERP'}")


async def _create_run(session: AsyncSession, ctx: AgentContext, a: CreateArgs
                      ) -> dict[str, Any]:
    return await writes.run_create(session, ctx, a.write_id)


register_tool(ToolDef(
    key=linking.CREATE_TOOL,
    title="Create a customer in the ERP",
    description="Creates a Forge account as a customer in the ERP, after checking that the "
                "ERP has no customer with the same GSTIN, PAN or name. Each request is sent "
                "at most once.",
    args=CreateArgs, run=_create_run, facts=("account_tier",), compute_facts=_create_facts,
    touches=_create_touches, describe=_create_describe, permission="erp.write",
    evidence_class="customer_money",
))


register_agent(AgentDef(
    key=linking.AGENT_KEY,
    name="ERP Connector",
    purpose="Keeps Forge in step with the company's ERP: links ERP customers to the accounts "
            "that share their verified tax ID, creates accounts for customers that match "
            "nothing, brings over the ERP's legal and billing details, and creates customers "
            "in the ERP when asked. Unclear matches and contradictions go to a person.",
    tools=(linking.LINK_TOOL, linking.NEW_ACCOUNT_TOOL, linking.UPDATE_TOOL,
           linking.CREATE_TOOL),
    default_level=L2, max_level=L3,
    triggers=("ERP sync (every 5 minutes)", "a person asks to create a customer in the ERP"),
    model_tier=None,
    permissions=("accounts.read", "accounts.write", "erp.write"),
))
