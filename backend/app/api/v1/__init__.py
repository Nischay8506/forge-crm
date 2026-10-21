from fastapi import APIRouter

from app.api.v1 import (
    accounts,
    agents,
    audit,
    auth,
    channels,
    costs,
    decisions,
    doa,
    enquiries,
    integrations,
    privacy,
    products,
    public,
    quotes,
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
router.include_router(quotes.router)
router.include_router(costs.router)
router.include_router(doa.router)
router.include_router(public.router)
