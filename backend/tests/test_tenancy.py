"""Tenant isolation is enforced by the database, not by remembering a WHERE clause."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db import get_sessionmaker, set_tenant, tenant_session
from app.models import Tenant, User
from tests.conftest import login


async def test_api_only_shows_own_tenant(client, make_tenant):
    a = await make_tenant()
    b = await make_tenant()
    users_a = (await client.get("/api/v1/users", headers=await login(client, *a))).json()
    users_b = (await client.get("/api/v1/users", headers=await login(client, *b))).json()
    assert [u["email"] for u in users_a] == [a[1]]
    assert [u["email"] for u in users_b] == [b[1]]


async def test_unscoped_session_sees_nothing(make_tenant):
    await make_tenant()
    async with get_sessionmaker()() as s:
        assert (await s.scalars(select(User))).all() == []


async def test_cannot_write_into_another_tenant(make_tenant):
    a = await make_tenant()
    b = await make_tenant()
    async with get_sessionmaker()() as s:
        ta = await s.scalar(select(Tenant).where(Tenant.slug == a[0]))
        tb = await s.scalar(select(Tenant).where(Tenant.slug == b[0]))
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(ta.id) as s:
            s.add(User(tenant_id=tb.id, email="x@y.z", full_name="x", password_hash="x"))
            await s.flush()


async def test_app_role_cannot_create_tenants():
    with pytest.raises(DBAPIError, match="permission denied"):
        async with get_sessionmaker()() as s, s.begin():
            await s.execute(
                text("INSERT INTO tenants (id, name, slug) VALUES (gen_random_uuid(), 'x', 'x')")
            )


async def test_every_tenant_table_has_forced_rls():
    """Guards against a new table being added without enable_tenant_rls()."""
    async with get_sessionmaker()() as s:
        rows = (
            await s.execute(
                text("""
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
            FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid
            WHERE a.attname = 'tenant_id' AND c.relkind = 'r'
              AND c.relnamespace = 'public'::regnamespace
        """)
            )
        ).all()
    assert rows, "expected tenant tables"
    missing = [r.relname for r in rows if not (r.relrowsecurity and r.relforcerowsecurity)]
    assert missing == [], f"tables without forced RLS: {missing}"


async def test_set_tenant_is_transaction_local(make_tenant):
    a = await make_tenant()
    async with get_sessionmaker()() as s:
        t = await s.scalar(select(Tenant).where(Tenant.slug == a[0]))
    async with get_sessionmaker()() as s:
        async with s.begin():
            await set_tenant(s, t.id)
            assert len((await s.scalars(select(User))).all()) == 1
        async with s.begin():
            assert (await s.scalars(select(User))).all() == []
