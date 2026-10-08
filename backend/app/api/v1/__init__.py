from fastapi import APIRouter

from app.api.v1 import accounts, auth, steward, users

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
router.include_router(users.router)
router.include_router(accounts.router)
router.include_router(steward.router)
