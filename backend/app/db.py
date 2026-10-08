"""Database engine and sessions.

Tenant isolation is enforced by PostgreSQL row-level security (RLS). Every
tenant-owned table has a policy comparing its tenant_id with the
`app.tenant_id` setting, which is set per transaction by `set_tenant`.
A session that never calls `set_tenant` sees no tenant rows at all.

`system_session` is for background jobs that must work across tenants
(e.g. the outbox dispatcher). It sets `app.system = on`, which only the
policies that explicitly allow it honour (currently the outbox table).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine, _sessionmaker = None, None


async def set_tenant(session: AsyncSession, tenant_id: UUID) -> None:
    """Scope the current transaction to one tenant (transaction-local setting)."""
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)}
    )


@asynccontextmanager
async def tenant_session(tenant_id: UUID) -> AsyncIterator[AsyncSession]:
    """A session already scoped to a tenant; commits on success."""
    async with get_sessionmaker()() as session, session.begin():
        await set_tenant(session, tenant_id)
        yield session


@asynccontextmanager
async def system_session() -> AsyncIterator[AsyncSession]:
    """Cross-tenant session for platform jobs. Only RLS policies that allow app.system see rows."""
    async with get_sessionmaker()() as session, session.begin():
        await session.execute(text("SELECT set_config('app.system', 'on', true)"))
        yield session


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one transaction per request, committed at the end."""
    async with get_sessionmaker()() as session, session.begin():
        yield session
