"""Running flows on a schedule (decision E6) around the sync loop in sync.py.

tick() runs every minute (Celery beat): it checks every active connection (so a
revoked key alerts within minutes), starts each flow that is due (every
INTEGRATION_POLL_MINUTES, or at once after "Sync now" or a webhook nudge), and the
nightly reconciliation after INTEGRATION_RECONCILE_HOUR local time.

run_sync() / run_reconcile() run one flow. A flow is claimed first (running_since),
so two workers never run it at once; a claim older than the stuck limit is taken
over. Each page is stored together with the flow's resume point in one transaction,
so a crash loses at most the page being read. Afterwards the run is recorded, ERP
customers are linked to accounts, new codes go to the mapping studio, dead-letter
tasks and the connection's alert are brought up to date.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.config import get_settings
from app.db import system_session, tenant_session
from app.integrations import linking, mapping
from app.integrations.adapter import ConnectorError, RecordProblem
from app.integrations.alerts import sync_task
from app.integrations.connections import adapter_for, refresh_health
from app.integrations.contracts import ENTITY_LABELS, Entity, Record
from app.integrations.store import DbSink
from app.integrations.sync import DEFAULT_RETRY, FlowState, RetryPolicy, reconcile, run_flow
from app.models import ErpConnection, SyncDeadLetter, SyncFlow, SyncRun

log = logging.getLogger(__name__)


class _Pages:
    """A Sink that stores each page in its own transaction, committed together with the
    flow's resume point when the sync loop calls checkpoint()."""

    def __init__(self, tenant_id: uuid.UUID, connection_id: uuid.UUID, flow_id: uuid.UUID):
        self.tenant_id, self.connection_id, self.flow_id = tenant_id, connection_id, flow_id
        self._cm = None
        self._sink: DbSink | None = None
        self.changed: dict[Entity, list[uuid.UUID]] = {e: [] for e in Entity}

    async def _open(self) -> DbSink:
        if self._sink is None:
            self._cm = tenant_session(self.tenant_id)
            session = await self._cm.__aenter__()
            self._sink = DbSink(session, self.tenant_id, self.connection_id)
        return self._sink

    async def close(self, exc: BaseException | None = None) -> None:
        if self._cm is None or self._sink is None:
            return
        cm, sink = self._cm, self._sink
        self._cm, self._sink = None, None
        if exc is None:
            await cm.__aexit__(None, None, None)
            for e, ids in sink.changed.items():
                self.changed[e].extend(ids)
        else:
            await cm.__aexit__(type(exc), exc, exc.__traceback__)

    async def put(self, entity: Entity, records: list[Record]) -> None:
        await (await self._open()).put(entity, records)

    async def problems(self, entity: Entity, problems: list[RecordProblem]) -> None:
        await (await self._open()).problems(entity, problems)

    async def held(self, entity: Entity) -> dict[str, datetime]:
        return await (await self._open()).held(entity)

    async def mark_gone(self, entity: Entity, external_ids: list[str]) -> int:
        return await (await self._open()).mark_gone(entity, external_ids)

    async def checkpoint(self, state: FlowState) -> None:
        sink = await self._open()
        flow = await sink.session.get(SyncFlow, self.flow_id)
        flow.state = state.to_json()
        await self.close()


async def _claim(tenant_id: uuid.UUID, flow_id: uuid.UUID, kind: str, trigger: str,
                 now: datetime) -> tuple[uuid.UUID, Any, FlowState, Entity, uuid.UUID] | None:
    stuck = timedelta(minutes=get_settings().integration_stuck_run_minutes)
    async with tenant_session(tenant_id) as s:
        flow = await s.get(SyncFlow, flow_id, with_for_update=True)
        if flow is None or not flow.enabled:
            return None
        conn = await s.get(ErpConnection, flow.connection_id)
        if conn is None or conn.status != "active":
            return None
        if flow.running_since and now - flow.running_since < stuck:
            return None  # another worker has it
        flow.running_since, flow.last_started_at = now, now
        if kind == "sync":
            flow.run_requested_at = None
        run = SyncRun(id=uuid.uuid4(), tenant_id=tenant_id, flow_id=flow.id, kind=kind,
                      trigger=trigger, status="running", started_at=now)
        s.add(run)
        try:
            adapter = adapter_for(conn)
        except ConnectorError as e:
            run.status, run.finished_at = "failed", now
            run.error_cause, run.error_message = e.cause, e.message[:500]
            flow.running_since = None
            flow.consecutive_failures += 1
            flow.last_error_cause, flow.last_error_message = e.cause, e.message[:500]
            await refresh_health(s, conn, now=now)
            return None
        return run.id, adapter, FlowState.from_json(flow.state), Entity(flow.entity), conn.id


async def _finish(tenant_id: uuid.UUID, flow_id: uuid.UUID, run_id: uuid.UUID,
                  started: datetime, *, ok: bool, read: int, problems: int, retries: int,
                  error: ConnectorError | None, state: FlowState | None,
                  changed: dict[Entity, list[uuid.UUID]], details: dict[str, Any]) -> dict:
    now = datetime.now(UTC)
    async with tenant_session(tenant_id) as s:
        flow = await s.get(SyncFlow, flow_id, with_for_update=True)
        run = await s.get(SyncRun, run_id)
        conn = await s.get(ErpConnection, flow.connection_id)
        entity = Entity(flow.entity)
        if state is not None:
            flow.state = state.to_json()
        flow.running_since, flow.last_finished_at = None, now
        flow.records_read += read
        if ok:
            flow.last_success_at = started  # everything changed before the run began is in
            flow.consecutive_failures = 0
            flow.last_error_cause = flow.last_error_message = None
        elif error is not None:
            flow.consecutive_failures += 1
            flow.last_error_cause, flow.last_error_message = error.cause, error.message[:500]
        run.finished_at = now
        run.read, run.problems, run.retries = read, problems, retries
        run.status = ("failed" if error is not None else
                      "succeeded" if ok and not problems else "partial")
        if error is not None:
            run.error_cause, run.error_message = error.cause, error.message[:500]
        run.details = details

        if entity == Entity.customer and changed[Entity.customer]:
            details["linking"] = await linking.process_changed(
                s, conn, changed[Entity.customer], trigger=f"sync:{run.kind}")
            run.details = dict(details)
        if any(changed.values()):
            await mapping.discover(s, conn, entity)
        await sync_dead_letter_task(s, flow, conn)
        await refresh_health(s, conn, now=now)
        return {"run_id": str(run.id), "status": run.status, "read": read,
                "problems": problems, "error": error.cause if error else None, **details}


async def sync_dead_letter_task(s, flow: SyncFlow, conn: ErpConnection) -> None:
    open_rows = list(await s.scalars(
        select(SyncDeadLetter).where(SyncDeadLetter.connection_id == conn.id,
                                     SyncDeadLetter.entity == flow.entity,
                                     SyncDeadLetter.status == "open")
        .order_by(SyncDeadLetter.first_seen_at).limit(50)))
    total = await s.scalar(select(func.count()).select_from(SyncDeadLetter).where(
        SyncDeadLetter.connection_id == conn.id, SyncDeadLetter.entity == flow.entity,
        SyncDeadLetter.status == "open")) or 0
    label = ENTITY_LABELS[Entity(flow.entity)].lower()
    title = (f"{total} {label} from {conn.name} could not be read into Forge" if total else None)
    reasons = [f"{d.external_id or 'A record'}: {d.reason}" for d in open_rows[:5]]
    if total > 5:
        reasons.append(f"...and {total - 5} more on the connection's page")
    await sync_task(s, conn.tenant_id, key=f"dead:{flow.id}", record_type="erp_connection",
                    record_id=conn.id, title=title, reasons=reasons,
                    args={"connection_id": str(conn.id), "entity": flow.entity, "open": total},
                    done_note="Every record was read, retried or skipped")


async def run_sync(tenant_id: uuid.UUID, flow_id: uuid.UUID, *, trigger: str = "schedule",
                   page_size: int = 200, retry: RetryPolicy = DEFAULT_RETRY,
                   sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                   now: datetime | None = None) -> dict | None:
    """One sync of one flow. Returns a summary, or None when it did not start (paused,
    switched off, or already running)."""
    started = now or datetime.now(UTC)
    claimed = await _claim(tenant_id, flow_id, "sync", trigger, started)
    if claimed is None:
        return None
    run_id, adapter, state, entity, conn_id = claimed
    pages = _Pages(tenant_id, conn_id, flow_id)
    try:
        result = await run_flow(adapter, entity, pages, state, page_size=page_size, retry=retry,
                                sleep=sleep, checkpoint=pages.checkpoint)
    except Exception as exc:  # noqa: BLE001 - recorded on the run and alerted
        log.exception("sync of %s failed", entity)
        await pages.close(exc)
        await adapter.aclose()
        return await _finish(tenant_id, flow_id, run_id, started, ok=False, read=0, problems=0,
                             retries=0, error=_internal(exc), state=None, changed=pages.changed,
                             details={})
    await pages.close()
    await adapter.aclose()
    return await _finish(tenant_id, flow_id, run_id, started, ok=result.ok, read=result.read,
                         problems=result.problems, retries=result.retries, error=result.error,
                         state=result.state, changed=pages.changed,
                         details={"pages": result.pages})


async def run_reconcile(tenant_id: uuid.UUID, flow_id: uuid.UUID, *, trigger: str = "schedule",
                        retry: RetryPolicy = DEFAULT_RETRY,
                        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                        now: datetime | None = None) -> dict | None:
    """The nightly comparison of one flow: repairs what is missing or out of date, marks
    records the ERP no longer has as gone, and asks for a full re-read when the gap is
    too large to repair one by one."""
    started = now or datetime.now(UTC)
    claimed = await _claim(tenant_id, flow_id, "reconcile", trigger, started)
    if claimed is None:
        return None
    run_id, adapter, _, entity, conn_id = claimed
    pages = _Pages(tenant_id, conn_id, flow_id)
    try:
        rec = await reconcile(adapter, entity, pages, retry=retry, sleep=sleep)
        gone = await pages.mark_gone(entity, rec.gone) if rec.error is None else 0
        await pages.close()
    except Exception as exc:  # noqa: BLE001
        log.exception("reconciliation of %s failed", entity)
        await pages.close(exc)
        await adapter.aclose()
        return await _finish(tenant_id, flow_id, run_id, started, ok=False, read=0, problems=0,
                             retries=0, error=_internal(exc), state=None, changed=pages.changed,
                             details={})
    await adapter.aclose()
    details = {"checked": rec.checked, "missing": len(rec.missing), "stale": len(rec.stale),
               "gone": len(rec.gone), "newly_gone": gone, "repaired": rec.repaired,
               "unrepaired": len(rec.unrepaired),
               "examples": {"missing": rec.missing[:10], "stale": rec.stale[:10],
                            "gone": rec.gone[:10]}}
    summary = await _finish(tenant_id, flow_id, run_id, started, ok=rec.error is None,
                            read=rec.repaired, problems=0, retries=0, error=rec.error,
                            state=None, changed=pages.changed, details=details)
    async with tenant_session(tenant_id) as s:
        flow = await s.get(SyncFlow, flow_id)
        if rec.error is None:
            flow.reconciled_at = started
        flow.last_reconcile = {**details, "at": started.isoformat(),
                               "error": rec.error.message if rec.error else None}
        if rec.unrepaired:
            # Too much drift to fetch one by one: read the whole record type again.
            flow.state, flow.run_requested_at = {}, datetime.now(UTC)
    return summary


def _internal(exc: Exception) -> ConnectorError:
    return ConnectorError("bad_response", f"Forge failed while storing what ERPNext sent: "
                          f"{type(exc).__name__}", detail=str(exc)[:500])


# --- the minute tick ----------------------------------------------------------------------

Enqueue = Callable[[str, uuid.UUID, uuid.UUID, str], Awaitable[None] | None]


def _local(dt: datetime) -> datetime:
    return dt.astimezone(ZoneInfo(get_settings().integration_local_time_zone))


def due(flow: SyncFlow, now: datetime) -> str | None:
    """'sync', 'reconcile' or None for one flow of an active connection."""
    cfg = get_settings()
    if not flow.enabled:
        return None
    if flow.running_since and now - flow.running_since < timedelta(
            minutes=cfg.integration_stuck_run_minutes):
        return None
    if (flow.run_requested_at or flow.last_started_at is None
            or now - flow.last_started_at >= timedelta(minutes=cfg.integration_poll_minutes)):
        return "sync"
    here = _local(now)
    if (flow.last_success_at and here.hour >= cfg.integration_reconcile_hour
            and (flow.reconciled_at is None or _local(flow.reconciled_at).date() < here.date())):
        return "reconcile"
    return None


async def tick(*, now: datetime | None = None, enqueue: Enqueue | None = None) -> dict[str, int]:
    """Check every active connection and start what is due. Platform job: it finds
    connections of every tenant, then works inside each tenant's own session."""
    from app.integrations.connections import check_connection

    now = now or datetime.now(UTC)
    if enqueue is None:
        enqueue = _celery_enqueue
    async with system_session() as s:
        conns = [(c.id, c.tenant_id, c.status) for c in await s.scalars(select(ErpConnection))]
        flows = list(await s.scalars(select(SyncFlow)))
    counts = {"checked": 0, "sync": 0, "reconcile": 0}
    active = {cid for cid, _, status in conns if status == "active"}
    for conn_id, tenant_id, status in conns:
        async with tenant_session(tenant_id) as s:
            conn = await s.get(ErpConnection, conn_id)
            if status == "active":
                await check_connection(s, conn, now=now)
                counts["checked"] += 1
            else:
                await refresh_health(s, conn, now=now)  # paused: clears its alert
    for f in flows:
        if f.connection_id not in active:
            continue
        what = due(f, now)
        if what:
            trigger = "manual" if (what == "sync" and f.run_requested_at) else "schedule"
            maybe = enqueue(what, f.tenant_id, f.id, trigger)
            if asyncio.iscoroutine(maybe):
                await maybe
            counts[what] += 1
    return counts


def _celery_enqueue(what: str, tenant_id: uuid.UUID, flow_id: uuid.UUID, trigger: str) -> None:
    from app.worker.tasks import integration_run

    integration_run.delay(what, str(tenant_id), str(flow_id), trigger)
