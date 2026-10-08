"""Request dependencies: authentication, tenant scoping and permission checks.

Order matters: the JWT gives the tenant id, the transaction is scoped to that
tenant (RLS) BEFORE any tenant table is read, then the user and roles load.
"""

from dataclasses import dataclass, field
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Forbidden, Unauthorized
from app.core.permissions import has_permission
from app.core.security import verify_token
from app.db import get_db, set_tenant
from app.models import Role, User, UserRole

bearer = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    user: User
    tenant_id: UUID
    permissions: set[str] = field(default_factory=set)


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    if creds is None:
        raise Unauthorized("Authentication required")
    payload = verify_token(creds.credentials, "access")
    if payload is None:
        raise Unauthorized("Invalid or expired token")

    tenant_id = UUID(payload["tid"])
    await set_tenant(db, tenant_id)

    user = await db.scalar(
        select(User).where(User.id == UUID(payload["sub"]), User.is_active.is_(True))
    )
    if user is None:
        raise Unauthorized("User not found or deactivated")

    rows = await db.scalars(
        select(Role.permissions)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user.id)
    )
    perms: set[str] = set()
    for p in rows:
        perms.update(p)
    return CurrentUser(user=user, tenant_id=tenant_id, permissions=perms)


def require_permission(code: str):
    """Dependency factory: `Depends(require_permission("users.read"))`."""

    async def _check(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not has_permission(current.permissions, code):
            raise Forbidden(f"Missing permission: {code}")
        return current

    return _check
