"""Tone guides (decision L7 A): how messages in each language should sound.

Each company has one per language; the starting versions below apply until someone
changes them on the Language settings screen. They are a starting point for native
speakers to correct, not a finished style guide.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import chain
from app.core.errors import AppError
from app.language import LANGUAGES, WRITING
from app.models import ToneGuide

FORMALITY = {"formal": "Formal and respectful", "neutral": "Polite and plain",
             "friendly": "Warm and friendly"}


@dataclass(frozen=True)
class Tone:
    language: str
    formality: str
    greeting: str | None
    sign_off: str | None
    avoid: list[str] = field(default_factory=list)
    notes: str | None = None
    is_default: bool = True
    updated_by: str | None = None
    updated_at: datetime | None = None


DEFAULTS: dict[str, Tone] = {
    "en": Tone("en", "neutral", "Dear {name},", "Regards,\n{sender}",
               ["slang", "exclamation marks in a row", "ALL CAPITALS"],
               "Indian business English: polite and direct. Short paragraphs. Write amounts "
               "as ₹1,50,000 and dates as 12 Oct 2026."),
    "hi": Tone("hi", "formal", "आदरणीय {name} जी,", "धन्यवाद,\n{sender}",
               ["तू, तुम (use आप)", "बहुत भारी संस्कृतनिष्ठ शब्द जब आम बोलचाल का शब्द हो"],
               "Write everyday business Hindi as people in offices speak and write it, in "
               "Devanagari. Address the reader as आप. Common English business words "
               "(invoice, order, GST, delivery) may stay in English where that is what "
               "people say. Amounts as ₹1,50,000."),
    "te": Tone("te", "formal", "గౌరవనీయులైన {name} గారికి,", "ధన్యవాదాలు,\n{sender}",
               ["నువ్వు (use మీరు)", "అతి గ్రాంథిక భాష"],
               "Write natural business Telugu as used in offices in Telangana and Andhra "
               "Pradesh, in Telugu script. Address the reader as మీరు with గారు after names. "
               "Common English business words (invoice, order, GST, delivery) may stay in "
               "English where that is what people say. Amounts as ₹1,50,000."),
}


def _view(row: ToneGuide) -> Tone:
    return Tone(row.language, row.formality, row.greeting, row.sign_off, list(row.avoid),
                row.notes, False, row.updated_by, row.updated_at)


async def load(session: AsyncSession, language: str) -> Tone:
    row = await session.scalar(select(ToneGuide).where(ToneGuide.language == language))
    if row is not None:
        return _view(row)
    return DEFAULTS.get(language) or Tone(language, "neutral", None, None)


async def all_tones(session: AsyncSession) -> list[Tone]:
    rows = {r.language: _view(r) for r in await session.scalars(select(ToneGuide))}
    return [rows.get(code) or DEFAULTS.get(code) or Tone(code, "neutral", None, None)
            for code in WRITING]


async def save(session: AsyncSession, tenant_id: uuid.UUID, language: str,
               data: dict[str, Any], actor: str) -> Tone:
    if language not in LANGUAGES:
        raise AppError("Unknown language")
    if data.get("formality") not in FORMALITY:
        raise AppError("Formality must be formal, neutral or friendly")
    avoid = [str(a).strip()[:100] for a in data.get("avoid") or [] if str(a).strip()][:30]
    row = await session.scalar(select(ToneGuide).where(ToneGuide.language == language))
    if row is None:
        row = ToneGuide(tenant_id=tenant_id, language=language, updated_by=actor,
                        formality="neutral")
        session.add(row)
    row.formality = data["formality"]
    row.greeting = (str(data.get("greeting") or "").strip()[:200] or None)
    row.sign_off = (str(data.get("sign_off") or "").strip()[:200] or None)
    row.avoid = avoid
    row.notes = (str(data.get("notes") or "").strip()[:4000] or None)
    row.updated_by, row.updated_at = actor, datetime.now(UTC)
    await session.flush()
    await chain.append(session, tenant_id, "language.tone_saved", actor=actor,
                       data={"tone_guide_id": row.id, "language": language,
                             "formality": row.formality})
    return _view(row)


def instructions(t: Tone) -> list[str]:
    out = [f"Tone: {FORMALITY.get(t.formality, t.formality)}."]
    if t.greeting:
        out.append(f"Open with a greeting in this style: {t.greeting}")
    if t.sign_off:
        out.append(f"Close in this style: {t.sign_off}")
    if t.avoid:
        out.append("Avoid: " + "; ".join(t.avoid) + ".")
    if t.notes:
        out.append(t.notes)
    return out
