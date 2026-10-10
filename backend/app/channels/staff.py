"""Approval cards by email (decision H5): whoever may decide a card also gets an email
with a link to it, alongside the browser notification. The email cannot approve
anything: the card is opened in Forge, where its full content is shown and its
fingerprint checked (Step 5 safeguard 6). Each company can turn these emails off on
the Channel settings screen. They go through the same sending path as customer
messages (the simulator until the company switches email to live)."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import rules, send
from app.config import get_settings
from app.models import User

ACTOR = "system:inbox"


async def email(session: AsyncSession, tenant_id: uuid.UUID, user_ids: set[uuid.UUID], *,
                title: str, body: str, url: str, key: str) -> int:
    """Queue one email per person; `key` makes a repeated call send nothing new."""
    if not user_ids or not (await rules.load(session, tenant_id))["staff_email"]:
        return 0
    link = get_settings().app_base_url.rstrip("/") + url
    n = 0
    for user in await session.scalars(select(User).where(User.id.in_(user_ids),
                                                         User.is_active.is_(True))):
        text = (f"{body}\n\nOpen it in Forge: {link}\n\nDecide on the card itself: it shows "
                "everything behind the decision. This email cannot approve anything.")
        msg = await send.draft(session, tenant_id, purpose="staff", subject=title[:300],
                               body=text, created_by=ACTOR, user_id=user.id,
                               feature="inbox.notify", idempotency_key=f"{key}:{user.id}"[:100])
        if msg.status == "draft":
            await send.queue(session, msg, actor=ACTOR)
            n += 1
    return n
