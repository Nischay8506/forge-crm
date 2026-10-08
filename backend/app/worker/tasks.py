import asyncio

from app.db import dispose_engine
from app.events.outbox import dispatch_batch
from app.worker.celery_app import celery_app


async def _dispatch() -> int:
    try:
        return await dispatch_batch()
    finally:
        # Each task runs in a fresh event loop, so the pool must not outlive it.
        await dispose_engine()


@celery_app.task(name="forge.dispatch_outbox")
def dispatch_outbox() -> int:
    return asyncio.run(_dispatch())
