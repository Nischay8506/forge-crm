"""The Enquiry Agent (M1): reads incoming email, opens enquiries, acknowledges them,
nudges for missing details, parks the quiet ones and merges sure duplicates.

Its actions go through the policy gate like any agent's. The acknowledgement and the
nudges are customer messages: strategic accounts start at L0 (a person checks and
sends each one) and tenders are always drafted for a person (decision N7 A), and no
message may promise a price, discount, delivery date or what we can supply (the
commitment guard, app/enquiries/guard.py). A company can change the levels per tier
on the Agents screen.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.controls import account_tiers
from app.agents.registry import (
    L0,
    L2,
    AgentContext,
    AgentDef,
    ToolDef,
    Touches,
    register_agent,
    register_tool,
)
from app.core.errors import AppError
from app.enquiries import guard
from app.models import Contact, Enquiry, Message
from app.models.enquiries import OPEN_STATUSES

AGENT_KEY = "enquiry"
ACK_TOOL = "enquiry.acknowledge"
NUDGE_TOOL = "enquiry.nudge"
PARK_TOOL = "enquiry.park"
MERGE_TOOL = "enquiry.merge"
TENDER_RULE = "Tenders are always drafted for a person to check and send (decision N7 A)"


class MessageArgs(BaseModel):
    enquiry_id: uuid.UUID
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=8000)
    language: str = Field(default="en", pattern="^(en|hi|te)$")
    question_keys: list[str] = Field(default_factory=list, max_length=10)


class EnquiryArgs(BaseModel):
    enquiry_id: uuid.UUID


class MergeArgs(BaseModel):
    enquiry_id: uuid.UUID  # the later one, merged into the earlier one
    into_id: uuid.UUID
    reasons: list[str] = Field(default_factory=list, max_length=6)


async def _enquiry(session: AsyncSession, enquiry_id: uuid.UUID) -> Enquiry | None:
    return await session.get(Enquiry, enquiry_id)


async def _facts(session: AsyncSession, a: Any) -> dict[str, Any]:
    enq = await _enquiry(session, a.enquiry_id)
    facts: dict[str, Any] = {"is_tender": "yes" if enq is not None and enq.is_tender else "no"}
    if enq is not None and enq.account_id:
        facts["account_tier"] = (await account_tiers(session, [enq.account_id]))[enq.account_id]
    else:
        facts["account_tier"] = "standard"
    if isinstance(a, MessageArgs):
        facts["claims"] = guard.claims(a.subject, a.body)
    return facts


async def _touches(session: AsyncSession, a: Any) -> Touches:
    enq = await _enquiry(session, a.enquiry_id)
    if enq is None:
        return Touches(records=(("enquiry", a.enquiry_id),))
    recs: list[tuple[str, Any]] = [("enquiry", enq.id)]
    if enq.contact_id:
        recs.append(("contact", enq.contact_id))
    return Touches(accounts=(enq.account_id,) if enq.account_id else (), records=tuple(recs))


def _who(enq: Enquiry, contact: Contact | None) -> str:
    return (contact.full_name if contact else None) or enq.customer.get("contact_name") or \
        enq.customer.get("company") or enq.customer.get("email") or "the customer"


async def _describe_message(session: AsyncSession, a: MessageArgs, facts: dict[str, Any],
                            what: str) -> str:
    enq = await _enquiry(session, a.enquiry_id)
    if enq is None:
        return f"Send the {what}"
    contact = await session.get(Contact, enq.contact_id) if enq.contact_id else None
    return f"Send the {what} for {enq.ref} to {_who(enq, contact)}"


async def _ack_describe(session: AsyncSession, a: MessageArgs, facts: dict[str, Any]) -> str:
    return await _describe_message(session, a, facts, "acknowledgement")


async def _nudge_describe(session: AsyncSession, a: MessageArgs, facts: dict[str, Any]) -> str:
    return await _describe_message(session, a, facts, "reminder")


def _no_promises(facts: dict[str, Any]) -> str | None:
    found = facts.get("claims") or []
    return guard.reason(found) if found else None


def _tender(facts: dict[str, Any]) -> str | None:
    return TENDER_RULE if facts.get("is_tender") == "yes" else None


async def first_inbound(session: AsyncSession, enq: Enquiry) -> Message | None:
    return await session.scalar(select(Message).where(
        Message.enquiry_id == enq.id, Message.direction == "in")
        .order_by(Message.created_at).limit(1))


async def send_about(session: AsyncSession, enq: Enquiry, a: MessageArgs, feature: str,
                     key: str | None, *, actor: str, run_id: uuid.UUID | None, ai: bool,
                     agent_sent: bool) -> Message:
    """Write the message as asked and send it through every channel check. `ai`: the
    text came from Forge (marked as such); `agent_sent`: no person approved it."""
    from app.channels import send

    inbound = await first_inbound(session, enq)
    test = inbound is not None and inbound.provider == "test"
    msg = await send.draft(
        session, enq.tenant_id, purpose="reply" if inbound else "transactional",
        subject=a.subject, body=a.body, created_by=actor,
        contact_id=enq.contact_id, account_id=enq.account_id, language=a.language,
        ai_generated=ai, disclosure=send.disclosure_for(a.language, agent=agent_sent)
        if ai else None, run_id=run_id, feature=feature,
        in_reply_to_id=inbound.id if inbound else None, idempotency_key=key,
        simulator_only=test)
    msg.enquiry_id = enq.id
    if msg.status == "draft":
        # Fixed text written by people (or edited by one): nothing for the writing review.
        await send.queue(session, msg, actor=actor, review=False)
    return msg


async def acknowledge(session: AsyncSession, enq: Enquiry, a: MessageArgs, *, actor: str,
                      run_id: uuid.UUID | None = None, ai: bool = True,
                      agent_sent: bool = True) -> Message:
    """Send the acknowledgement and note the questions it asks."""
    from app.enquiries import ack, records, timers

    if enq.status not in OPEN_STATUSES:
        raise AppError(f"{enq.ref} is {enq.status}, so it is not acknowledged")
    found = guard.claims(a.subject, a.body)
    if found:
        raise AppError(guard.reason(found))
    # The agent's acknowledgement goes once; a person may send another.
    msg = await send_about(session, enq, a, "enquiry.acknowledge",
                           f"enquiry-ack:{enq.id}" if ai else None,
                           actor=actor, run_id=run_id, ai=ai, agent_sent=agent_sent)
    if msg.status == "blocked":
        raise AppError(f"The acknowledgement was blocked: {msg.reason}")
    now = datetime.now(UTC)
    items = await ack.checklist(session, enq)
    ack.ask_questions(enq, items, a.question_keys, now)
    if a.question_keys:
        enq.status, enq.waiting_on, enq.waiting_since = "waiting_customer", "customer", now
        enq.nudges, enq.last_nudge_at = 0, None
        await timers.pause(session, enq, "quote", now)
    elif enq.status == "new":
        enq.status = "open"
    await records.event(session, enq, "acknowledged", actor, message_id=str(msg.id),
                        status=msg.status, questions=a.question_keys)
    return msg


async def _ack_run(session: AsyncSession, ctx: AgentContext, a: MessageArgs) -> dict[str, Any]:
    enq = await session.get(Enquiry, a.enquiry_id, with_for_update=True)
    if enq is None:
        raise AppError("Enquiry not found")
    msg = await acknowledge(session, enq, a, actor=ctx.approved_by or ctx.actor,
                            run_id=ctx.run_id, agent_sent=ctx.approved_by is None)
    return {"message_id": str(msg.id), "status": msg.status, "enquiry_id": str(enq.id)}


async def nudge(session: AsyncSession, enq: Enquiry, a: MessageArgs, *, actor: str,
                run_id: uuid.UUID | None = None, ai: bool = True,
                agent_sent: bool = True) -> Message:
    from app.enquiries import records

    if enq.status != "waiting_customer":
        raise AppError(f"{enq.ref} is not waiting on the customer any more")
    found = guard.claims(a.subject, a.body)
    if found:
        raise AppError(guard.reason(found))
    n = enq.nudges + 1
    msg = await send_about(session, enq, a, "enquiry.nudge",
                           f"enquiry-nudge:{enq.id}:{n}" if ai else None,
                           actor=actor, run_id=run_id, ai=ai, agent_sent=agent_sent)
    if msg.status == "blocked":
        raise AppError(f"The reminder was blocked: {msg.reason}")
    enq.nudges, enq.last_nudge_at = n, datetime.now(UTC)
    await records.event(session, enq, "nudged", actor, message_id=str(msg.id), nudge=n)
    return msg


async def _nudge_run(session: AsyncSession, ctx: AgentContext, a: MessageArgs
                     ) -> dict[str, Any]:
    enq = await session.get(Enquiry, a.enquiry_id, with_for_update=True)
    if enq is None:
        raise AppError("Enquiry not found")
    msg = await nudge(session, enq, a, actor=ctx.approved_by or ctx.actor, run_id=ctx.run_id,
                      agent_sent=ctx.approved_by is None)
    return {"message_id": str(msg.id), "status": msg.status, "enquiry_id": str(enq.id)}


async def _park_describe(session: AsyncSession, a: EnquiryArgs, facts: dict[str, Any]) -> str:
    enq = await _enquiry(session, a.enquiry_id)
    return f"Park {enq.ref if enq else 'the enquiry'}: no answer from the customer"


async def _park_run(session: AsyncSession, ctx: AgentContext, a: EnquiryArgs
                    ) -> dict[str, Any]:
    from app.enquiries import records

    enq = await session.get(Enquiry, a.enquiry_id, with_for_update=True)
    if enq is None or enq.status != "waiting_customer":
        raise AppError("Only an enquiry waiting on the customer is parked")
    enq.status, enq.parked_at = "parked", datetime.now(UTC)
    await records.event(session, enq, "parked", ctx.approved_by or ctx.actor,
                        nudges=enq.nudges)
    return {"enquiry_id": str(enq.id), "status": enq.status}


async def _merge_touches(session: AsyncSession, a: MergeArgs) -> Touches:
    out = await _touches(session, a)
    other = await _enquiry(session, a.into_id)
    accounts = {*out.accounts, *((other.account_id,) if other and other.account_id else ())}
    return Touches(accounts=tuple(accounts), records=(*out.records, ("enquiry", a.into_id)))


async def _merge_describe(session: AsyncSession, a: MergeArgs, facts: dict[str, Any]) -> str:
    one, two = await _enquiry(session, a.enquiry_id), await _enquiry(session, a.into_id)
    return f"Merge {one.ref if one else '?'} into {two.ref if two else '?'} (the same enquiry)"


async def _merge_run(session: AsyncSession, ctx: AgentContext, a: MergeArgs) -> dict[str, Any]:
    from app.enquiries import dedupe

    one = await session.get(Enquiry, a.enquiry_id, with_for_update=True)
    two = await session.get(Enquiry, a.into_id, with_for_update=True)
    if one is None or two is None:
        raise AppError("Enquiry not found")
    await dedupe.merge(session, one, two, actor=ctx.approved_by or ctx.actor,
                       reasons=a.reasons, automatic=ctx.approved_by is None, run_id=ctx.run_id)
    return {"enquiry_id": str(one.id), "into_id": str(two.id)}


register_tool(ToolDef(
    key=ACK_TOOL, title="Acknowledge an enquiry",
    description="Sends the customer a short thank-you with the enquiry's reference number "
                "and questions for the details still needed to quote. It never mentions "
                "prices, discounts, delivery dates or what we can supply.",
    args=MessageArgs, run=_ack_run, facts=("account_tier", "is_tender"),
    compute_facts=_facts, touches=_touches, describe=_ack_describe,
    evidence_class="customer_money", counter_fields=("subject", "body"),
    ask_when=_tender, block_when=_no_promises,
))
register_tool(ToolDef(
    key=NUDGE_TOOL, title="Remind a customer about missing details",
    description="Reminds the customer which details are still needed to quote (day 2 and "
                "day 5 by default). Same rules as the acknowledgement.",
    args=MessageArgs, run=_nudge_run, facts=("account_tier", "is_tender"),
    compute_facts=_facts, touches=_touches, describe=_nudge_describe,
    evidence_class="customer_money", counter_fields=("subject", "body"),
    ask_when=_tender, block_when=_no_promises,
))
register_tool(ToolDef(
    key=PARK_TOOL, title="Park a quiet enquiry",
    description="Moves an enquiry the customer has not answered (day 7 by default) to "
                "Parked. It can be opened again at any time.",
    args=EnquiryArgs, run=_park_run, facts=("account_tier", "is_tender"),
    compute_facts=_facts, touches=_touches, describe=_park_describe,
))
register_tool(ToolDef(
    key=MERGE_TOOL, title="Merge a duplicate enquiry",
    description="Marks a later enquiry as the same as an earlier one when Forge is sure: "
                "the same email thread, or the same phone number with the same items, "
                "within 30 days. The merge can be undone.",
    args=MergeArgs, run=_merge_run, facts=("account_tier", "is_tender"),
    compute_facts=_facts, touches=_merge_touches, describe=_merge_describe,
))

register_agent(AgentDef(
    key=AGENT_KEY,
    name="Enquiry Agent",
    purpose="Reads the sales mailbox, opens an enquiry for each request to quote, and gives "
            "it to the right person. It thanks the customer with a reference number and "
            "asks for the details still needed to quote, reminds them if they do not "
            "answer, parks enquiries that go quiet, and merges the same enquiry sent twice. "
            "It never promises prices, discounts, delivery dates or what we can supply.",
    tools=(ACK_TOOL, NUDGE_TOOL, PARK_TOOL, MERGE_TOOL),
    default_level=L2, max_level=L2,
    triggers=("an email arrives in the connected mailbox", "the Test mailbox",
              "a customer's reply", "the daily nudge and park check"),
    model_tier="extraction",
    tier_levels=(("strategic", L0),),
))
