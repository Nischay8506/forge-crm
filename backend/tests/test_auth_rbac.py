from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.db import set_tenant
from app.models import Role, Tenant, User, UserRole
from tests.conftest import OWNER_URL, login


async def test_login_me_and_refresh(client, make_tenant):
    slug, email, pw = await make_tenant()
    headers = await login(client, slug, email, pw)
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    assert me["email"] == email and me["permissions"] == ["*"]

    r = await client.post(
        "/api/v1/auth/login", json={"company": slug, "email": email, "password": pw}
    )
    r2 = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": r.json()["refresh_token"]}
    )
    assert r2.status_code == 200


async def test_bad_credentials_rejected(client, make_tenant):
    slug, email, _ = await make_tenant()
    r = await client.post(
        "/api/v1/auth/login", json={"company": slug, "email": email, "password": "wrong"}
    )
    assert r.status_code == 401
    r = await client.post(
        "/api/v1/auth/login", json={"company": "nope", "email": email, "password": "x"}
    )
    assert r.status_code == 401


async def test_endpoints_require_token(client):
    assert (await client.get("/api/v1/users")).status_code == 401
    bad = {"Authorization": "Bearer not-a-token"}
    assert (await client.get("/api/v1/users", headers=bad)).status_code == 401


async def test_permission_is_checked(client, make_tenant):
    slug, _, _ = await make_tenant()
    engine = create_async_engine(OWNER_URL)
    async with async_sessionmaker(engine)() as s, s.begin():
        tenant = await s.scalar(select(Tenant).where(Tenant.slug == slug))
        await set_tenant(s, tenant.id)
        role = Role(tenant_id=tenant.id, name="Viewer", permissions=["llm.usage.read"])
        user = User(
            tenant_id=tenant.id,
            email="viewer@example.com",
            full_name="V",
            password_hash=hash_password("pw"),
        )
        s.add_all([role, user])
        await s.flush()
        s.add(UserRole(tenant_id=tenant.id, user_id=user.id, role_id=role.id))
    await engine.dispose()

    headers = await login(client, slug, "viewer@example.com", "pw")
    r = await client.get("/api/v1/users", headers=headers)
    assert r.status_code == 403
    assert "users.read" in r.json()["error"]["message"]
