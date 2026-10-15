"""A fixed admin login for development PCs (asked for on 15 Oct 2026), so testing always
has a way in:

    company  forge
    email    admin@forge.example.com
    password Forge@2026

The API adds it when it starts with ENVIRONMENT=development (the default). A server with
any other ENVIRONMENT, such as production, never gets it. Once it exists it is left as it
is, so a password changed in Forge stays changed.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.core.security import hash_password
from app.db import set_tenant
from app.models import Role, Tenant, User, UserRole

log = logging.getLogger(__name__)

COMPANY = "forge"
EMAIL = "admin@forge.example.com"
PASSWORD = "Forge@2026"


async def ensure() -> bool:
    """Add the company, its Admin role and the admin if any is missing (as the schema owner,
    like create-tenant). True when something was added."""
    engine = create_async_engine(get_settings().migration_database_url)
    try:
        async with async_sessionmaker(engine)() as s, s.begin():
            tenant = await s.scalar(select(Tenant).where(Tenant.slug == COMPANY))
            if tenant is None:
                tenant = Tenant(name="Forge (development)", slug=COMPANY)
                s.add(tenant)
                await s.flush()
            await set_tenant(s, tenant.id)
            if await s.scalar(select(User.id).where(User.email == EMAIL)):
                return False
            role = await s.scalar(select(Role).where(Role.name == "Admin"))
            if role is None:
                role = Role(tenant_id=tenant.id, name="Admin", description="Full access",
                            permissions=["*"])
                s.add(role)
            user = User(tenant_id=tenant.id, email=EMAIL, full_name="Forge Admin",
                        password_hash=hash_password(PASSWORD))
            s.add(user)
            await s.flush()
            s.add(UserRole(tenant_id=tenant.id, user_id=user.id, role_id=role.id))
            return True
    finally:
        await engine.dispose()


async def ensure_on_startup() -> None:
    if get_settings().environment != "development":
        return
    try:
        if await ensure():
            log.warning("Added the development login: company %s, %s", COMPANY, EMAIL)
    except Exception:  # a database not migrated yet must not stop the API starting
        log.exception("Could not add the development login; run `uv run alembic upgrade "
                      "head` and start the API again")
