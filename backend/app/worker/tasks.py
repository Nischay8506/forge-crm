import asyncio

from app.agents.registry import load_definitions
from app.db import dispose_engine
from app.events.outbox import dispatch_batch
from app.steward import handlers as steward_handlers
from app.worker.celery_app import celery_app

# Outbox handlers and agent definitions must be registered in the worker process
# before delivery starts.
steward_handlers.register()
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
