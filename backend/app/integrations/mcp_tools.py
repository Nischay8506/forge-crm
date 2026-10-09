"""What outside AI tools may do through Forge's MCP server (decision E9), as the
"Outside AI tools" agent: three reads and the one gated write. Every call goes
through the policy gate and lands in the audit chain like any agent action, so a
tenant can stop outside tools with a kill-switch, and by default (L0) a person
approves every write they ask for.
"""

import uuid
from datetime import date
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import (
    L0,
    L2,
    AgentContext,
    AgentDef,
    ToolDef,
    Touches,
    register_agent,
    register_tool,
)
from app.integrations import linking
from app.models import Account, ErpConnection, ErpCustomer, ErpInvoice, ErpOrder

AGENT_KEY = "outside_ai"
FIND_TOOL = "mcp.find_accounts"
ORDERS_TOOL = "mcp.account_orders"
OUTSTANDING_TOOL = "mcp.outstanding_invoices"


class FindArgs(BaseModel):
    query: str = Field(min_length=2, max_length=100)
    limit: int = Field(default=10, ge=1, le=25)


class AccountArgs(BaseModel):
    account_id: uuid.UUID
    limit: int = Field(default=20, ge=1, le=50)


async def _find(session: AsyncSession, ctx: AgentContext, a: FindArgs) -> dict[str, Any]:
    like = f"%{a.query.strip()}%"
    rows = await session.scalars(select(Account).where(
        Account.status == "active",
        or_(Account.name.ilike(like), Account.gstin == a.query.strip().upper(),
            Account.pan == a.query.strip().upper()))
        .order_by(Account.name).limit(a.limit))
    return {"accounts": [{"id": str(r.id), "name": r.name, "kind": r.kind, "gstin": r.gstin,
                          "city": r.city, "state": r.state, "tier": r.tier} for r in rows]}


async def _codes(session: AsyncSession, account_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
    return [(c.connection_id, c.external_id) for c in await session.scalars(
        select(ErpCustomer).where(ErpCustomer.account_id == account_id))]


async def _orders(session: AsyncSession, ctx: AgentContext, a: AccountArgs) -> dict[str, Any]:
    codes = await _codes(session, a.account_id)
    if not codes:
        return {"orders": [], "note": "This account is not linked to an ERP customer"}
    names = dict((await session.execute(select(ErpConnection.id, ErpConnection.name))).all())
    rows = await session.scalars(select(ErpOrder).where(
        or_(*[(ErpOrder.connection_id == c) & (ErpOrder.customer_external_id == e)
              for c, e in codes]), ErpOrder.gone_at.is_(None))
        .order_by(ErpOrder.order_date.desc()).limit(a.limit))
    return {"orders": [{"erp": names.get(o.connection_id), "number": o.number,
                        "date": o.order_date.isoformat(), "status": o.status,
                        "currency": o.currency, "grand_total": str(o.grand_total),
                        "as_of": o.synced_at.isoformat()} for o in rows]}


async def _outstanding(session: AsyncSession, ctx: AgentContext, a: AccountArgs
                       ) -> dict[str, Any]:
    codes = await _codes(session, a.account_id)
    if not codes:
        return {"invoices": [], "note": "This account is not linked to an ERP customer"}
    names = dict((await session.execute(select(ErpConnection.id, ErpConnection.name))).all())
    rows = await session.scalars(select(ErpInvoice).where(
        or_(*[(ErpInvoice.connection_id == c) & (ErpInvoice.customer_external_id == e)
              for c, e in codes]), ErpInvoice.gone_at.is_(None), ErpInvoice.outstanding > 0,
        ErpInvoice.status.not_in(("draft", "cancelled", "paid")))
        .order_by(ErpInvoice.due_date).limit(a.limit))
    today = date.today()
    return {"invoices": [{"erp": names.get(i.connection_id), "number": i.number,
                          "date": i.invoice_date.isoformat(),
                          "due": i.due_date.isoformat() if i.due_date else None,
                          "overdue": bool(i.due_date and i.due_date < today),
                          "currency": i.currency, "outstanding": str(i.outstanding),
                          "as_of": i.synced_at.isoformat()} for i in rows]}


async def _touch(session: AsyncSession, a: AccountArgs) -> Touches:
    return Touches(accounts=(a.account_id,))


async def _nothing(session: AsyncSession, a: Any) -> Touches:
    return Touches()


async def _desc_find(session: AsyncSession, a: FindArgs, facts: dict[str, Any]) -> str:
    return f"Look up accounts matching '{a.query}'"


register_tool(ToolDef(
    key=FIND_TOOL, title="Find accounts",
    description="Finds active accounts by name, GSTIN or PAN.",
    args=FindArgs, run=_find, side_effect=False, touches=_nothing, describe=_desc_find,
    permission="accounts.read"))


def _account_describer(what: str):
    async def describe(session: AsyncSession, a: AccountArgs, facts: dict[str, Any]) -> str:
        acc = await session.get(Account, a.account_id)
        return f"Read {what} of '{acc.name if acc else '?'}'"
    return describe


register_tool(ToolDef(
    key=ORDERS_TOOL, title="Read an account's ERP orders",
    description="Lists the account's latest sales orders from the connected ERPs, with the "
                "time they were last read.",
    args=AccountArgs, run=_orders, side_effect=False, touches=_touch,
    describe=_account_describer("the ERP orders"), permission="accounts.read"))
register_tool(ToolDef(
    key=OUTSTANDING_TOOL, title="Read an account's outstanding invoices",
    description="Lists the account's unpaid ERP invoices and which are overdue.",
    args=AccountArgs, run=_outstanding, side_effect=False, touches=_touch,
    describe=_account_describer("the outstanding invoices"), permission="accounts.read"))

register_agent(AgentDef(
    key=AGENT_KEY,
    name="Outside AI tools (MCP)",
    purpose="What AI assistants and scripts connected through Forge's MCP server may do: "
            "look up accounts, read an account's ERP orders and outstanding invoices, and ask "
            "for an account to be created as a customer in an ERP. Each tool needs its own "
            "token; writes need a token that allows them and, by default, a person's approval.",
    tools=(FIND_TOOL, ORDERS_TOOL, OUTSTANDING_TOOL, linking.CREATE_TOOL),
    default_level=L0, max_level=L2,
    triggers=("a call from an outside AI tool with a Forge MCP token",),
    model_tier=None,
    permissions=("accounts.read", "erp.write"),
))
