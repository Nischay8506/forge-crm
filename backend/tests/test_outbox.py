import pytest
from sqlalchemy import select

from app.db import get_sessionmaker, system_session, tenant_session
from app.events import outbox
from app.models import OutboxEvent, Tenant


@pytest.fixture(autouse=True)
def _clean_handlers():
    outbox.clear_handlers()
    yield
    outbox.clear_handlers()


async def _tenant_id(slug):
    async with get_sessionmaker()() as s:
        return (await s.scalar(select(Tenant).where(Tenant.slug == slug))).id


async def _drain_pending():
    async with system_session() as s:
        for e in await s.scalars(select(OutboxEvent).where(OutboxEvent.status == "pending")):
            e.status = "published"


async def test_published_event_is_delivered_to_handler(make_tenant):
    await _drain_pending()
    tid = await _tenant_id((await make_tenant())[0])
    seen = []

    @outbox.subscribe("demo.created")
    async def handler(session, event):
        seen.append((event.tenant_id, event.payload["n"]))

    async with tenant_session(tid) as s:
        await outbox.publish(s, tid, "demo.created", {"n": 1})

    assert await outbox.dispatch_batch() == 1
    assert seen == [(tid, 1)]
    async with tenant_session(tid) as s:
        ev = await s.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "demo.created"))
        assert ev.status == "published" and ev.published_at is not None


async def test_rolled_back_transaction_publishes_nothing(make_tenant):
    await _drain_pending()
    tid = await _tenant_id((await make_tenant())[0])
    with pytest.raises(RuntimeError):
        async with tenant_session(tid) as s:
            await outbox.publish(s, tid, "demo.ghost", {})
            raise RuntimeError("business change failed")
    assert await outbox.dispatch_batch() == 0


async def test_failing_handler_retries_then_goes_dead(make_tenant, monkeypatch):
    await _drain_pending()
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "outbox_max_attempts", 2)
    tid = await _tenant_id((await make_tenant())[0])

    @outbox.subscribe("demo.boom")
    async def handler(session, event):
        raise ValueError("downstream down")

    async with tenant_session(tid) as s:
        ev = await outbox.publish(s, tid, "demo.boom", {})
        ev_id = ev.id

    await outbox.dispatch_batch()
    async with system_session() as s:
        ev = await s.get(OutboxEvent, ev_id)
        assert (ev.status, ev.attempts) == ("pending", 1)
        assert "downstream down" in ev.last_error
        ev.available_at = ev.created_at  # skip the backoff wait

    await outbox.dispatch_batch()
    async with system_session() as s:
        ev = await s.get(OutboxEvent, ev_id)
        assert (ev.status, ev.attempts) == ("dead", 2)
