"""Admin commands, run as the schema owner (tenants can only be created this way):

    uv run python -m app.cli create-tenant --name "Acme Pumps" --slug acme \
        --admin-email admin@acme.test --admin-name "Acme Admin" --admin-password '...'

Keys for stored ERP credentials (app/core/secrets.py):

    uv run python -m app.cli new-credential-key      # prints a key for CREDENTIAL_KEYS
    uv run python -m app.cli rotate-credentials      # re-encrypts under the first key

Key that signs the audit chain's hourly seals (app/audit/seals.py):

    uv run python -m app.cli new-seal-key            # prints a key for AUDIT_SEAL_KEYS

Key that encrypts the audit trail's evidence (app/audit/evidence.py):

    uv run python -m app.cli new-evidence-key        # prints a key for EVIDENCE_KEYS
    uv run python -m app.cli rotate-evidence         # re-encrypts under the first key

Browser push notifications for the approval inbox (app/approvals/push.py):

    uv run python -m app.cli new-push-keys           # prints VAPID_PUBLIC_KEY/PRIVATE_KEY

Prepared cards for the approval-inbox usability test (docs/USABILITY_TEST.md):

    uv run python -m app.cli seed-usability --slug ut-anita [--password '...']
    uv run python -m app.cli seed-usability --slug ut-anita --refresh
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


async def rotate_evidence() -> None:
    from app.audit.evidence import rotate_all
    from app.db import dispose_engine

    counts = await rotate_all()
    await dispose_engine()
    print(f"Re-encrypted {counts['rotated']} audit evidence row(s) under key "
          f"{counts['current']}; {counts['already']} were already on it.")


async def seed_usability(slug: str, password: str, refresh_only: bool) -> None:
    from app.approvals import usability
    from app.db import dispose_engine

    if refresh_only:
        await usability.refresh(slug)
        await dispose_engine()
        print(f"ERP figures for '{slug}' now read as current (for the next 15 minutes).")
        return
    done = await usability.seed(slug, password)
    await dispose_engine()
    print(f"Company '{done.slug}' is ready with {done.cards} waiting cards.")
    print(f"Log in with company '{done.slug}' and password '{done.password}':")
    for email, role in done.people.items():
        print(f"  {email}  {role}")


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
    sk = sub.add_parser("new-seal-key")
    sk.add_argument("--id", help="key id (default s<today>)")
    ek = sub.add_parser("new-evidence-key")
    ek.add_argument("--id", help="key id (default e<today>)")
    sub.add_parser("rotate-evidence")
    sub.add_parser("new-push-keys")
    su = sub.add_parser("seed-usability")
    su.add_argument("--slug", required=True, help="a new company slug for this session")
    su.add_argument("--password", default="Usability-2026",
                    help="password for every test login (default Usability-2026)")
    su.add_argument("--refresh", action="store_true",
                    help="only bring the session's fresh ERP figures up to date")
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
    elif a.cmd == "new-seal-key":
        from app.audit.seals import new_seal_key

        print("Add this FIRST in AUDIT_SEAL_KEYS in backend/.env (keep older keys after it "
              "so their seals can still be checked):")
        print(new_seal_key(a.id))
    elif a.cmd == "new-evidence-key":
        from app.audit.evidence import new_evidence_key

        print("Add this FIRST in EVIDENCE_KEYS in backend/.env (keep older keys after it "
              "until rotate-evidence has run), and keep a copy off this server:")
        print(new_evidence_key(a.id))
    elif a.cmd == "rotate-evidence":
        asyncio.run(rotate_evidence())
    elif a.cmd == "new-push-keys":
        from app.approvals.push import new_keys

        public, private = new_keys()
        print("Add these to backend/.env and restart the API and the worker. Changing them "
              "later means everyone turns notifications on again:")
        print(f"VAPID_PUBLIC_KEY={public}")
        print(f"VAPID_PRIVATE_KEY={private}")
        print("VAPID_SUBJECT=mailto:<an address the push services can reach you at>")
    elif a.cmd == "seed-usability":
        asyncio.run(seed_usability(a.slug, a.password, a.refresh))


if __name__ == "__main__":
    main()
