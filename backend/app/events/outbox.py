"""Transactional outbox.

Publishing: call `publish(session, tenant_id, "enquiry.created", {...})` inside the
same transaction as the business change. If the transaction rolls back, the event
is gone too, so events never describe changes that did not happen.

Delivery: the worker calls `dispatch_batch()` on a schedule. Each pending event is
passed to every handler registered for its type. A failure is retried with
exponential backoff; after `outbox_max_attempts` the event is marked `dead` and
stays visible for a human (no silent failures). Handlers must be idempotent: an
event can be delivered more than once if the worker dies mid-batch.

When Kafka/Redpanda is introduced later, only `dispatch_batch` changes: it will
forward events to the broker instead of calling handlers in-process.
"""

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import system_session, tenant_session
from app.models import OutboxEvent

log = logging.getLogger(__name__)

Handler = Callable[[AsyncSession, OutboxEvent], Awaitable[None]]
_handlers: dict[str, list[Handler]] = defaultdict(list)


def subscribe(event_type: str) -> Callable[[Handler], Handler]:
    """Decorator registering a handler. The handler gets a session scoped to the event's tenant."""

    def deco(fn: Handler) -> Handler:
        _handlers[event_type].append(fn)
        return fn

    return deco


def clear_handlers() -> None:
    _handlers.clear()


async def publish(
    session: AsyncSession, tenant_id: UUID, event_type: str, payload: dict[str, Any]
) -> OutboxEvent:
    event = OutboxEvent(tenant_id=tenant_id, event_type=event_type, payload=payload)
    session.add(event)
    await session.flush()
    return event


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(2**attempts, 3600))


async def dispatch_batch() -> int:
    """Deliver one batch of due events. Returns how many were processed."""
    s = get_settings()
    async with system_session() as session:
        events = list(
            await session.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.status == "pending",
                    OutboxEvent.available_at <= datetime.now(UTC),
                )
                .order_by(OutboxEvent.created_at)
                .limit(s.outbox_batch_size)
                .with_for_update(skip_locked=True)
            )
        )
        for event in events:
            try:
                for handler in _handlers.get(event.event_type, []):
                    # Each handler runs in its own tenant-scoped transaction.
                    async with tenant_session(event.tenant_id) as tsession:
                        await handler(tsession, event)
            except Exception as exc:  # noqa: BLE001 - every failure must be recorded
                event.attempts += 1
                event.last_error = f"{type(exc).__name__}: {exc}"[:2000]
                if event.attempts >= s.outbox_max_attempts:
                    event.status = "dead"
                    log.error("outbox event %s dead after %s attempts", event.id, event.attempts)
                else:
                    event.available_at = datetime.now(UTC) + _backoff(event.attempts)
                    log.warning("outbox event %s failed (attempt %s)", event.id, event.attempts)
            else:
                event.status = "published"
                event.published_at = datetime.now(UTC)
        return len(events)
