from fastapi import APIRouter

from app.api.v1 import (
    accounts,
    agents,
    audit,
    auth,
    channels,
    decisions,
    enquiries,
    integrations,
    privacy,
    products,
    public,
    steward,
    users,
)

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
router.include_router(users.router)
router.include_router(accounts.router)
router.include_router(steward.router)
router.include_router(agents.router)
router.include_router(decisions.router)
router.include_router(audit.router)
router.include_router(integrations.router)
router.include_router(privacy.router)
router.include_router(channels.router)
router.include_router(enquiries.router)
router.include_router(products.router)
router.include_router(public.router)
