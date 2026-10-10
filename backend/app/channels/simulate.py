"""The Test inbox (decision L11 A): what the simulator "sent", and the customer's side
of it. A person can reply as the customer (the reply comes in through the same path
a real one will), make the email bounce, or press its unsubscribe link."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import send
from app.channels import statuses as st
from app.channels.adapters.simulator import NAME
from app.core.errors import AppError, NotFound
from app.models import Contact, Message


async def _sent(session: AsyncSession, message_id: uuid.UUID) -> Message:
    msg = await session.get(Message, message_id)
    if msg is None:
        raise NotFound("Message not found")
    if msg.provider != NAME or msg.direction != "out":
        raise AppError("Only messages in the Test inbox can be acted out")
    if msg.status not in (st.SENT, st.DELIVERED, st.READ, st.BOUNCED):
        raise AppError("This message has not been sent yet")
    return msg


async def reply(session: AsyncSession, message_id: uuid.UUID, body: str, actor: str) -> Message:
    msg = await _sent(session, message_id)
    if not body or not body.strip():
        raise AppError("Write the customer's reply")
    return await send.receive(
        session, msg.tenant_id, from_address=msg.to_address or "", body=body.strip(),
        subject=f"Re: {msg.subject or ''}".strip(), provider=NAME,
        provider_ref=f"<in-{uuid.uuid4()}@simulator.test>", in_reply_to_ref=msg.provider_ref,
        source="simulator")


async def bounce(session: AsyncSession, message_id: uuid.UUID, actor: str) -> Message:
    msg = await _sent(session, message_id)
    await send.record_receipt(session, msg, st.BOUNCED, source="simulator", actor=actor,
                              reason="The receiving mail server returned it (simulated)")
    return msg


async def unsubscribe(session: AsyncSession, message_id: uuid.UUID, actor: str) -> bool:
    msg = await _sent(session, message_id)
    if msg.purpose != "promotional" or not msg.contact_id:
        raise AppError("Only promotional emails carry an unsubscribe link")
    contact = await session.get(Contact, msg.contact_id)
    if contact is None or contact.status != "active":
        raise AppError("The contact is no longer active")
    return await send.handle_stop(session, msg.tenant_id, contact, None, how="link")
