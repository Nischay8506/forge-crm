"""Writing to the ERP (decision E3: "create this account as a customer in ERPNext" is
the one write in Phase 1). Safeguard 1: duplicate validation, authorization checks
and idempotency.

1. Authorization: the person needs erp.write (checked by the API); the agent that
   carries the write needs the tool in its charter and its own erp.write permission,
   and the policy gate applies (kill-switches, takeovers, rules, autonomy level).
2. Duplicate validation, before asking and again right before sending: the account
   must be active and not already linked to a customer of that ERP; neither Forge's
   synced copy nor the ERP itself (asked live) may hold a customer with the same
   GSTIN, PAN or name.
3. Idempotency: one erp_writes row per idempotency key (by default one per
   connection and account). Its ref is stored on the ERP record, so a request whose
   answer was lost is found in the ERP before anything is sent again.

The erp_writes row is changed in its own short transactions, so what was sent to
the ERP is never forgotten even if the surrounding request fails. Every step is in
the audit chain (erp.write.* and the gate's action.* records).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import AGENTS, AgentContext
from app.agents.runtime import ToolOutcome, call_tool, finish_run, start_run
from app.audit import chain
from app.core.errors import AppError, NotFound
from app.db import tenant_session
from app.integrations import linking
from app.integrations.adapter import ConnectorError, CustomerMatch
from app.integrations.connections import adapter_for
from app.integrations.contracts import Entity, NewCustomer
from app.models import Account, ErpConnection, ErpCustomer, ErpWrite
from app.steward.normalize import normalize_name

KIND = "create_customer"


@dataclass
class Refusal(Exception):
    reasons: list[str]

    def __str__(self) -> str:
        return "; ".join(self.reasons)


def default_key(conn_id: uuid.UUID, account_id: uuid.UUID) -> str:
    return f"{KIND}:{conn_id}:{account_id}"


def erp_ref(write_id: uuid.UUID) -> str:
    return f"fw-{write_id.hex}"


def out(w: ErpWrite) -> dict[str, Any]:
    return {"id": str(w.id), "kind": w.kind, "status": w.status, "account_id": str(w.account_id),
            "connection_id": str(w.connection_id), "external_id": w.external_id,
            "error": w.error, "attempts": w.attempts, "requested_by": w.requested_by,
            "decision_id": (w.request or {}).get("decision_id"),
            "created_at": w.created_at, "updated_at": w.updated_at}


# --- duplicate validation -----------------------------------------------------------------

async def duplicates_in_forge(session: AsyncSession, conn: ErpConnection,
                              acc: Account) -> list[str]:
    reasons = []
    if acc.status != "active":
        raise Refusal([f"{acc.name} is {acc.status}; use the account it was merged into"])
    if acc.kind != "entity":
        raise Refusal(["Only a legal entity (not a group or a site) can become an ERP "
                       "customer"])
    linked = await session.scalar(select(ErpCustomer).where(
        ErpCustomer.connection_id == conn.id, ErpCustomer.account_id == acc.id,
        ErpCustomer.gone_at.is_(None)))
    if linked is not None:
        reasons.append(f"{acc.name} is already customer {linked.external_id} in {conn.name}")
    same = [ErpCustomer.name == acc.name]
    if acc.gstin:
        same.append(ErpCustomer.gstin == acc.gstin)
    if acc.pan:
        same.append(ErpCustomer.pan == acc.pan)
    rows = await session.scalars(select(ErpCustomer).where(
        ErpCustomer.connection_id == conn.id, ErpCustomer.gone_at.is_(None), or_(*same)))
    for ec in rows:
        if ec.account_id == acc.id:
            continue
        what = ("GSTIN" if acc.gstin and ec.gstin == acc.gstin else
                "PAN" if acc.pan and ec.pan == acc.pan else "name")
        reasons.append(f"{conn.name} already has customer {ec.external_id} with the same "
                       f"{what}; link it to this account instead of creating another")
    return reasons


def duplicates_in_erp(acc: Account, found: list[CustomerMatch]) -> list[str]:
    target = normalize_name(acc.name)
    reasons = []
    for m in found:
        c = m.customer
        if m.matched_on == "name" and normalize_name(c.name) != target:
            continue
        what = {"gstin": "GSTIN", "name": "name", "ref": "Forge reference"}[m.matched_on]
        reasons.append(f"ERPNext already has customer {c.external_id} with the same {what}")
    return reasons


# --- the erp_writes row, in its own transactions --------------------------------------------

async def _mark(tenant_id: uuid.UUID, write_id: uuid.UUID, **values: Any) -> ErpWrite:
    async with tenant_session(tenant_id) as s:
        w = await s.get(ErpWrite, write_id, with_for_update=True)
        for k, v in values.items():
            setattr(w, k, v)
        w.updated_at = datetime.now(UTC)
        await s.flush()
        s.expunge(w)
        return w


async def _open_write(tenant_id: uuid.UUID, conn_id: uuid.UUID, account_id: uuid.UUID,
                      key: str, actor: str, request: dict[str, Any]) -> tuple[ErpWrite, bool]:
    """Find the write for this key or create it. Returns (write, is_new_attempt)."""
    async with tenant_session(tenant_id) as s:
        w = await s.scalar(select(ErpWrite).where(ErpWrite.idempotency_key == key)
                           .with_for_update())
        if w is not None and (w.connection_id != conn_id or w.account_id != account_id):
            raise AppError("This idempotency key was already used for a different request")
        if w is None:
            w = ErpWrite(id=uuid.uuid4(), tenant_id=tenant_id, connection_id=conn_id, kind=KIND,
                         idempotency_key=key, account_id=account_id, status="pending",
                         request=request, attempts=0, requested_by=actor)
            s.add(w)
            await s.flush()
            s.expunge(w)
            return w, True
        if w.status in ("failed", "refused"):
            w.request = {**request, "previous_error": w.error}
            w.status, w.error, w.requested_by = "pending", None, actor
            await s.flush()
            s.expunge(w)
            return w, True
        s.expunge(w)
        return w, False


# --- asking for the write ------------------------------------------------------------------

async def request_create(session: AsyncSession, tenant_id: uuid.UUID, *, connection_id: uuid.UUID,
                         account_id: uuid.UUID, actor: str, agent_key: str = linking.AGENT_KEY,
                         idempotency_key: str | None = None) -> dict[str, Any]:
    """Ask for an account to be created as a customer in an ERP. Returns the write and
    what happened: done, waiting (a person must approve), refused, blocked or failed."""
    conn = await session.get(ErpConnection, connection_id)
    acc = await session.get(Account, account_id)
    if conn is None or acc is None:
        raise NotFound("Connection or account not found")
    if conn.status != "active":
        raise AppError(f"{conn.name} is paused; resume it first")
    key = (idempotency_key or default_key(conn.id, acc.id)).strip()[:120]
    if len(key) < 8:
        raise AppError("Idempotency key must be at least 8 characters")
    w, new_attempt = await _open_write(tenant_id, conn.id, acc.id, key, actor,
                                       {"account": acc.name, "connection": conn.name})
    if not new_attempt and w.status in ("succeeded", "sending", "pending", "unknown"):
        if w.status == "unknown":
            new_attempt = True  # look the customer up by its ref before trying again
        else:
            return {"outcome": "already", "write": out(w)}
    await chain.append(session, tenant_id, "erp.write.requested", actor=actor,
                       record_type="account", record_id=acc.id,
                       data={"write_id": w.id, "kind": KIND, "connection": conn.name,
                             "idempotency_key": key, "attempt": w.attempts + 1,
                             "via_agent": agent_key})
    try:
        reasons = await duplicates_in_forge(session, conn, acc)
    except Refusal as r:
        reasons = r.reasons
    if reasons:
        return await _refuse(session, w, acc, actor, reasons)
    ctx = await start_run(session, tenant_id, agent_key, f"erp write requested by {actor}",
                          str(w.id))
    outcome: ToolOutcome = await call_tool(
        session, ctx, linking.CREATE_TOOL, {"write_id": w.id, "requested_by": actor},
        recommendation=f"Create {acc.name} as a customer in {conn.name}",
        dedupe_key=f"erp.create:{w.id}")
    await finish_run(session, ctx, summary={"status": outcome.status})
    if outcome.status == "waiting":
        w = await _mark(tenant_id, w.id, status="pending",
                        request={**w.request, "decision_id": str(outcome.decision_id)})
    elif outcome.status == "blocked":
        w = await _mark(tenant_id, w.id, status="refused", error="; ".join(outcome.reasons)[:1000])
    else:
        async with tenant_session(tenant_id) as s:
            w = await s.get(ErpWrite, w.id)
            s.expunge(w)
    return {"outcome": outcome.status, "write": out(w), "summary": outcome.summary,
            "reasons": outcome.reasons, "error": outcome.error,
            "decision_id": str(outcome.decision_id) if outcome.decision_id else None}


async def _refuse(session: AsyncSession, w: ErpWrite, acc: Account, actor: str,
                  reasons: list[str]) -> dict[str, Any]:
    w = await _mark(w.tenant_id, w.id, status="refused", error="; ".join(reasons)[:1000])
    await chain.append(session, w.tenant_id, "erp.write.refused", actor=actor,
                       record_type="account", record_id=acc.id,
                       data={"write_id": w.id, "reasons": reasons})
    return {"outcome": "refused", "write": out(w), "reasons": reasons}


# --- the tool body (runs once the gate allows it, or after a person approves) -------------

async def run_create(session: AsyncSession, ctx: AgentContext, write_id: uuid.UUID
                     ) -> dict[str, Any]:
    w = await session.get(ErpWrite, write_id)
    if w is None:
        raise NotFound("ERP write not found")
    await session.refresh(w)
    if w.status == "succeeded":
        return {"write_id": str(w.id), "external_id": w.external_id, "already": True}
    conn = await session.get(ErpConnection, w.connection_id)
    acc = await session.get(Account, w.account_id)
    actor = ctx.approved_by or ctx.actor
    # Checked again: things may have changed while a person was deciding.
    try:
        reasons = await duplicates_in_forge(session, conn, acc)
    except Refusal as r:
        reasons = r.reasons
    if reasons:
        await _mark(w.tenant_id, w.id, status="refused", error="; ".join(reasons)[:1000])
        raise AppError("Not created: " + "; ".join(reasons))
    ref = erp_ref(w.id)
    try:
        adapter = adapter_for(conn)
    except ConnectorError as e:
        await _mark(w.tenant_id, w.id, status="failed", error=e.message[:1000])
        raise AppError(f"Not sent: {e.message}") from e
    try:
        found = await adapter.find_customers(ref=ref, gstin=acc.gstin, name=acc.name)
        ours = [m.customer for m in found if m.matched_on == "ref"]
        if ours:  # sent before, answer lost: it is there, adopt it
            customer = ours[0]
        else:
            reasons = duplicates_in_erp(acc, found)
            if reasons:
                await _mark(w.tenant_id, w.id, status="refused", error="; ".join(reasons)[:1000])
                raise AppError("Not created: " + "; ".join(reasons))
            await _mark(w.tenant_id, w.id, status="sending", attempts=w.attempts + 1)
            customer = await adapter.create_customer(NewCustomer(
                ref=ref, name=acc.name[:140], gstin=acc.gstin))
    except ConnectorError as e:
        lost = e.retryable  # it may or may not have arrived
        await _mark(w.tenant_id, w.id, status="unknown" if lost else "failed",
                    error=e.message[:1000])
        raise AppError(
            (f"ERPNext did not answer ({e.message}). Forge will look for the customer before "
             "trying again, so it cannot be created twice.") if lost
            else f"ERPNext refused it: {e.message}") from e
    finally:
        await adapter.aclose()
    await _mark(w.tenant_id, w.id, status="succeeded", external_id=customer.external_id,
                error=None)
    # Store and link the new customer now rather than on the next sync.
    from app.integrations.store import DbSink

    sink = DbSink(session, w.tenant_id, conn.id)
    await sink.put(Entity.customer, [customer])
    ec = await session.scalar(select(ErpCustomer).where(
        ErpCustomer.connection_id == conn.id, ErpCustomer.external_id == customer.external_id))
    if ec is not None and ec.account_id is None:
        await linking.link(session, ec.id, acc.id, method="created_in_erp", actor=actor,
                           reasons=[f"Created in {conn.name} from Forge"], ctx=ctx)
    await chain.append(session, w.tenant_id, "erp.write.succeeded", actor=actor,
                       agent_key=ctx.agent.key, run_id=ctx.run_id, record_type="account",
                       record_id=acc.id, data={"write_id": w.id, "connection": conn.name,
                                               "external_id": customer.external_id,
                                               "adopted": bool(ours)})
    return {"write_id": str(w.id), "external_id": customer.external_id,
            "connection": conn.name, "adopted_after_lost_answer": bool(ours)}


def agent_ctx(tenant_id: uuid.UUID, agent_key: str = linking.AGENT_KEY) -> AgentContext:
    return AgentContext(tenant_id=tenant_id, agent=AGENTS[agent_key])
