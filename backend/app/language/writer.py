"""Writing a message natively in the customer's language (decisions L5 A, L7 A).

The Writing Assistant gets the brief, the customer, the message being answered, the
company's tone guide for the language and the glossary. It writes in the language
directly (the prompt forbids drafting in English first). Its draft is checked
(app/language/glossary.py); if anything is wrong it rewrites once with the problems
pointed out. What is still wrong comes back with the draft: a person sees it before
sending, and an agent's message waits for a language review.

Each model call is in the audit trail with its prompt and answer (R2), under the
Writing Assistant's run.
"""

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import controls, runtime
from app.agents.registry import AGENTS, load_definitions
from app.core.errors import AppError
from app.language import LANGUAGES, WRITING, glossary, name, tone
from app.language.agent import AGENT_KEY
from app.llm import gateway
from app.models import Account, Contact, Message, Tenant

PURPOSES = {
    "promotional": "a promotional or follow-up message (an offer, news, or following up on "
                   "a quote or visit)",
    "transactional": "a service message about their order, invoice, delivery or something "
                     "they asked for",
    "reply": "a reply to the customer's message below",
}


@dataclass
class Draft:
    subject: str
    body: str
    language: str
    problems: list[str] = field(default_factory=list)
    rewritten: bool = False
    run_id: uuid.UUID | None = None


def available() -> bool:
    return gateway.tier_available("writing")


def _parse(text: str) -> tuple[str, str]:
    raw = text.strip()
    fence = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        data = json.loads(fence.group(0) if fence else raw)
        return str(data.get("subject") or "").strip(), str(data.get("body") or "").strip()
    except (ValueError, AttributeError):
        return "", raw


def _system(company: str, language: str, purpose: str, tone_lines: list[str],
            glossary_lines: list[str]) -> str:
    lang = LANGUAGES[language]
    native = "" if language == "en" else (
        f" Compose directly in {lang.name}, as a native speaker writing to a business "
        f"customer would; do not write it in English first and translate it. Use {lang.name} "
        f"script ({lang.native}).")
    return "\n".join([
        f"You write emails for {company}, a business in India, to its business customers.",
        f"Write in {lang.name}.{native}",
        f"This is {PURPOSES[purpose]}.",
        *tone_lines, *glossary_lines,
        "Use only facts in the brief: no invented prices, dates, offers or promises. If "
        "something is not known, leave it out; never leave a placeholder such as [Name].",
        "Do not mention AI; Forge adds that note itself.",
        'Answer with JSON only: {"subject": "...", "body": "..."}',
    ])


async def write(session: AsyncSession, tenant_id: uuid.UUID, *, language: str, purpose: str,
                brief: str, contact: Contact | None = None, in_reply_to: Message | None = None,
                sender_name: str | None = None, trigger: str = "compose",
                trigger_ref: str | None = None) -> Draft:
    load_definitions()
    if language not in WRITING:
        raise AppError(f"Forge writes in {', '.join(name(c) for c in WRITING)} for now "
                       "(decision L5)")
    if purpose not in PURPOSES:
        raise AppError("Unknown message purpose")
    if not brief or not brief.strip():
        raise AppError("Say what the message should say")
    if not available():
        raise AppError("Writing needs the Gemini key (GEMINI_API_KEY in backend/.env, "
                       "open question D3)")
    for ks in await controls.active_switches(session):
        if ks.scope == "tenant" or (ks.scope == "agent" and ks.agent_key == AGENT_KEY):
            raise AppError(f"The Writing Assistant is stopped ({ks.reason})")
    ctx = await runtime.start_run(session, tenant_id, AGENT_KEY, trigger, trigger_ref)
    tenant = await session.get(Tenant, tenant_id)
    account = await session.get(Account, contact.account_id) \
        if contact and contact.account_id else None
    t = await tone.load(session, language)
    terms = await glossary.terms(session)
    system = _system(tenant.name if tenant else "the company", language, purpose,
                     tone.instructions(t), glossary.instructions(language, terms))
    lines = []
    if contact is not None:
        lines.append(f"Customer: {contact.full_name}"
                     + (f", {contact.designation}" if contact.designation else "")
                     + (f", at {account.name}" if account else ""))
        ctx.saw("record", {"full_name": contact.full_name,
                           "account": account.name if account else None},
                record_type="contact", record_id=contact.id)
    if in_reply_to is not None:
        lines.append(f"Their message:\nSubject: {in_reply_to.subject or ''}\n"
                     f"{in_reply_to.body}")
        ctx.saw("message", {"subject": in_reply_to.subject, "body": in_reply_to.body},
                record_type="message", record_id=in_reply_to.id)
    lines.append(f"Brief: {brief.strip()}")
    if sender_name:
        lines.append(f"Sign it from: {sender_name}")
    messages = [{"role": "system", "content": system}, {"role": "user", "content":
                                                        "\n\n".join(lines)}]
    kw: dict[str, Any] = {"record_type": "contact" if contact else None,
                          "record_id": contact.id if contact else None,
                          "temperature": 0.4, "response_format": {"type": "json_object"}}
    res = await runtime.ask_model(session, ctx, task="writer.draft", messages=messages, **kw)
    subject, body = _parse(res.text)
    problems = glossary.problems(subject, body, language, terms, brief)
    rewritten = False
    if problems:
        rewritten = True
        messages += [{"role": "assistant", "content": res.text},
                     {"role": "user", "content": "Your draft has these problems:\n- "
                      + "\n- ".join(problems) + "\nWrite it again with them fixed. "
                      "Same JSON form."}]
        res = await runtime.ask_model(session, ctx, task="writer.rewrite", messages=messages,
                                      **kw)
        subject, body = _parse(res.text)
        problems = glossary.problems(subject, body, language, terms, brief)
    await runtime.finish_run(session, ctx, summary={
        "language": language, "purpose": purpose, "rewritten": rewritten,
        "problems_left": len(problems)})
    return Draft(subject=subject[:300], body=body, language=language, problems=problems,
                 rewritten=rewritten, run_id=ctx.run_id)


def agent_name() -> str:
    return AGENTS[AGENT_KEY].name
