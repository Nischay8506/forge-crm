"""Sandbox Agent (test only, decision T7): records pretend payments with an amount,
so policy rules on money ("ask a person above ₹1,00,000") can be tried and tested
before real money-moving agents exist. It writes only to sandbox_actions.

It also drafts test messages (decision R7) so a message can be traced back to
everything behind it; a drafted message is marked as written by Forge's AI assistant
and is never sent. Since Step 6 it can also send a test email through the channel
checks, which always lands in the Test inbox (the simulator), never a real mailbox:
that is how an agent's message, the language review and the controls are tried.
"""

import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.controls import account_tiers
from app.agents.registry import (
    L2,
    L3,
    AgentContext,
    AgentDef,
    ToolDef,
    Touches,
    register_agent,
    register_tool,
)
from app.core.format import inr
from app.models import Account, Contact, Message, SandboxAction

AGENT_KEY = "sandbox"
PAYMENT_TOOL = "sandbox.record_payment"
MESSAGE_TOOL = "sandbox.draft_message"
EMAIL_TOOL = "sandbox.send_test_email"
AI_DISCLOSURE = "Sent by our AI assistant"


class PaymentArgs(BaseModel):
    account_id: uuid.UUID | None = None
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    note: str | None = Field(default=None, max_length=300)


async def _facts(session: AsyncSession, a: PaymentArgs) -> dict[str, Any]:
    facts: dict[str, Any] = {"amount": float(a.amount)}
    if a.account_id:
        facts["account_tier"] = (await account_tiers(session, [a.account_id]))[a.account_id]
    return facts


async def _touches(session: AsyncSession, a: PaymentArgs) -> Touches:
    return Touches(accounts=(a.account_id,) if a.account_id else ())


async def _describe(session: AsyncSession, a: PaymentArgs, facts: dict[str, Any]) -> str:
    who = ""
    if a.account_id:
        acc = await session.get(Account, a.account_id)
        who = f" for {acc.name}" if acc else ""
    return f"Record a test payment of {inr(a.amount)}{who}"


async def _run(session: AsyncSession, ctx: AgentContext, a: PaymentArgs) -> dict[str, Any]:
    row = SandboxAction(tenant_id=ctx.tenant_id, account_id=a.account_id, amount=a.amount,
                        note=a.note, created_by=ctx.approved_by or ctx.actor)
    session.add(row)
    await session.flush()
    return {"sandbox_action_id": str(row.id), "amount": str(a.amount)}


register_tool(ToolDef(
    key=PAYMENT_TOOL,
    title="Record a test payment",
    description="Writes a pretend payment to the sandbox table. Nothing real changes.",
    args=PaymentArgs, run=_run, facts=("amount", "account_tier"),
    compute_facts=_facts, touches=_touches, describe=_describe,
    evidence_class="customer_money", counter_fields=("amount",),
))


class MessageArgs(BaseModel):
    account_id: uuid.UUID
    contact_id: uuid.UUID | None = None
    channel: str = Field(default="email", pattern="^(email|whatsapp)$")
    subject: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=4000)


async def _message_touches(session: AsyncSession, a: MessageArgs) -> Touches:
    return Touches(accounts=(a.account_id,),
                   records=(("contact", a.contact_id),) if a.contact_id else ())


async def _message_describe(session: AsyncSession, a: MessageArgs,
                            facts: dict[str, Any]) -> str:
    acc = await session.get(Account, a.account_id)
    return f"Draft a test {a.channel} message to {acc.name if acc else 'an account'} " \
        "(never sent)"


async def _message_run(session: AsyncSession, ctx: AgentContext, a: MessageArgs
                       ) -> dict[str, Any]:
    acc = await session.get(Account, a.account_id)
    if acc is None:
        raise ValueError("Account not found")
    c = None
    if a.contact_id:
        c = await session.get(Contact, a.contact_id)
        if c is None or c.account_id != acc.id:
            raise ValueError("That contact is not at this account")
    greeting = f"Dear {c.full_name},\n\n" if c else ""
    body = a.body or (f"{greeting}Hello from Forge. This is a test message for {acc.name}, "
                      "drafted by the Sandbox Agent. It will never be sent.")
    msg = Message(tenant_id=ctx.tenant_id, account_id=acc.id, contact_id=a.contact_id,
                  channel=a.channel, direction="out", status="draft",
                  subject=a.subject or ("Test message" if a.channel == "email" else None),
                  body=body, ai_generated=True, disclosure=AI_DISCLOSURE, run_id=ctx.run_id,
                  created_by=ctx.approved_by or ctx.actor)
    session.add(msg)
    await session.flush()
    return {"message_id": str(msg.id), "status": msg.status, "channel": msg.channel}


register_tool(ToolDef(
    key=MESSAGE_TOOL,
    title="Draft a test message",
    description="Writes a draft message marked as written by Forge's AI assistant. It is "
                "never sent; it exists to check that a message can be traced back to "
                "everything behind it.",
    args=MessageArgs, run=_message_run, touches=_message_touches,
    describe=_message_describe, evidence_class="customer_money",
))

class TestEmailArgs(BaseModel):
    contact_id: uuid.UUID
    purpose: str = Field(default="transactional", pattern="^(promotional|transactional)$")
    language: str | None = Field(default=None, max_length=10)
    subject: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=4000)
    # With a brief, the Writing Assistant writes it natively in `language` (L5).
    brief: str | None = Field(default=None, max_length=1000)


async def _email_touches(session: AsyncSession, a: TestEmailArgs) -> Touches:
    c = await session.get(Contact, a.contact_id)
    return Touches(accounts=(c.account_id,) if c and c.account_id else (),
                   records=(("contact", a.contact_id),))


async def _email_describe(session: AsyncSession, a: TestEmailArgs,
                          facts: dict[str, Any]) -> str:
    c = await session.get(Contact, a.contact_id)
    return f"Send a test {a.purpose} email to {c.full_name if c else 'a contact'} " \
        "(Test inbox only)"


async def _email_run(session: AsyncSession, ctx: AgentContext, a: TestEmailArgs
                     ) -> dict[str, Any]:
    from app.channels import send
    from app.language import writer

    c = await session.get(Contact, a.contact_id)
    if c is None:
        raise ValueError("Contact not found")
    language = a.language or c.preferred_language or "en"
    problems = None
    if a.brief:
        d = await writer.write(session, ctx.tenant_id, language=language, purpose=a.purpose,
                               brief=a.brief, contact=c, trigger="agent",
                               trigger_ref=str(ctx.run_id))
        subject, body, problems = d.subject, d.body, d.problems
    else:
        subject = a.subject or "Test email from Forge"
        body = a.body or (f"Dear {c.full_name},\n\nThis is a test email from Forge's Sandbox "
                          "Agent. It went only to the Test inbox.")
    msg = await send.draft(session, ctx.tenant_id, purpose=a.purpose, subject=subject,
                           body=body, created_by=ctx.approved_by or ctx.actor, contact_id=c.id,
                           language=language if a.brief else None, ai_generated=True,
                           disclosure=send.disclosure_for(language, agent=True),
                           run_id=ctx.run_id, feature="sandbox.test_email",
                           simulator_only=True)
    if not a.brief:
        msg.disclosure = send.disclosure_for(msg.language, agent=True)
    # Only text the Writing Assistant wrote goes to the language review sample (L11):
    # the fixed test text says nothing about how well the AI writes.
    await send.queue(session, msg, actor=ctx.actor, writing_problems=problems,
                     review=bool(a.brief))
    return {"message_id": str(msg.id), "status": msg.status, "reason": msg.reason}


register_tool(ToolDef(
    key=EMAIL_TOOL,
    title="Send a test email",
    description="Sends a test email to a contact through every channel check (consent, "
                "quiet hours, limits, kill-switches, takeovers). It always lands in the Test "
                "inbox, never a real mailbox.",
    args=TestEmailArgs, run=_email_run, touches=_email_touches,
    describe=_email_describe, evidence_class="customer_money",
))

register_agent(AgentDef(
    key=AGENT_KEY,
    name="Sandbox Agent",
    purpose="Test only. Lets you try policy rules on amounts before real agents that move "
            "money exist, drafts test messages that are never sent, and sends test emails "
            "that only ever reach the Test inbox. It records pretend payments, test drafts "
            "and test emails and nothing else.",
    tools=(PAYMENT_TOOL, MESSAGE_TOOL, EMAIL_TOOL),
    default_level=L2, max_level=L3,
    triggers=("'Try it' on the Agents screen", "tests"),
    test_only=True,
))
