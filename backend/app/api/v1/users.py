from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_permission
from app.db import get_db
from app.models import User

router = APIRouter(prefix="/users", tags=["users"])


class UserOut(BaseModel):
    id: str
    email: str
    full_name: str
    is_active: bool


@router.get("", response_model=list[UserOut])
async def list_users(
    _: CurrentUser = Depends(require_permission("users.read")),
    db: AsyncSession = Depends(get_db),
) -> list[UserOut]:
    # RLS already limits rows to the caller's tenant; no tenant filter needed here.
    users = await db.scalars(select(User).order_by(User.email))
    return [
        UserOut(id=str(u.id), email=u.email, full_name=u.full_name, is_active=u.is_active)
        for u in users
    ]
