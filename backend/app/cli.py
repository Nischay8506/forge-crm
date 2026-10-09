"""Admin commands, run as the schema owner (tenants can only be created this way):

    uv run python -m app.cli create-tenant --name "Acme Pumps" --slug acme \
        --admin-email admin@acme.test --admin-name "Acme Admin" --admin-password '...'

Keys for stored ERP credentials (app/core/secrets.py):

    uv run python -m app.cli new-credential-key      # prints a key for CREDENTIAL_KEYS
    uv run python -m app.cli rotate-credentials      # re-encrypts under the first key
"""

import argparse
import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.core.security import hash_password
from app.db import set_tenant
from app.models import Role, Tenant, User, UserRole


async def create_tenant(name: str, slug: str, email: str, full_name: str, password: str) -> None:
    engine = create_async_engine(get_settings().migration_database_url)
    async with async_sessionmaker(engine)() as s, s.begin():
        tenant = Tenant(name=name, slug=slug.lower())
        s.add(tenant)
        await s.flush()
        # Tables use FORCE ROW LEVEL SECURITY, so even the owner must scope to the tenant.
        await set_tenant(s, tenant.id)
        admin_role = Role(
            tenant_id=tenant.id, name="Admin", description="Full access", permissions=["*"]
        )
        user = User(
            tenant_id=tenant.id,
            email=email.lower(),
            full_name=full_name,
            password_hash=hash_password(password),
        )
        s.add_all([admin_role, user])
        await s.flush()
        s.add(UserRole(tenant_id=tenant.id, user_id=user.id, role_id=admin_role.id))
    await engine.dispose()
    print(f"Created tenant '{slug}' with admin {email}")


async def rotate_credentials() -> None:
    from app.db import dispose_engine
    from app.integrations.connections import rotate_all_credentials

    counts = await rotate_all_credentials()
    await dispose_engine()
    print(f"Re-encrypted {counts['rotated']} stored credential(s) under key "
          f"{counts['current']}; {counts['already']} were already on it.")


def main() -> None:
    p = argparse.ArgumentParser(prog="app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    ct = sub.add_parser("create-tenant")
    ct.add_argument("--name", required=True)
    ct.add_argument("--slug", required=True)
    ct.add_argument("--admin-email", required=True)
    ct.add_argument("--admin-name", required=True)
    ct.add_argument("--admin-password", required=True)
    nk = sub.add_parser("new-credential-key")
    nk.add_argument("--id", help="key id (default k<today>)")
    sub.add_parser("rotate-credentials")
    a = p.parse_args()
    if a.cmd == "create-tenant":
        asyncio.run(create_tenant(a.name, a.slug, a.admin_email, a.admin_name, a.admin_password))
    elif a.cmd == "new-credential-key":
        from app.core.secrets import new_key_spec

        print("Add this FIRST in CREDENTIAL_KEYS in backend/.env (keep older keys after it "
              "until rotate-credentials has run):")
        print(new_key_spec(a.id))
    elif a.cmd == "rotate-credentials":
        asyncio.run(rotate_credentials())


if __name__ == "__main__":
    main()
