"""Browser push notifications (decision H5): desktop and Android Chrome, and iPhone
once Forge is added to the home screen. Off until VAPID keys are set in backend/.env
(`uv run python -m app.cli new-push-keys`). WhatsApp and email arrive in Step 6.

Messages carry no personal data beyond the card's title, and are encrypted for the
browser, so the push service in between cannot read them.
"""

import asyncio
import base64
import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import PushSubscription

log = logging.getLogger(__name__)

GONE = (404, 410)  # the browser unsubscribed or the subscription expired
MAX_FAILURES = 5


def configured() -> bool:
    s = get_settings()
    return bool(s.vapid_public_key and s.vapid_private_key)


def new_keys() -> tuple[str, str]:
    """(public, private) for VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY, base64url."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    k = ec.generate_private_key(ec.SECP256R1())
    raw = k.private_numbers().private_value.to_bytes(32, "big")
    pub = k.public_key().public_bytes(serialization.Encoding.X962,
                                      serialization.PublicFormat.UncompressedPoint)

    def b64(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    return b64(pub), b64(raw)


def _webpush(sub: dict[str, Any], message: dict[str, Any]) -> int | None:
    """Send one message; returns the HTTP status of a refusal, None when delivered."""
    from pywebpush import WebPushException, webpush

    s = get_settings()
    try:
        webpush(subscription_info=sub, data=json.dumps(message),
                vapid_private_key=s.vapid_private_key,
                vapid_claims={"sub": s.vapid_subject}, ttl=3600, timeout=10)
    except WebPushException as exc:
        return exc.response.status_code if exc.response is not None else 0
    return None


# Swapped out in tests; never reaches a real push service there.
sender: Callable[[dict[str, Any], dict[str, Any]], int | None] = _webpush


async def send(session: AsyncSession, user_ids: set[uuid.UUID], message: dict[str, Any]) -> int:
    """Push one message to every browser these people turned notifications on in.
    Returns how many browsers took it. Subscriptions the browser dropped are removed."""
    if not user_ids or not configured():
        return 0
    subs = list(await session.scalars(select(PushSubscription).where(
        PushSubscription.user_id.in_(user_ids))))
    sent = 0
    for sub in subs:
        info = {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}
        try:
            refused = await asyncio.to_thread(sender, info, message)
        except Exception:  # noqa: BLE001 - one browser must not stop the others
            log.exception("push to subscription %s failed", sub.id)
            refused = 0
        if refused is None:
            sub.last_sent_at, sub.failures = datetime.now(UTC), 0
            sent += 1
        elif refused in GONE or sub.failures + 1 >= MAX_FAILURES:
            await session.delete(sub)
        else:
            sub.failures += 1
    return sent
