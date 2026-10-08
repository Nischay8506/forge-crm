from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.core.errors import Unauthorized
from app.core.security import (
    create_access_token,
    create_refresh_token,
    verify_password,
    verify_token,
)
from app.db import get_db, set_tenant
from app.models import Tenant, User

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    company: str  # tenant slug, e.g. "acme"
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    id: str
    email: str
    full_name: str
    tenant_id: str
    permissions: list[str]


def _tokens(user: User) -> TokenPair:
    uid, tid = str(user.id), str(user.tenant_id)
    return TokenPair(
        access_token=create_access_token(uid, tid), refresh_token=create_refresh_token(uid, tid)
    )


@router.post("/login", response_model=TokenPair)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenPair:
    tenant = await db.scalar(
        select(Tenant).where(Tenant.slug == body.company.lower(), Tenant.is_active.is_(True))
    )
    if tenant is None:
        raise Unauthorized("Invalid company, email or password")
    await set_tenant(db, tenant.id)
    user = await db.scalar(
        select(User).where(User.email == body.email.lower(), User.is_active.is_(True))
    )
    if user is None or not verify_password(body.password, user.password_hash):
        raise Unauthorized("Invalid company, email or password")
    return _tokens(user)


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenPair:
    payload = verify_token(body.refresh_token, "refresh")
    if payload is None:
        raise Unauthorized("Invalid or expired refresh token")
    await set_tenant(db, UUID(payload["tid"]))
    user = await db.scalar(
        select(User).where(User.id == UUID(payload["sub"]), User.is_active.is_(True))
    )
    if user is None:
        raise Unauthorized("User not found or deactivated")
    return _tokens(user)


@router.get("/me", response_model=MeResponse)
async def me(current: CurrentUser = Depends(get_current_user)) -> MeResponse:
    u = current.user
    return MeResponse(
        id=str(u.id),
        email=u.email,
        full_name=u.full_name,
        tenant_id=str(u.tenant_id),
        permissions=sorted(current.permissions),
    )
