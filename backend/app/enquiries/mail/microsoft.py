"""Microsoft 365 through Microsoft Graph (read-only). The cursor is Graph's delta link
for the inbox: each check asks what was added since. Push (optional) is a Graph
subscription to the inbox; it only makes Forge check sooner."""

import secrets as _random
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import get_settings
from app.enquiries.mail import oauth
from app.enquiries.mail.base import Batch, Fetched, MailError
from app.models import Mailbox

GRAPH = "https://graph.microsoft.com/v1.0"
PER_CHECK = 25


async def _get(c: Any, token: str, url: str) -> Any:
    r = await c.get(url, headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 401:
        raise MailError("Microsoft no longer accepts Forge's sign-in; connect it again",
                        signed_out=True)
    if r.status_code == 410:
        raise LookupError("delta expired")
    if r.status_code != 200:
        raise MailError(f"Microsoft Graph answered {r.status_code}")
    return r


async def me(token: str) -> dict[str, Any]:
    async with oauth.client() as c:
        return (await _get(c, token, f"{GRAPH}/me?$select=mail,userPrincipalName")).json()


def _start(since: datetime) -> str:
    stamp = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (f"{GRAPH}/me/mailFolders/inbox/messages/delta?$select=id,receivedDateTime"
            f"&$filter=receivedDateTime+ge+{stamp}")


async def check(mb: Mailbox, token: str, cursor: dict[str, Any]) -> Batch:
    async with oauth.client() as c:
        url = cursor.get("delta_link") or cursor.get("next_link") or _start(
            mb.created_at or datetime.now(UTC))
        found: list[tuple[str, str | None]] = []
        try:
            while True:
                data = (await _get(c, token, url)).json()
                for m in data.get("value") or []:
                    if "@removed" not in m and m.get("id"):
                        found.append((m["id"], m.get("receivedDateTime")))
                if data.get("@odata.nextLink") and len(found) <= PER_CHECK:
                    url = data["@odata.nextLink"]
                    continue
                break
        except LookupError:
            since = (mb.last_ok_at or datetime.now(UTC)) - timedelta(hours=1)
            return Batch([], {"next_link": _start(since)}, more=True)
        cursor_out: dict[str, Any] = {}
        if data.get("@odata.deltaLink"):
            cursor_out["delta_link"] = data["@odata.deltaLink"]
        else:
            cursor_out["next_link"] = data.get("@odata.nextLink") or url
        batch = Batch([], cursor_out, more="next_link" in cursor_out)
        for mid, received in found:
            raw = (await _get(c, token, f"{GRAPH}/me/messages/{mid}/$value")).content
            at = datetime.fromisoformat(received.replace("Z", "+00:00")) if received else None
            batch.emails.append(Fetched(mid, raw, at))
        return batch


async def subscribe(token: str, tenant_id: Any) -> tuple[str, datetime, str] | None:
    """A Graph subscription to new inbox mail (needs an https PUBLIC_BASE_URL)."""
    base = get_settings().public_base_url.rstrip("/")
    if not base.startswith("https://"):
        return None
    secret = _random.token_urlsafe(24)
    expires = datetime.now(UTC) + timedelta(days=2)
    async with oauth.client() as c:
        r = await c.post(f"{GRAPH}/subscriptions", headers={"Authorization": f"Bearer {token}"},
                         json={"changeType": "created",
                               "notificationUrl":
                                   f"{base}/api/v1/public/mail/push/microsoft/{tenant_id}",
                               "resource": "me/mailFolders('inbox')/messages",
                               "expirationDateTime": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "clientState": secret})
    if r.status_code not in (200, 201):
        raise MailError(f"Microsoft did not start notifications ({r.status_code})")
    return r.json()["id"], expires, secret
