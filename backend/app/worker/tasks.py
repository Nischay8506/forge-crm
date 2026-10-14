import asyncio

from app.agents.registry import load_definitions
from app.approvals import notify as inbox_notify
from app.db import dispose_engine
from app.events.outbox import dispatch_batch
from app.steward import handlers as steward_handlers
from app.worker.celery_app import celery_app

# Outbox handlers and agent definitions must be registered in the worker process
# before delivery starts.
steward_handlers.register()
inbox_notify.register()
load_definitions()


async def _dispatch() -> int:
    try:
        return await dispatch_batch()
    finally:
        # Each task runs in a fresh event loop, so the pool must not outlive it.
        await dispose_engine()


@celery_app.task(name="forge.dispatch_outbox")
def dispatch_outbox() -> int:
    return asyncio.run(_dispatch())


async def _process_import(tenant_id: str, batch_id: str) -> None:
    from uuid import UUID

    from app.db import tenant_session
    from app.models import ImportBatch
    from app.steward.importer import run_import

    try:
        async with tenant_session(UUID(tenant_id)) as session:
            batch = await session.get(ImportBatch, UUID(batch_id))
            if batch is not None and batch.status == "processing":
                await run_import(session, batch)
    except Exception as exc:
        async with tenant_session(UUID(tenant_id)) as session:
            batch = await session.get(ImportBatch, UUID(batch_id))
            if batch is not None:
                batch.status = "failed"
                batch.errors = [{"row": None, "problems": [f"{type(exc).__name__}: {exc}"]}]
        raise
    finally:
        await dispose_engine()


@celery_app.task(name="forge.process_import")
def process_import(tenant_id: str, batch_id: str) -> None:
    asyncio.run(_process_import(tenant_id, batch_id))


async def _integrations_tick() -> dict:
    from app.integrations.runner import tick

    try:
        return await tick()
    finally:
        await dispose_engine()


@celery_app.task(name="forge.integrations_tick")
def integrations_tick() -> dict:
    return asyncio.run(_integrations_tick())


async def _integration_run(what: str, tenant_id: str, flow_id: str, trigger: str) -> dict | None:
    from uuid import UUID

    from app.integrations.runner import run_reconcile, run_sync

    run = run_sync if what == "sync" else run_reconcile
    try:
        return await run(UUID(tenant_id), UUID(flow_id), trigger=trigger)
    finally:
        await dispose_engine()


@celery_app.task(name="forge.integration_run")
def integration_run(what: str, tenant_id: str, flow_id: str, trigger: str) -> dict | None:
    return asyncio.run(_integration_run(what, tenant_id, flow_id, trigger))


async def _audit_seal() -> dict:
    from app.audit.seals import seal_all

    try:
        return await seal_all()
    finally:
        await dispose_engine()


@celery_app.task(name="forge.audit_seal")
def audit_seal() -> dict:
    return asyncio.run(_audit_seal())


async def _audit_retention() -> dict:
    from app.audit.retention import run_all

    try:
        return await run_all()
    finally:
        await dispose_engine()


@celery_app.task(name="forge.audit_retention")
def audit_retention() -> dict:
    return asyncio.run(_audit_retention())


async def _inbox_tick() -> dict:
    from app.approvals.sla import tick_all

    try:
        return await tick_all()
    finally:
        await dispose_engine()


@celery_app.task(name="forge.inbox_tick")
def inbox_tick() -> dict:
    return asyncio.run(_inbox_tick())


async def _channels_tick() -> dict:
    from app.channels.send import tick_all

    try:
        return await tick_all()
    finally:
        await dispose_engine()


@celery_app.task(name="forge.channels_tick")
def channels_tick() -> dict:
    return asyncio.run(_channels_tick())


async def _enquiry_tick() -> dict:
    from app.enquiries.intake import tick_all

    try:
        return await tick_all()
    finally:
        await dispose_engine()


@celery_app.task(name="forge.enquiry_tick")
def enquiry_tick() -> dict:
    """M1: read waiting emails, run the enquiry timers (F1.1, F1.10)."""
    return asyncio.run(_enquiry_tick())


async def _enquiry_followups() -> dict:
    from app.enquiries.intake import followups_all

    try:
        return await followups_all()
    finally:
        await dispose_engine()


@celery_app.task(name="forge.enquiry_followups")
def enquiry_followups() -> dict:
    """M1: nudge customers for missing details, park the quiet ones (F1.8)."""
    return asyncio.run(_enquiry_followups())


async def _mail_tick() -> dict:
    from app.enquiries.mail.service import check_all

    try:
        return await check_all()
    finally:
        await dispose_engine()


@celery_app.task(name="forge.mail_tick")
def mail_tick() -> dict:
    """M1: check the connected sales mailboxes (every minute, sooner after a push)."""
    return asyncio.run(_mail_tick())


async def _costs_refresh() -> dict:
    from app.cpq.costing import daily_refresh_all

    try:
        return await daily_refresh_all()
    finally:
        await dispose_engine()


@celery_app.task(name="forge.costs_refresh")
def costs_refresh() -> dict:
    """F3.2: work drafts out again and flag sent quotes when costs moved (K9, K11 A)."""
    return asyncio.run(_costs_refresh())


async def _cost_alert_emails() -> int:
    from app.cpq.costing import email_alerts_all

    try:
        return await email_alerts_all()
    finally:
        await dispose_engine()


@celery_app.task(name="forge.cost_alert_emails")
def cost_alert_emails() -> int:
    """F3.2: one email a day to each owner whose sent quotes' costs changed (K9 A)."""
    return asyncio.run(_cost_alert_emails())
