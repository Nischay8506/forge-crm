"""Telling people about cards (decision H5): a new card or the next step of a chain
goes to whoever may decide it (and their stand-ins), a reminder at 75% of the time,
and a card past its deadline also goes to the next role up (H4).

Notifications leave through the outbox, so a card whose transaction rolls back is
never announced, and sending (slow, may fail) happens in the worker, not in the
request that created the card. Each one goes as a browser push and, since Step 6, as
an email with a link to the card (app/channels/staff.py; no approving from email).
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import TOOLS
from app.events.outbox import publish, subscribe
from app.models import Decision, OutboxEvent

EVENT = "inbox.notify"


async def notify(session: AsyncSession, d: Decision, reason: str,
                 **extra: Any) -> None:
    """reason: new | step | reminder | overdue | handed."""
    await publish(session, d.tenant_id, EVENT, {
        "decision_id": str(d.id), "reason": reason, "step": d.step_index,
        **{k: str(v) for k, v in extra.items()}})


async def tell(session: AsyncSession, tenant_id: uuid.UUID, user_ids: set[uuid.UUID],
               title: str, body: str, url: str, tag: str) -> None:
    """A message not tied to one card's deciders (e.g. a bottleneck alert)."""
    await publish(session, tenant_id, EVENT, {
        "reason": "message", "user_ids": sorted(str(u) for u in user_ids), "title": title,
        "body": body, "url": url, "tag": tag})


def _message(d: Decision, title: str) -> dict[str, Any]:
    return {"title": title, "body": d.title, "url": f"/decisions?card={d.id}",
            "tag": f"decision-{d.id}"}


async def _on_notify(session: AsyncSession, event: OutboxEvent) -> None:
    from app.approvals import push
    from app.approvals.calendar import load
    from app.approvals.chain import deciders, escalation_targets
    from app.channels import staff

    sent = 0

    async def tell_people(user_ids: set[uuid.UUID], m: dict[str, Any]) -> None:
        nonlocal sent
        sent += 1
        await push.send(session, user_ids, m)
        await staff.email(session, event.tenant_id, user_ids, title=m["title"], body=m["body"],
                          url=m["url"], key=f"inbox:{event.id}:{sent}")

    p = event.payload
    if p.get("reason") == "message":
        await tell_people({uuid.UUID(u) for u in p["user_ids"]},
                          {k: p[k] for k in ("title", "body", "url", "tag")})
        return
    d = await session.get(Decision, uuid.UUID(p["decision_id"]))
    # A card decided (or moved on) before the worker got here needs no message.
    if d is None or d.status != "pending" or d.step_index != p.get("step"):
        return
    tool = TOOLS.get(d.tool_key)
    what = tool.title if tool else "A decision"
    reason = p["reason"]
    if reason == "handed":
        await tell_people({uuid.UUID(p["to_user_id"])}, _message(d, f"Handed to you: {what}"))
        return
    who = await deciders(session, d)
    if reason == "new":
        await tell_people(who, _message(d, f"Waiting for you: {what}"))
    elif reason == "step":
        await tell_people(who, _message(
            d, f"Your turn: step {d.step_index + 1} of {len(d.steps)} ({what})"))
    elif reason == "reminder":
        await tell_people(who, _message(d, f"Reminder: {what} is due soon"))
    elif reason == "overdue":
        await tell_people(who, _message(d, f"Past its deadline: {what}"))
        cfg = await load(session, d.tenant_id)
        above = await escalation_targets(session, d, cfg["escalation_role_id"]) - who
        await tell_people(above, _message(d, f"Escalated to you: {what} is past its "
                                             "deadline"))


def register() -> None:
    subscribe(EVENT)(_on_notify)
