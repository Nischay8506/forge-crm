"""Fixed logins for development PCs (asked for on 15 Oct 2026), so testing always has a
way in without seeding a company first. One company, `forge`, with one login per role:

    company  forge          password  Forge@2026
    admin@forge.example.com     Admin, every screen
    tester@forge.example.com    Sales Manager, decides cards
    finance@forge.example.com   Finance, second approver
    rep@forge.example.com       Sales, asks for approvals

The API adds whatever is missing when it starts with ENVIRONMENT=development (the
default). A server with any other ENVIRONMENT, such as production, never gets them. An
existing company, role or user is left as it is, so a password changed in Forge stays
changed, and the data in the company survives restarts.

The prepared approval cards are a different thing: `cli seed-usability` builds a throwaway
company for one usability session (docs/USABILITY_TEST.md).
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.approvals.usability import DECIDER
from app.config import get_settings
from app.core.security import hash_password
from app.db import set_tenant
from app.models import Role, Tenant, User, UserRole

log = logging.getLogger(__name__)

COMPANY = "forge"
PASSWORD = "Forge@2026"
EMAIL = "admin@forge.example.com"  # the Admin, for anything that needs one login
ROLES = {
    "Admin": ("Full access", ["*"]),
    "Sales Manager": ("Decides payments and messages", DECIDER),
    "Finance": ("Second approver on large amounts", DECIDER),
    "Sales": ("Asks for payments to be recorded", ["accounts.read"]),
}
PEOPLE = {
    "admin": ("Forge Admin", "Admin"),
    "tester": ("Forge Tester", "Sales Manager"),
    "finance": ("Forge Finance", "Finance"),
    "rep": ("Forge Sales", "Sales"),
}


async def _roles(session: AsyncSession, tenant_id) -> dict[str, Role]:
    have = {r.name: r for r in await session.scalars(select(Role))}
    for name, (description, permissions) in ROLES.items():
        if name not in have:
            have[name] = Role(tenant_id=tenant_id, name=name, description=description,
                              permissions=permissions)
            session.add(have[name])
    await session.flush()
    return have


async def ensure() -> list[str]:
    """Add the company, its roles and the people who are missing (as the schema owner,
    like create-tenant). Returns the emails added."""
    engine = create_async_engine(get_settings().migration_database_url)
    try:
        async with async_sessionmaker(engine)() as s, s.begin():
            tenant = await s.scalar(select(Tenant).where(Tenant.slug == COMPANY))
            if tenant is None:
                tenant = Tenant(name="Forge (development)", slug=COMPANY)
                s.add(tenant)
                await s.flush()
            await set_tenant(s, tenant.id)
            roles = await _roles(s, tenant.id)
            added = []
            for key, (full_name, role) in PEOPLE.items():
                email = f"{key}@{COMPANY}.example.com"
                if await s.scalar(select(User.id).where(User.email == email)):
                    continue
                user = User(tenant_id=tenant.id, email=email, full_name=full_name,
                            password_hash=hash_password(PASSWORD))
                s.add(user)
                await s.flush()
                s.add(UserRole(tenant_id=tenant.id, user_id=user.id, role_id=roles[role].id))
                added.append(email)
            return added
    finally:
        await engine.dispose()


async def ensure_on_startup() -> None:
    if get_settings().environment != "development":
        return
    try:
        added = await ensure()
        if added:
            log.warning("Added the development logins for company %s: %s", COMPANY,
                        ", ".join(added))
    except Exception:  # a database not migrated yet must not stop the API starting
        log.exception("Could not add the development logins; run `uv run alembic upgrade "
                      "head` and start the API again")
