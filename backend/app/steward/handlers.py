"""Outbox subscriptions: the Data Steward reacts to record changes."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.events.outbox import subscribe
from app.models import OutboxEvent
from app.steward.service import process_record


async def _on_change(session: AsyncSession, event: OutboxEvent) -> None:
    await process_record(session, event.payload["record_type"], uuid.UUID(event.payload["id"]))


async def _on_import(session: AsyncSession, event: OutboxEvent) -> None:
    from app.worker.tasks import process_import

    process_import.delay(str(event.tenant_id), event.payload["batch_id"])


def register() -> None:
    subscribe("import.requested")(_on_import)
    for name in ("account.created", "account.updated", "contact.created", "contact.updated"):
        subscribe(name)(_on_change)
