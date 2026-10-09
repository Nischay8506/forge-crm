"""Integrations (F12.6): ERP connections and their health, the setup wizard's test,
syncs on request, dead letters, the mapping studio (customers and code lists), ERP
data on the account page, the gated "create customer in ERPNext", webhooks, and
tokens for outside AI tools (MCP).

Credentials go in and never come out: responses carry the key's last four
characters only.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import load_definitions
from app.audit import chain
from app.core import secrets
from app.core.deps import CurrentUser, require_permission
from app.core.errors import AppError, Forbidden, NotFound
from app.core.permissions import has_permission
from app.db import get_db, system_session, tenant_session
from app.integrations import connections as cx
from app.integrations import linking, mapping, writes
from app.integrations.adapter import CAUSES, ConnectorError
from app.integrations.connections import ENTITY_ORDER, problems_for
from app.integrations.contracts import ENTITY_LABELS, Entity
from app.integrations.runner import sync_dead_letter_task
from app.integrations.store import DbSink
from app.models import (
    Account,
    CodeMap,
    CodeMapHistory,
    Decision,
    ErpConnection,
    ErpCustomer,
    ErpInvoice,
    ErpOrder,
    ErpReceipt,
    ErpWrite,
    ForgeCode,
    McpClient,
    SyncDeadLetter,
    SyncFlow,
    SyncRun,
)

router = APIRouter(prefix="/integrations", tags=["integrations"])
load_definitions()

READ = require_permission("integrations.read")
MANAGE = require_permission("integrations.manage")
MAP = require_permission("integrations.map")


def _actor(u: CurrentUser) -> str:
    return f"user:{u.user.id}"


def _iso(v: Any) -> Any:
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


async def _conn(db: AsyncSession, conn_id: uuid.UUID) -> ErpConnection:
    conn = await db.get(ErpConnection, conn_id)
    if conn is None:
        raise NotFound("Connection not found")
    return conn


def _flow(f: SyncFlow) -> dict[str, Any]:
    return {
        "id": str(f.id), "entity": f.entity, "label": ENTITY_LABELS[Entity(f.entity)],
        "enabled": f.enabled, "running_since": f.running_since,
        "last_started_at": f.last_started_at, "last_finished_at": f.last_finished_at,
        "last_success_at": f.last_success_at, "last_error_cause": f.last_error_cause,
        "last_error_message": f.last_error_message,
        "consecutive_failures": f.consecutive_failures, "records_read": f.records_read,
        "reconciled_at": f.reconciled_at, "last_reconcile": f.last_reconcile,
        "run_requested_at": f.run_requested_at,
        "resuming": bool((f.state or {}).get("cursor")),
    }


async def _summary(db: AsyncSession, conn: ErpConnection) -> dict[str, Any]:
    flows = await cx.flows_of(db, conn)
    problems = problems_for(conn, flows, datetime.now(UTC))
    alert = await db.scalar(select(Decision).where(
        Decision.dedupe_key == f"alert:conn:{conn.id}",
        Decision.status.in_(("open", "acknowledged"))))
    dead = await db.scalar(select(func.count()).select_from(SyncDeadLetter).where(
        SyncDeadLetter.connection_id == conn.id, SyncDeadLetter.status == "open")) or 0
    review = await db.scalar(select(func.count()).select_from(ErpCustomer).where(
        ErpCustomer.connection_id == conn.id, ErpCustomer.link_status == "review",
        ErpCustomer.gone_at.is_(None))) or 0
    unmapped = await db.scalar(select(func.count()).select_from(CodeMap).where(
        CodeMap.connection_id == conn.id, CodeMap.status != "confirmed")) or 0
    return {
        **cx.public(conn),
        "health": "paused" if conn.status != "active" else ("problem" if problems else "ok"),
        "problems": [{"code": p.code, "title": p.title, "detail": p.detail, "fix": p.fix,
                      **{k: v for k, v in p.data.items()}} for p in
                     sorted(problems, key=lambda p: -p.severity)],
        "alert_id": str(alert.id) if alert else None,
        "last_check": {"at": conn.last_check_at, "ok": conn.last_check_ok,
                       "cause": conn.last_check_cause, "message": conn.last_check_message},
        "last_ok_at": conn.last_ok_at, "about": conn.about,
        "flows": [_flow(f) for f in flows],
        "dead_letters": dead, "customers_to_review": review, "codes_to_map": unmapped,
        "webhook": bool(conn.webhook_token_hash),
        "created_at": conn.created_at, "created_by": conn.created_by,
    }


# --- overview, test, create ---------------------------------------------------------------

@router.get("")
async def overview(current: CurrentUser = Depends(READ), db: AsyncSession = Depends(get_db)
                   ) -> dict[str, Any]:
    conns = list(await db.scalars(select(ErpConnection).order_by(ErpConnection.created_at)))
    return {"credential_keys_configured": secrets.configured(),
            "kinds": [{"kind": k, "label": a.label} for k, a in cx.KINDS.items()],
            "entities": [{"entity": e.value, "label": ENTITY_LABELS[e]} for e in ENTITY_ORDER],
            "connections": [await _summary(db, c) for c in conns]}


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["erpnext"] = "erpnext"
    base_url: str = Field(max_length=500)
    api_key: str = Field(min_length=1, max_length=200)
    api_secret: str = Field(min_length=1, max_length=200)
    settings: dict[str, str | None] = Field(default_factory=dict)

    def __repr__(self) -> str:  # never print the credentials
        return f"LoginIn(kind={self.kind!r}, base_url={self.base_url!r})"


@router.post("/test")
async def test_login(body: LoginIn, current: CurrentUser = Depends(MANAGE)) -> dict[str, Any]:
    """The wizard's "Test connection": logs in, reads the time zone and checks read
    access to every record type, without saving anything."""
    check = await cx.test_login(body.kind, body.base_url, body.api_key, body.api_secret,
                                body.settings)
    return {"ok": check.ok, "cause": check.cause, "message": check.message,
            "title": cx.CAUSE_TITLES.get(check.cause or "", None),
            "fix": cx.CAUSE_FIXES.get(check.cause or "", None), "about": check.about}


class ConnectionIn(LoginIn):
    name: str = Field(min_length=1, max_length=100)
    entities: list[Entity] | None = None
    start_sync: bool = True


@router.post("/connections", status_code=201)
async def create(body: ConnectionIn, current: CurrentUser = Depends(MANAGE),
                 db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    conn = await cx.create_connection(
        db, current.tenant_id, kind=body.kind, name=body.name, base_url=body.base_url,
        api_key=body.api_key, api_secret=body.api_secret, settings=body.settings,
        actor=_actor(current), entities=[e.value for e in body.entities] if body.entities
        else None)
    await cx.check_connection(db, conn, full=True)
    if body.start_sync:
        now = datetime.now(UTC)
        for f in await cx.flows_of(db, conn):
            if f.enabled:
                f.run_requested_at = now
    await db.flush()
    return await _summary(db, conn)


@router.get("/connections/{conn_id}")
async def detail(conn_id: uuid.UUID, current: CurrentUser = Depends(READ),
                 db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    conn = await _conn(db, conn_id)
    runs = list(await db.scalars(
        select(SyncRun).join(SyncFlow, SyncFlow.id == SyncRun.flow_id)
        .where(SyncFlow.connection_id == conn.id).order_by(SyncRun.started_at.desc()).limit(30)))
    flows = {f.id: f.entity for f in await cx.flows_of(db, conn)}
    return {**await _summary(db, conn), "runs": [{
        "id": str(r.id), "entity": flows.get(r.flow_id), "kind": r.kind, "trigger": r.trigger,
        "status": r.status, "started_at": r.started_at, "finished_at": r.finished_at,
        "read": r.read, "problems": r.problems, "retries": r.retries,
        "error_cause": r.error_cause, "error_message": r.error_message, "details": r.details,
    } for r in runs]}


class ConnectionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=100)
    base_url: str | None = Field(default=None, max_length=500)
    settings: dict[str, str | None] | None = None
    status: Literal["active", "paused"] | None = None


@router.patch("/connections/{conn_id}")
async def update(conn_id: uuid.UUID, body: ConnectionPatch, current: CurrentUser = Depends(MANAGE),
                 db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    conn = await _conn(db, conn_id)
    await cx.update_connection(db, conn, body.model_dump(exclude_unset=True), _actor(current))
    await cx.refresh_health(db, conn)
    return await _summary(db, conn)


class CredentialsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str = Field(min_length=1, max_length=200)
    api_secret: str = Field(min_length=1, max_length=200)

    def __repr__(self) -> str:
        return "CredentialsIn(...)"


@router.put("/connections/{conn_id}/credentials")
async def change_credentials(conn_id: uuid.UUID, body: CredentialsIn,
                             current: CurrentUser = Depends(MANAGE),
                             db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    conn = await _conn(db, conn_id)
    await cx.change_credentials(db, conn, body.api_key, body.api_secret, _actor(current))
    await cx.check_connection(db, conn, full=True)
    return await _summary(db, conn)


@router.post("/connections/{conn_id}/check")
async def check(conn_id: uuid.UUID, current: CurrentUser = Depends(MANAGE),
                db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    conn = await _conn(db, conn_id)
    result = await cx.check_connection(db, conn, full=True)
    return {"ok": result.ok, "cause": result.cause, "message": result.message,
            "about": result.about, "connection": await _summary(db, conn)}


class SyncIn(BaseModel):
    entities: list[Entity] | None = None
    reconcile: bool = False


@router.post("/connections/{conn_id}/sync")
async def sync_now(conn_id: uuid.UUID, body: SyncIn | None = None,
                   current: CurrentUser = Depends(MANAGE),
                   db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Start a sync (or the comparison) now. The worker picks it up within a minute."""
    conn = await _conn(db, conn_id)
    if conn.status != "active":
        raise AppError(f"{conn.name} is paused; resume it first")
    body = body or SyncIn()
    wanted = {e.value for e in body.entities} if body.entities else None
    now = datetime.now(UTC)
    started = []
    for f in await cx.flows_of(db, conn):
        if f.enabled and (wanted is None or f.entity in wanted):
            if body.reconcile:
                enqueue("reconcile", conn.tenant_id, f.id, "manual")
            else:
                f.run_requested_at = now  # the worker's next tick (within a minute) starts it
            started.append(f.entity)
    await chain.append(db, conn.tenant_id, "integration.sync.requested", actor=_actor(current),
                       record_type="erp_connection", record_id=conn.id,
                       data={"entities": started, "reconcile": body.reconcile})
    return {"requested": started}


class FlowPatch(BaseModel):
    enabled: bool


@router.patch("/flows/{flow_id}")
async def set_flow(flow_id: uuid.UUID, body: FlowPatch, current: CurrentUser = Depends(MANAGE),
                   db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    flow = await db.get(SyncFlow, flow_id)
    if flow is None:
        raise NotFound("Flow not found")
    await cx.set_flow(db, flow, body.enabled, _actor(current))
    return _flow(flow)


@router.post("/connections/{conn_id}/webhook-token")
async def webhook_token(conn_id: uuid.UUID, request: Request,
                        current: CurrentUser = Depends(MANAGE),
                        db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """A new secret for ERPNext webhooks, shown once. ERPNext sends it in the
    X-Forge-Webhook-Token header; the old one stops working."""
    conn = await _conn(db, conn_id)
    token = await cx.issue_webhook_token(db, conn, _actor(current))
    base = str(request.base_url).rstrip("/")
    return {"token": token, "url": f"{base}/api/v1/integrations/webhooks/{conn.id}",
            "header": "X-Forge-Webhook-Token"}


def enqueue(what: str, tenant_id: uuid.UUID, flow_id: uuid.UUID, trigger: str) -> None:
    """Hand a run to the worker (replaced in tests)."""
    from app.integrations.runner import _celery_enqueue

    _celery_enqueue(what, tenant_id, flow_id, trigger)


DOCTYPE_ENTITY = {"Customer": "customer", "Address": "customer", "Item": "item",
                  "Item Price": "price", "Sales Order": "order", "Sales Invoice": "invoice",
                  "Payment Entry": "receipt", "Bin": "stock", "Stock Ledger Entry": "stock"}


@router.post("/webhooks/{conn_id}", status_code=202)
async def webhook(conn_id: uuid.UUID, request: Request,
                  x_forge_webhook_token: str | None = Header(default=None)) -> dict[str, Any]:
    """ERPNext says something changed. The body is not trusted: it only says which
    record type to read sooner; the data itself always comes from the normal sync."""
    async with system_session() as s:
        row = (await s.execute(select(ErpConnection.tenant_id, ErpConnection.webhook_token_hash)
                               .where(ErpConnection.id == conn_id))).first()
    if row is None:
        raise NotFound("Not found")
    async with tenant_session(row.tenant_id) as s:
        conn = await s.get(ErpConnection, conn_id)
        if not cx.webhook_token_ok(conn, x_forge_webhook_token):
            raise Forbidden("Webhook token missing or wrong")
        try:
            body = await request.json()
        except ValueError:
            body = {}
        doctype = body.get("doctype") if isinstance(body, dict) else None
        entity = DOCTYPE_ENTITY.get(doctype) if isinstance(doctype, str) else None
        now = datetime.now(UTC)
        nudged = []
        for f in await cx.flows_of(s, conn):
            if f.enabled and (entity is None or f.entity == entity):
                f.run_requested_at = now
                nudged.append(f.entity)
    return {"accepted": True, "entities": nudged}


# --- dead letters ---------------------------------------------------------------------------

def _dead(d: SyncDeadLetter) -> dict[str, Any]:
    return {"id": str(d.id), "entity": d.entity, "label": ENTITY_LABELS[Entity(d.entity)],
            "external_id": d.external_id, "reason": d.reason, "status": d.status,
            "attempts": d.attempts, "first_seen_at": d.first_seen_at,
            "last_seen_at": d.last_seen_at, "resolved_by": d.resolved_by,
            "resolved_at": d.resolved_at, "note": d.note, "raw": d.raw}


@router.get("/connections/{conn_id}/dead-letters")
async def dead_letters(conn_id: uuid.UUID, status: Literal["open", "resolved", "skipped",
                                                            "all"] = "open",
                       current: CurrentUser = Depends(READ),
                       db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    stmt = select(SyncDeadLetter).where(SyncDeadLetter.connection_id == conn_id)
    if status != "all":
        stmt = stmt.where(SyncDeadLetter.status == status)
    rows = await db.scalars(stmt.order_by(SyncDeadLetter.last_seen_at.desc()).limit(500))
    return [_dead(d) for d in rows]


async def _dead_or_404(db: AsyncSession, dead_id: uuid.UUID) -> SyncDeadLetter:
    d = await db.get(SyncDeadLetter, dead_id)
    if d is None:
        raise NotFound("Record not found")
    if d.status != "open":
        raise AppError(f"This record is already {d.status}")
    return d


async def _after_dead(db: AsyncSession, d: SyncDeadLetter) -> None:
    flow = await db.scalar(select(SyncFlow).where(SyncFlow.connection_id == d.connection_id,
                                                  SyncFlow.entity == d.entity))
    conn = await db.get(ErpConnection, d.connection_id)
    if flow is not None:
        await sync_dead_letter_task(db, flow, conn)


@router.post("/dead-letters/{dead_id}/retry")
async def retry_dead(dead_id: uuid.UUID, current: CurrentUser = Depends(MAP),
                     db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Read the record from the ERP again (after it was fixed there, or a mapping)."""
    d = await _dead_or_404(db, dead_id)
    if not d.external_id:
        raise AppError("This record has no id to read again; skip it instead")
    conn = await db.get(ErpConnection, d.connection_id)
    adapter = cx.adapter_for(conn)
    try:
        record = await adapter.fetch(Entity(d.entity), d.external_id)
    except ConnectorError as e:
        d.attempts, d.last_seen_at, d.reason = d.attempts + 1, datetime.now(UTC), e.message
        await chain.append(db, d.tenant_id, "integration.record.retried", actor=_actor(current),
                           record_type="erp_connection", record_id=conn.id,
                           data={"entity": d.entity, "external_id": d.external_id, "ok": False,
                                 "reason": e.message})
        return {"ok": False, "reason": e.message, "record": _dead(d)}
    finally:
        await adapter.aclose()
    if record is None:
        d.status, d.resolved_by, d.resolved_at = "resolved", _actor(current), datetime.now(UTC)
        d.note = "No longer in the ERP"
    else:
        sink = DbSink(db, d.tenant_id, d.connection_id)
        await sink.put(Entity(d.entity), [record])
        await db.refresh(d)
        if d.status == "open":
            d.status, d.resolved_by, d.resolved_at = "resolved", _actor(current), datetime.now(UTC)
        if Entity(d.entity) == Entity.customer and sink.changed[Entity.customer]:
            await linking.process_changed(db, conn, sink.changed[Entity.customer],
                                          trigger="retry")
    await chain.append(db, d.tenant_id, "integration.record.retried", actor=_actor(current),
                       record_type="erp_connection", record_id=conn.id,
                       data={"entity": d.entity, "external_id": d.external_id, "ok": True})
    await _after_dead(db, d)
    return {"ok": True, "record": _dead(d)}


class NoteIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


@router.post("/dead-letters/{dead_id}/skip")
async def skip_dead(dead_id: uuid.UUID, body: NoteIn | None = None,
                    current: CurrentUser = Depends(MAP),
                    db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    d = await _dead_or_404(db, dead_id)
    note = (body.note if body else None) or "Skipped by a person"
    d.status, d.resolved_by, d.resolved_at, d.note = ("skipped", _actor(current),
                                                      datetime.now(UTC), note)
    await chain.append(db, d.tenant_id, "integration.record.skipped", actor=_actor(current),
                       record_type="erp_connection", record_id=d.connection_id,
                       data={"entity": d.entity, "external_id": d.external_id, "note": note})
    await _after_dead(db, d)
    return _dead(d)


# --- mapping studio: customers --------------------------------------------------------------

async def _names(db: AsyncSession, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return dict((await db.execute(select(Account.id, Account.name)
                                  .where(Account.id.in_(ids)))).all())


def _customer(c: ErpCustomer, names: dict[uuid.UUID, str], conns: dict[uuid.UUID, str]
              ) -> dict[str, Any]:
    return {"id": str(c.id), "connection_id": str(c.connection_id),
            "connection": conns.get(c.connection_id), "external_id": c.external_id,
            "name": c.name, "gstin": c.gstin, "pan": c.pan, "customer_group": c.customer_group,
            "territory": c.territory, "payment_terms": c.payment_terms,
            "credit_limit": _iso(c.credit_limit), "currency": c.currency,
            "city": ((c.data or {}).get("billing_address") or {}).get("city"),
            "disabled": c.disabled, "gone": c.gone_at is not None, "synced_at": c.synced_at,
            "account_id": str(c.account_id) if c.account_id else None,
            "account_name": names.get(c.account_id), "link_status": c.link_status,
            "link_method": c.link_method, "link_reasons": c.link_reasons,
            "candidates": c.candidates, "linked_by": c.linked_by, "linked_at": c.linked_at}


@router.get("/customers")
async def customers(connection_id: uuid.UUID | None = None,
                    status: Literal["review", "pending", "linked", "ignored", "all"] = "review",
                    q: str | None = None, limit: int = Query(100, le=500), offset: int = 0,
                    current: CurrentUser = Depends(READ),
                    db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    stmt = select(ErpCustomer).where(ErpCustomer.gone_at.is_(None))
    if connection_id:
        stmt = stmt.where(ErpCustomer.connection_id == connection_id)
    if status != "all":
        stmt = stmt.where(ErpCustomer.link_status == status)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(ErpCustomer.name.ilike(like), ErpCustomer.external_id.ilike(like),
                              ErpCustomer.gstin.ilike(like)))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(await db.scalars(stmt.order_by(ErpCustomer.name).limit(limit).offset(offset)))
    names = await _names(db, {r.account_id for r in rows})
    conns = dict((await db.execute(select(ErpConnection.id, ErpConnection.name))).all())
    counts = dict((await db.execute(
        select(ErpCustomer.link_status, func.count()).where(ErpCustomer.gone_at.is_(None),
                                                            *([ErpCustomer.connection_id ==
                                                               connection_id]
                                                              if connection_id else []))
        .group_by(ErpCustomer.link_status))).all())
    return {"total": total, "counts": counts,
            "items": [_customer(r, names, conns) for r in rows]}


async def _ec(db: AsyncSession, ec_id: uuid.UUID) -> ErpCustomer:
    ec = await db.get(ErpCustomer, ec_id)
    if ec is None:
        raise NotFound("ERP customer not found")
    return ec


async def _one_customer(db: AsyncSession, ec: ErpCustomer) -> dict[str, Any]:
    await db.flush()
    await db.refresh(ec)
    conn = await db.get(ErpConnection, ec.connection_id)
    return _customer(ec, await _names(db, {ec.account_id}), {conn.id: conn.name})


class LinkIn(BaseModel):
    account_id: uuid.UUID
    note: str | None = Field(default=None, max_length=500)


@router.post("/customers/{ec_id}/link")
async def link_customer(ec_id: uuid.UUID, body: LinkIn, current: CurrentUser = Depends(MAP),
                        db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    ec = await _ec(db, ec_id)
    await linking.link_by_person(db, ec, body.account_id, _actor(current), body.note)
    return await _one_customer(db, ec)


@router.post("/customers/{ec_id}/new-account")
async def customer_new_account(ec_id: uuid.UUID, current: CurrentUser = Depends(MAP),
                               db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    if not has_permission(current.permissions, "accounts.write"):
        raise Forbidden("Missing permission: accounts.write")
    ec = await _ec(db, ec_id)
    await linking.new_account_by_person(db, ec, _actor(current))
    return await _one_customer(db, ec)


@router.post("/customers/{ec_id}/ignore")
async def ignore_customer(ec_id: uuid.UUID, body: NoteIn | None = None,
                          current: CurrentUser = Depends(MAP),
                          db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    ec = await _ec(db, ec_id)
    await linking.ignore_by_person(db, ec, _actor(current), body.note if body else None)
    return await _one_customer(db, ec)


@router.post("/customers/{ec_id}/unlink")
async def unlink_customer(ec_id: uuid.UUID, body: NoteIn | None = None,
                          current: CurrentUser = Depends(MAP),
                          db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    ec = await _ec(db, ec_id)
    await linking.unlink_by_person(db, ec, _actor(current), body.note if body else None)
    return await _one_customer(db, ec)


# --- mapping studio: code lists -------------------------------------------------------------

def _code(m: CodeMap, labels: dict[tuple[str, str], str], conns: dict[uuid.UUID, str]
          ) -> dict[str, Any]:
    return {"id": str(m.id), "connection_id": str(m.connection_id),
            "connection": conns.get(m.connection_id), "list": m.list, "erp_code": m.erp_code,
            "forge_code": m.forge_code, "forge_label": labels.get((m.list, m.forge_code or "")),
            "status": m.status, "suggestion_source": m.suggestion_source,
            "confidence": _iso(m.confidence), "version": m.version, "updated_by": m.updated_by,
            "updated_at": m.updated_at}


@router.get("/codes")
async def codes(connection_id: uuid.UUID | None = None,
                list_: str | None = Query(default=None, alias="list"),
                status: Literal["suggested", "unmapped", "confirmed", "open", "all"] = "open",
                current: CurrentUser = Depends(READ),
                db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    stmt = select(CodeMap)
    if connection_id:
        stmt = stmt.where(CodeMap.connection_id == connection_id)
    if list_:
        stmt = stmt.where(CodeMap.list == list_)
    if status == "open":
        stmt = stmt.where(CodeMap.status != "confirmed")
    elif status != "all":
        stmt = stmt.where(CodeMap.status == status)
    rows = list(await db.scalars(stmt.order_by(CodeMap.list, CodeMap.erp_code).limit(2000)))
    labels = {(r.list, r.code): r.label for r in await db.execute(
        select(ForgeCode.list, ForgeCode.code, ForgeCode.label))}
    conns = dict((await db.execute(select(ErpConnection.id, ErpConnection.name))).all())
    counts = {}
    for lst, st, n in (await db.execute(
            select(CodeMap.list, CodeMap.status, func.count())
            .where(*([CodeMap.connection_id == connection_id] if connection_id else []))
            .group_by(CodeMap.list, CodeMap.status))).all():
        counts.setdefault(lst, {})[st] = n
    return {"lists": [{"list": k, "label": v, "standard": k in mapping.STANDARD_LISTS,
                       "counts": counts.get(k, {})} for k, v in mapping.LISTS.items()],
            "items": [_code(r, labels, conns) for r in rows]}


@router.get("/forge-codes")
async def forge_codes(list_: str = Query(alias="list"), current: CurrentUser = Depends(READ),
                      db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    if list_ not in mapping.LISTS:
        raise NotFound("Unknown list")
    await mapping.ensure_standard(db, current.tenant_id, list_)
    rows = await db.scalars(select(ForgeCode).where(ForgeCode.list == list_,
                                                    ForgeCode.active.is_(True))
                            .order_by(ForgeCode.code))
    return [{"code": r.code, "label": r.label, "source": r.source} for r in rows]


class ForgeCodeIn(BaseModel):
    list: str
    code: str = Field(min_length=1, max_length=200)
    label: str | None = Field(default=None, max_length=300)


@router.post("/forge-codes", status_code=201)
async def add_forge_code(body: ForgeCodeIn, current: CurrentUser = Depends(MAP),
                         db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    fc = await mapping.add_forge_code(db, current.tenant_id, body.list, body.code,
                                      body.label or body.code, _actor(current))
    return {"code": fc.code, "label": fc.label, "source": fc.source}


class CodeIn(BaseModel):
    forge_code: str | None
    note: str | None = Field(default=None, max_length=500)


@router.put("/codes/{map_id}")
async def set_code(map_id: uuid.UUID, body: CodeIn, current: CurrentUser = Depends(MAP),
                   db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    m = await db.get(CodeMap, map_id)
    if m is None:
        raise NotFound("Mapping not found")
    await mapping.change(db, m, forge_code=body.forge_code, actor=_actor(current),
                         note=body.note)
    labels = {(m.list, r.code): r.label for r in await db.scalars(
        select(ForgeCode).where(ForgeCode.list == m.list))}
    return _code(m, labels, {})


class ConfirmIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)


@router.post("/codes/confirm")
async def confirm_codes(body: ConfirmIn, current: CurrentUser = Depends(MAP),
                        db: AsyncSession = Depends(get_db)) -> dict[str, int]:
    return {"confirmed": await mapping.confirm_many(db, body.ids, _actor(current))}


@router.get("/codes/{map_id}/history")
async def code_history(map_id: uuid.UUID, current: CurrentUser = Depends(READ),
                       db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = await db.scalars(select(CodeMapHistory).where(CodeMapHistory.code_map_id == map_id)
                            .order_by(CodeMapHistory.version.desc()))
    return [{"version": h.version, "forge_code": h.forge_code, "status": h.status,
             "suggestion_source": h.suggestion_source, "changed_by": h.changed_by,
             "changed_at": h.changed_at} for h in rows]


# --- the account page ------------------------------------------------------------------------

@router.get("/accounts/{account_id}")
async def account_erp(account_id: uuid.UUID,
                      current: CurrentUser = Depends(require_permission("accounts.read")),
                      db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """What the ERPs hold for one account: its customer codes (with each ERP's credit
    limit and payment terms), orders, invoices, payments and the outstanding total,
    each with the time it was last read ("as of")."""
    acc = await db.get(Account, account_id)
    if acc is None:
        raise NotFound("Account not found")
    links = list(await db.scalars(select(ErpCustomer).where(
        ErpCustomer.account_id == account_id).order_by(ErpCustomer.external_id)))
    conns = {c.id: c for c in await db.scalars(select(ErpConnection))}
    keys = [(lk.connection_id, lk.external_id) for lk in links]

    def of(model):
        if not keys:
            return None
        return or_(*[(model.connection_id == c) & (model.customer_external_id == e)
                     for c, e in keys])

    orders, invoices, receipts = [], [], []
    if keys:
        orders = list(await db.scalars(select(ErpOrder).where(of(ErpOrder),
                                                              ErpOrder.gone_at.is_(None))
                                       .order_by(ErpOrder.order_date.desc()).limit(200)))
        invoices = list(await db.scalars(select(ErpInvoice).where(of(ErpInvoice),
                                                                  ErpInvoice.gone_at.is_(None))
                                         .order_by(ErpInvoice.invoice_date.desc()).limit(200)))
        receipts = list(await db.scalars(select(ErpReceipt).where(of(ErpReceipt),
                                                                  ErpReceipt.gone_at.is_(None))
                                         .order_by(ErpReceipt.received_on.desc()).limit(200)))
    today = date.today()
    open_inv = [i for i in invoices if i.status not in ("draft", "cancelled", "paid")
                and i.outstanding > 0]
    by_currency: dict[str, dict[str, Decimal]] = {}
    for i in open_inv:
        t = by_currency.setdefault(i.currency, {"outstanding": Decimal(0),
                                                "overdue": Decimal(0)})
        t["outstanding"] += i.outstanding
        if i.due_date and i.due_date < today:
            t["overdue"] += i.outstanding
    can_write = has_permission(current.permissions, "erp.write")
    linked_conns = {lk.connection_id for lk in links}
    writes_rows = list(await db.scalars(select(ErpWrite).where(ErpWrite.account_id == account_id)
                                        .order_by(ErpWrite.created_at.desc()).limit(20)))

    def row(r, **extra):
        conn = conns.get(r.connection_id)
        return {"id": str(r.id), "connection": conn.name if conn else None,
                "external_id": r.external_id, "synced_at": r.synced_at, **extra}

    return {
        "links": [{**row(lk, name=lk.name, gstin=lk.gstin, link_method=lk.link_method,
                         credit_limit=_iso(lk.credit_limit), currency=lk.currency,
                         payment_terms=lk.payment_terms, customer_group=lk.customer_group,
                         territory=lk.territory, gone=lk.gone_at is not None)}
                  for lk in links],
        "orders": [row(o, number=o.number, order_date=o.order_date, status=o.status,
                       erp_status=o.erp_status, delivery_date=o.delivery_date,
                       customer_po=o.customer_po, currency=o.currency,
                       grand_total=_iso(o.grand_total), lines=len(o.data.get("lines", [])))
                   for o in orders],
        "invoices": [row(i, number=i.number, invoice_date=i.invoice_date, due_date=i.due_date,
                         status=i.status, erp_status=i.erp_status, currency=i.currency,
                         grand_total=_iso(i.grand_total), outstanding=_iso(i.outstanding),
                         overdue=bool(i.due_date and i.due_date < today and i.outstanding > 0
                                      and i.status not in ("paid", "cancelled", "draft")))
                     for i in invoices],
        "receipts": [row(r, number=r.number, received_on=r.received_on, amount=_iso(r.amount),
                         currency=r.currency, status=r.status) for r in receipts],
        "outstanding": [{"currency": c, "outstanding": _iso(v["outstanding"]),
                         "overdue": _iso(v["overdue"])} for c, v in sorted(by_currency.items())],
        "as_of": max((x.synced_at for x in [*links, *orders, *invoices]), default=None),
        "can_create_in": [{"id": str(c.id), "name": c.name} for c in conns.values()
                          if c.status == "active" and c.id not in linked_conns] if can_write
        else [],
        "writes": [writes.out(w) for w in writes_rows],
    }


class CreateInErpIn(BaseModel):
    connection_id: uuid.UUID
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)


@router.post("/accounts/{account_id}/create-in-erp")
async def create_in_erp(account_id: uuid.UUID, body: CreateInErpIn,
                        current: CurrentUser = Depends(require_permission("erp.write")),
                        db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Create this account as a customer in an ERP (safeguard 1). Duplicates are
    refused, the policy gate decides whether a person must approve first, and the same
    request is never sent twice."""
    result = await writes.request_create(db, current.tenant_id,
                                         connection_id=body.connection_id,
                                         account_id=account_id, actor=_actor(current),
                                         idempotency_key=body.idempotency_key)
    return result


# --- MCP clients (decision E9) ---------------------------------------------------------------

class McpClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    allow_write: bool = False


def _mcp(c: McpClient) -> dict[str, Any]:
    return {"id": str(c.id), "name": c.name, "token_hint": c.token_hint,
            "allow_write": c.allow_write, "created_by": c.created_by,
            "created_at": c.created_at, "last_used_at": c.last_used_at,
            "revoked_at": c.revoked_at, "revoked_by": c.revoked_by}


@router.get("/mcp-clients")
async def mcp_clients(current: CurrentUser = Depends(require_permission("mcp.manage")),
                      db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = await db.scalars(select(McpClient).order_by(McpClient.created_at.desc()))
    return [_mcp(c) for c in rows]


@router.post("/mcp-clients", status_code=201)
async def create_mcp_client(body: McpClientIn,
                            current: CurrentUser = Depends(require_permission("mcp.manage")),
                            db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    from app.integrations.mcp_server import issue_client

    client, token = await issue_client(db, current.tenant_id, body.name, body.allow_write,
                                       _actor(current))
    return {**_mcp(client), "token": token}


@router.post("/mcp-clients/{client_id}/revoke")
async def revoke_mcp_client(client_id: uuid.UUID,
                            current: CurrentUser = Depends(require_permission("mcp.manage")),
                            db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    c = await db.get(McpClient, client_id)
    if c is None:
        raise NotFound("Client not found")
    if c.revoked_at is None:
        c.revoked_at, c.revoked_by = datetime.now(UTC), _actor(current)
        await chain.append(db, c.tenant_id, "mcp.client.revoked", actor=_actor(current),
                           data={"client_id": c.id, "name": c.name})
    return _mcp(c)


@router.get("/causes")
async def causes(current: CurrentUser = Depends(READ)) -> dict[str, Any]:
    return {k: {"meaning": v, "title": cx.CAUSE_TITLES.get(k), "fix": cx.CAUSE_FIXES.get(k)}
            for k, v in CAUSES.items()}
