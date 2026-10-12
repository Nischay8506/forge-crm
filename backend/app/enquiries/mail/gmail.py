"""Gmail through the Gmail API (read-only). The cursor is the mailbox's history id:
each check asks Gmail what was added to the inbox since then. Push (optional) comes
through Google Pub/Sub when GMAIL_PUBSUB_TOPIC is set; it only makes Forge check
sooner."""

import base64
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import get_settings
from app.enquiries.mail import oauth
from app.enquiries.mail.base import Batch, Fetched, MailError
from app.models import Mailbox

API = "https://gmail.googleapis.com/gmail/v1/users/me"
PER_CHECK = 25


async def _get(c: Any, token: str, url: str, **params: Any) -> dict[str, Any]:
    r = await c.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 401:
        raise MailError("Gmail no longer accepts Forge's sign-in; connect it again",
                        signed_out=True)
    if r.status_code == 404 and "history" in url:
        raise LookupError("history too old")
    if r.status_code != 200:
        raise MailError(f"Gmail answered {r.status_code}")
    return r.json()


async def profile(token: str) -> dict[str, Any]:
    async with oauth.client() as c:
        return await _get(c, token, f"{API}/profile")


async def check(mb: Mailbox, token: str, cursor: dict[str, Any]) -> Batch:
    async with oauth.client() as c:
        if not cursor.get("history_id"):
            p = await _get(c, token, f"{API}/profile")
            return Batch([], {"history_id": p["historyId"]})
        newest = cursor["history_id"]
        ids: list[str] = list(cursor.get("pending") or [])
        if not ids:
            try:
                page = None
                while True:
                    data = await _get(c, token, f"{API}/history", startHistoryId=newest,
                                      historyTypes="messageAdded", labelId="INBOX",
                                      **({"pageToken": page} if page else {}))
                    for h in data.get("history") or []:
                        for added in h.get("messagesAdded") or []:
                            mid = added.get("message", {}).get("id")
                            if mid and mid not in ids:
                                ids.append(mid)
                    page = data.get("nextPageToken")
                    if not page:
                        newest = data.get("historyId", newest)
                        break
            except LookupError:
                # Gmail forgot that far back (days offline): read the inbox since the
                # last good check instead, so nothing is missed.
                since = int(((mb.last_ok_at or datetime.now(UTC)) - timedelta(hours=1))
                            .timestamp())
                data = await _get(c, token, f"{API}/messages", q=f"in:inbox after:{since}",
                                  maxResults=500)
                ids = [m["id"] for m in data.get("messages") or []][::-1]
                newest = (await _get(c, token, f"{API}/profile"))["historyId"]
        take, rest = ids[:PER_CHECK], ids[PER_CHECK:]
        out: dict[str, Any] = {"history_id": newest}
        if rest:
            out["pending"] = rest  # read on the next checks, oldest first
        batch = Batch([], out, more=bool(rest))
        for mid in take:
            try:
                m = await _get(c, token, f"{API}/messages/{mid}", format="raw")
            except MailError as e:
                if "404" in str(e):
                    continue  # deleted before we read it
                raise
            raw = base64.urlsafe_b64decode(m["raw"] + "=" * (-len(m["raw"]) % 4))
            at = datetime.fromtimestamp(int(m.get("internalDate", 0)) / 1000, UTC) \
                if m.get("internalDate") else None
            batch.emails.append(Fetched(mid, raw, at))
        return batch


async def watch(token: str) -> tuple[str | None, datetime | None]:
    """Ask Gmail to tell Pub/Sub about new mail (renewed before it runs out, 7 days)."""
    topic = get_settings().gmail_pubsub_topic
    if not topic:
        return None, None
    async with oauth.client() as c:
        r = await c.post(f"{API}/watch", headers={"Authorization": f"Bearer {token}"},
                         json={"topicName": topic, "labelIds": ["INBOX"]})
    if r.status_code != 200:
        raise MailError(f"Gmail did not start notifications ({r.status_code})")
    data = r.json()
    return str(data.get("historyId")), datetime.fromtimestamp(
        int(data["expiration"]) / 1000, UTC)
