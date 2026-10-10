"""The company's glossary (decision L7 A) and the checks every AI-written message
passes before it may go.

    keep       written exactly as it is in every language (brand, product and
               company names): if the brief mentions it, the message must contain it
    translate  in one language, always written one fixed way: the English term must
               not appear in a message in that language
    forbid     never used, in any language

The text checks also catch a message in the wrong language or script, and anything
left to fill in. A message that fails is rewritten once with the problems pointed
out; if it still fails, a person reviews it (app/language/review.py).
"""

import re
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import chain
from app.core.errors import AppError
from app.language import LANGUAGES, name
from app.language.script import detect
from app.models import GlossaryTerm

KINDS = {"keep": "Keep as it is", "translate": "Always translate as",
         "forbid": "Never use"}
PLACEHOLDER = re.compile(r"\{\{.*?\}\}|\[(?:name|customer|company|date|amount|insert[^\]]*)\]"
                         r"|<insert[^>]*>|\bTODO\b|XXX", re.IGNORECASE)


async def terms(session: AsyncSession) -> list[GlossaryTerm]:
    return list(await session.scalars(select(GlossaryTerm).order_by(
        GlossaryTerm.kind, func.lower(GlossaryTerm.term))))


def view(t: GlossaryTerm) -> dict[str, Any]:
    return {"id": str(t.id), "term": t.term, "kind": t.kind, "language": t.language,
            "translation": t.translation, "note": t.note, "created_by": t.created_by,
            "created_at": t.created_at}


async def add(session: AsyncSession, tenant_id: uuid.UUID, data: dict[str, Any],
              actor: str) -> GlossaryTerm:
    term = str(data.get("term") or "").strip()
    kind = data.get("kind")
    language = data.get("language") or None
    translation = str(data.get("translation") or "").strip() or None
    if not term or len(term) > 100:
        raise AppError("Give the term (up to 100 characters)")
    if kind not in KINDS:
        raise AppError("Choose keep, translate or forbid")
    if kind == "translate" and (language not in LANGUAGES or language == "en"
                                or not translation):
        raise AppError("A fixed translation needs the language and how to write it")
    if kind != "translate":
        language, translation = None, None
    dup = await session.scalar(select(GlossaryTerm.id).where(
        func.lower(GlossaryTerm.term) == term.lower(),
        func.coalesce(GlossaryTerm.language, "") == (language or "")))
    if dup:
        raise AppError("This term is already in the glossary")
    t = GlossaryTerm(tenant_id=tenant_id, term=term, kind=kind, language=language,
                     translation=translation, created_by=actor,
                     note=(str(data.get("note") or "").strip()[:300] or None))
    session.add(t)
    await session.flush()
    await chain.append(session, tenant_id, "language.glossary_added", actor=actor,
                       data={"glossary_term_id": t.id, "kind": kind, "term": term,
                             "language": language})
    return t


async def remove(session: AsyncSession, tenant_id: uuid.UUID, term_id: uuid.UUID,
                 actor: str) -> None:
    t = await session.get(GlossaryTerm, term_id)
    if t is None:
        raise AppError("Term not found")
    await chain.append(session, tenant_id, "language.glossary_removed", actor=actor,
                       data={"glossary_term_id": t.id, "kind": t.kind, "term": t.term,
                             "language": t.language})
    await session.delete(t)


def _has(text: str, term: str) -> bool:
    """Whole-word match for terms in Latin letters, plain containment otherwise."""
    if re.fullmatch(r"[\w .&'-]+", term, re.ASCII):
        return re.search(rf"(?<![\w]){re.escape(term)}(?![\w])", text, re.IGNORECASE) \
            is not None
    return term in text


def instructions(language: str, glossary: list[GlossaryTerm]) -> list[str]:
    keep = [t.term for t in glossary if t.kind == "keep"]
    fixed = [(t.term, t.translation) for t in glossary
             if t.kind == "translate" and t.language == language]
    forbid = [t.term for t in glossary if t.kind == "forbid"]
    out = []
    if keep:
        out.append("Write these exactly as they are, never translated or transliterated: "
                   + ", ".join(keep) + ".")
    if fixed:
        out.append("Always write these terms this way: "
                   + "; ".join(f"{a} → {b}" for a, b in fixed) + ".")
    if forbid:
        out.append("Never use these words: " + ", ".join(forbid) + ".")
    return out


def problems(subject: str | None, body: str, language: str, glossary: list[GlossaryTerm],
             brief: str | None = None) -> list[str]:
    """What is wrong with a message in `language` (empty: nothing)."""
    text = f"{subject or ''}\n{body}"
    out: list[str] = []
    if not body.strip():
        return ["The message is empty"]
    want = LANGUAGES.get(language)
    found = detect(body)
    if want is not None and found.script is not None and found.script != want.script:
        out.append(f"It is written in {name(found.language)}, not {want.name}")
    elif want is not None and want.script != "Latn" and found.share < 0.5:
        out.append(f"Most of it is not in {want.name} script")
    elif want is not None and language in ("hi", "mr") and found.sure \
            and found.language != language:
        out.append(f"It reads as {name(found.language)}, not {want.name}")
    for t in glossary:
        if t.kind == "forbid" and _has(text, t.term):
            out.append(f"Uses the forbidden word \"{t.term}\"")
        elif t.kind == "translate" and t.language == language and _has(text, t.term):
            out.append(f"Writes \"{t.term}\"; in {name(language)} it is always "
                       f"\"{t.translation}\"")
        elif t.kind == "keep" and brief and _has(brief, t.term) and not _has(text, t.term):
            out.append(f"\"{t.term}\" must appear exactly as it is")
    hole = PLACEHOLDER.search(text)
    if hole:
        out.append(f"Something is left to fill in: {hole.group(0)}")
    return out
