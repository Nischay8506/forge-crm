"""Speech to text (decision L10 A): Whisper on Groq through the LLM gateway, with the
company's glossary as hints so names come out right. The provider is a route in
app/llm/routing.py ("speech"), so Sarvam AI can be added there if Telugu comes out
poorly. The audio is not kept."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.language import LANGUAGES, glossary
from app.language.script import detect
from app.llm import gateway

MAX_BYTES = 20 * 1024 * 1024
TYPES = ("audio/webm", "audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav", "audio/x-wav",
         "audio/m4a", "audio/x-m4a", "video/webm")


def available() -> bool:
    return gateway.tier_available("speech")


async def transcribe(session: AsyncSession, tenant_id: uuid.UUID, *, audio: bytes,
                     filename: str, content_type: str, language: str | None = None
                     ) -> dict[str, Any]:
    if not audio:
        raise AppError("The recording is empty")
    if len(audio) > MAX_BYTES:
        raise AppError("The recording is too long (20 MB at most)")
    base_type = (content_type or "").split(";")[0].strip().lower()
    if base_type not in TYPES:
        raise AppError("Send the recording as webm, ogg, mp3, m4a or wav")
    if language is not None and language not in LANGUAGES:
        raise AppError("Unknown language")
    if not available():
        raise AppError("Speech to text needs the Groq key (GROQ_API_KEY in backend/.env, "
                       "open question D3)")
    terms = await glossary.terms(session)
    hints = [t.term for t in terms if t.kind == "keep"]
    hints += [t.translation for t in terms if t.kind == "translate"
              and t.language == language and t.translation]
    prompt = ", ".join(hints)[:600] or None
    res = await gateway.transcribe(session, tenant_id, task="speech.dictate", audio=audio,
                                   filename=filename or "recording.webm",
                                   content_type=base_type, language=language, prompt=prompt)
    found = detect(res.text)
    return {"text": res.text.strip(), "language": found.language or language,
            "sure": found.sure, "seconds": res.seconds, "provider": res.provider,
            "model": res.model}
