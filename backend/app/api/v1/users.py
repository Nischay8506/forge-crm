"""Users and roles. Roles hold permission codes (app/core/permissions.py) and are
also who a policy rule can name to decide ("ask Finance").
"""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import chain
from app.core.deps import CurrentUser, require_permission
from app.core.errors import AppError, NotFound, ValidationFailed
from app.core.permissions import ALL, PERMISSIONS
from app.core.security import hash_password
from app.db import get_db
from app.models import Role, User, UserRole

router = APIRouter(tags=["users"])


def _actor(u: CurrentUser) -> str:
    return f"user:{u.user.id}"


class RoleRef(BaseModel):
    id: str
    name: str


class UserOut(BaseModel):
    id: str
    email: str
    full_name: str
    is_active: bool
    roles: list[RoleRef] = []


class RoleOut(BaseModel):
    id: str
    name: str
    description: str | None
    permissions: list[str]
    user_count: int


async def _roles_by_user(db: AsyncSession) -> dict[uuid.UUID, list[RoleRef]]:
    rows = await db.execute(select(UserRole.user_id, Role.id, Role.name)
                            .join(Role, Role.id == UserRole.role_id).order_by(Role.name))
    out: dict[uuid.UUID, list[RoleRef]] = {}
    for user_id, role_id, name in rows:
        out.setdefault(user_id, []).append(RoleRef(id=str(role_id), name=name))
    return out


def _user(u: User, roles: dict[uuid.UUID, list[RoleRef]]) -> UserOut:
    return UserOut(id=str(u.id), email=u.email, full_name=u.full_name, is_active=u.is_active,
                   roles=roles.get(u.id, []))


@router.get("/users", response_model=list[UserOut])
async def list_users(
    _: CurrentUser = Depends(require_permission("users.read")),
    db: AsyncSession = Depends(get_db),
) -> list[UserOut]:
    # RLS already limits rows to the caller's tenant; no tenant filter needed here.
    users = await db.scalars(select(User).order_by(User.email))
    roles = await _roles_by_user(db)
    return [_user(u, roles) for u in users]


async def _check_roles(db: AsyncSession, role_ids: list[uuid.UUID]) -> None:
    found = set(await db.scalars(select(Role.id).where(Role.id.in_(role_ids))))
    if missing := [str(r) for r in role_ids if r not in found]:
        raise NotFound(f"Unknown role(s): {', '.join(missing)}")


async def _set_roles(db: AsyncSession, current: CurrentUser, user: User,
                     role_ids: list[uuid.UUID]) -> None:
    await _check_roles(db, role_ids)
    await db.execute(delete(UserRole).where(UserRole.user_id == user.id))
    for rid in dict.fromkeys(role_ids):
        db.add(UserRole(tenant_id=current.tenant_id, user_id=user.id, role_id=rid))
    await db.flush()
    await chain.append(db, current.tenant_id, "user.roles_changed", actor=_actor(current),
                       data={"user_id": user.id, "role_ids": list(dict.fromkeys(role_ids))})


class UserIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=200)
    role_ids: list[uuid.UUID] = []


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(
    body: UserIn, current: CurrentUser = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    email = body.email.lower()
    if await db.scalar(select(User.id).where(User.email == email)):
        raise AppError("A user with this email already exists")
    user = User(tenant_id=current.tenant_id, email=email, full_name=body.full_name.strip(),
                password_hash=hash_password(body.password))
    db.add(user)
    await db.flush()
    await _set_roles(db, current, user, body.role_ids)
    return _user(user, await _roles_by_user(db))


class UserRolesIn(BaseModel):
    role_ids: list[uuid.UUID]


@router.put("/users/{user_id}/roles", response_model=UserOut)
async def set_user_roles(
    user_id: uuid.UUID, body: UserRolesIn,
    current: CurrentUser = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFound("User not found")
    if user.id == current.user.id and ALL in current.permissions:
        # Don't let the last full-access admin lock themselves out.
        admins = await db.scalar(
            select(func.count()).select_from(UserRole).join(Role, Role.id == UserRole.role_id)
            .where(Role.permissions.contains([ALL])))
        keeps_all = await db.scalar(select(func.count()).select_from(Role).where(
            Role.id.in_(body.role_ids), Role.permissions.contains([ALL])))
        if admins <= 1 and not keeps_all:
            raise AppError("You are the only full-access admin; keep a full-access role")
    await _set_roles(db, current, user, body.role_ids)
    return _user(user, await _roles_by_user(db))


@router.get("/permissions")
async def list_permissions(
    _: CurrentUser = Depends(require_permission("users.read")),
) -> list[dict[str, str]]:
    return [{"code": ALL, "description": "Full access"}] + [
        {"code": c, "description": d} for c, d in PERMISSIONS.items()]


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(
    _: CurrentUser = Depends(require_permission("users.read")),
    db: AsyncSession = Depends(get_db),
) -> list[RoleOut]:
    counts = dict((await db.execute(select(UserRole.role_id, func.count())
                                    .group_by(UserRole.role_id))).all())
    roles = await db.scalars(select(Role).order_by(Role.name))
    return [RoleOut(id=str(r.id), name=r.name, description=r.description,
                    permissions=list(r.permissions), user_count=counts.get(r.id, 0))
            for r in roles]


class RoleIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    permissions: list[str] = []


def _check_permissions(codes: list[str]) -> list[str]:
    if bad := [c for c in codes if c != ALL and c not in PERMISSIONS]:
        raise ValidationFailed([f"Unknown permission '{c}'" for c in bad])
    return sorted(set(codes))


@router.post("/roles", response_model=RoleOut, status_code=201)
async def create_role(
    body: RoleIn, current: CurrentUser = Depends(require_permission("roles.manage")),
    db: AsyncSession = Depends(get_db),
) -> RoleOut:
    name = body.name.strip()
    if await db.scalar(select(Role.id).where(Role.name == name)):
        raise AppError("A role with this name already exists")
    role = Role(tenant_id=current.tenant_id, name=name, description=body.description,
                permissions=_check_permissions(body.permissions))
    db.add(role)
    await db.flush()
    await chain.append(db, current.tenant_id, "role.created", actor=_actor(current),
                       data={"role_id": role.id, "name": name,
                             "permissions": role.permissions})
    return RoleOut(id=str(role.id), name=role.name, description=role.description,
                   permissions=role.permissions, user_count=0)


@router.put("/roles/{role_id}", response_model=RoleOut)
async def update_role(
    role_id: uuid.UUID, body: RoleIn,
    current: CurrentUser = Depends(require_permission("roles.manage")),
    db: AsyncSession = Depends(get_db),
) -> RoleOut:
    role = await db.get(Role, role_id)
    if role is None:
        raise NotFound("Role not found")
    perms = _check_permissions(body.permissions)
    if ALL in role.permissions and ALL not in perms:
        raise AppError("The full-access role cannot lose full access; create another role")
    before = {"name": role.name, "permissions": list(role.permissions)}
    role.name, role.description, role.permissions = body.name.strip(), body.description, perms
    await db.flush()
    await chain.append(db, current.tenant_id, "role.updated", actor=_actor(current),
                       data={"role_id": role.id, "before": before,
                             "after": {"name": role.name, "permissions": perms}})
    count = await db.scalar(select(func.count()).select_from(UserRole)
                            .where(UserRole.role_id == role.id))
    return RoleOut(id=str(role.id), name=role.name, description=role.description,
                   permissions=role.permissions, user_count=count or 0)
