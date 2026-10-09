"""Forge's MCP server (decision E9), mounted at /mcp on the backend.

Outside AI tools connect with a token issued on the Integrations page (shown once;
only its hash is stored). Each token belongs to one tenant and acts as the "Outside
AI tools" agent (app/integrations/mcp_tools.py), so every call goes through the
policy gate and the audit chain. A token without write access cannot ask for
writes; with it, a write still waits for a person's approval by default.
"""

import hashlib
import secrets as _random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import load_definitions
from app.agents.runtime import call_tool, finish_run, start_run
from app.audit import chain
from app.config import get_settings
from app.core.errors import AppError
from app.db import system_session, tenant_session
from app.integrations import mcp_tools, writes
from app.models import McpClient

TOKEN_PREFIX = "fmcp_"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_client(session: AsyncSession, tenant_id: uuid.UUID, name: str,
                       allow_write: bool, actor: str) -> tuple[McpClient, str]:
    token = TOKEN_PREFIX + _random.token_urlsafe(32)
    client = McpClient(id=uuid.uuid4(), tenant_id=tenant_id, name=name.strip(),
                       token_hash=_hash(token), token_hint="…" + token[-4:],
                       allow_write=allow_write, created_by=actor)
    session.add(client)
    await session.flush()
    await chain.append(session, tenant_id, "mcp.client.created", actor=actor,
                       data={"client_id": client.id, "name": client.name,
                             "allow_write": allow_write, "token_hint": client.token_hint})
    return client, token


async def client_for_token(token: str) -> McpClient | None:
    """The live client a token belongs to (platform lookup across tenants)."""
    if not token.startswith(TOKEN_PREFIX):
        return None
    async with system_session() as s:
        c = await s.scalar(select(McpClient).where(McpClient.token_hash == _hash(token)))
        if c is None or c.revoked_at is not None:
            return None
        now = datetime.now(UTC)
        if c.last_used_at is None or now - c.last_used_at > timedelta(minutes=1):
            c.last_used_at = now
        s.expunge(c)
        return c


# --- what each MCP tool does, independent of the transport (tested directly) ---------------

async def run_tool(client: McpClient, tool_key: str, args: dict[str, Any]) -> dict[str, Any]:
    load_definitions()
    async with tenant_session(client.tenant_id) as s:
        ctx = await start_run(s, client.tenant_id, mcp_tools.AGENT_KEY, f"mcp:{client.name}",
                              str(client.id))
        outcome = await call_tool(s, ctx, tool_key, args)
        await finish_run(s, ctx, summary={"status": outcome.status})
    if outcome.status == "done":
        return outcome.result
    raise AppError(outcome.error or "; ".join(outcome.reasons) or f"Not allowed ({outcome.status})")


async def create_customer(client: McpClient, account_id: uuid.UUID,
                          connection_id: uuid.UUID) -> dict[str, Any]:
    if not client.allow_write:
        raise AppError("This token may only read. Ask a Forge admin for a token that allows "
                       "writes.")
    load_definitions()
    async with tenant_session(client.tenant_id) as s:
        result = await writes.request_create(
            s, client.tenant_id, connection_id=connection_id, account_id=account_id,
            actor=f"mcp:{client.name}"[:100], agent_key=mcp_tools.AGENT_KEY)
    out = {"outcome": result["outcome"], "status": result["write"]["status"],
           "erp_customer": result["write"]["external_id"]}
    if result["outcome"] == "waiting":
        out["message"] = ("Waiting for a person to approve it in Forge (Waiting for you). "
                          "Asking again returns the same request.")
    if result.get("reasons"):
        out["reasons"] = result["reasons"]
    if result.get("error"):
        out["error"] = result["error"]
    return out


# --- the server ------------------------------------------------------------------------------

def build_server():
    from mcp.server.auth.middleware.auth_context import get_access_token
    from mcp.server.auth.provider import AccessToken
    from mcp.server.auth.settings import AuthSettings
    from mcp.server.mcpserver import MCPServer

    class Verifier:
        async def verify_token(self, token: str) -> AccessToken | None:
            c = await client_for_token(token)
            if c is None:
                return None
            return AccessToken(token=token, client_id=str(c.id),
                               scopes=["read", "write"] if c.allow_write else ["read"],
                               claims={"tenant_id": str(c.tenant_id), "name": c.name})

    async def current() -> McpClient:
        at = get_access_token()
        c = await client_for_token(at.token) if at else None
        if c is None:
            raise AppError("Token missing or revoked")
        return c

    base = get_settings().public_base_url.rstrip("/")
    server = MCPServer(
        name="forge-crm", title="Forge CRM",
        instructions="Look up Forge CRM accounts, their ERP orders and outstanding invoices, "
                     "and ask for an account to be created as a customer in a connected ERP "
                     "(a person approves writes).",
        token_verifier=Verifier(),
        auth=AuthSettings(issuer_url=base, resource_server_url=f"{base}/mcp/",
                          validate_token_resource=False),
    )

    @server.tool(description="Find active accounts by name, GSTIN or PAN.")
    async def find_accounts(query: str, limit: int = 10) -> dict[str, Any]:
        return await run_tool(await current(), mcp_tools.FIND_TOOL,
                              {"query": query, "limit": limit})

    @server.tool(description="An account's latest sales orders from the connected ERPs.")
    async def account_orders(account_id: str, limit: int = 20) -> dict[str, Any]:
        return await run_tool(await current(), mcp_tools.ORDERS_TOOL,
                              {"account_id": account_id, "limit": limit})

    @server.tool(description="An account's unpaid ERP invoices, oldest due first.")
    async def outstanding_invoices(account_id: str, limit: int = 20) -> dict[str, Any]:
        return await run_tool(await current(), mcp_tools.OUTSTANDING_TOOL,
                              {"account_id": account_id, "limit": limit})

    @server.tool(description="Ask for an account to be created as a customer in a connected "
                             "ERP. Refused if the ERP already has it; a person approves it "
                             "in Forge before it is sent.")
    async def create_erp_customer(account_id: str, connection_id: str) -> dict[str, Any]:
        return await create_customer(await current(), uuid.UUID(account_id),
                                     uuid.UUID(connection_id))

    return server
