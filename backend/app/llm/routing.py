"""Model routing (decision 3C): every LLM call names a *tier*, never a model.
The gateway tries the tier's routes in order and falls back on failure.

Change models here, not in agent code. Claude is added later as the frontier
tier by adding routes with provider "anthropic" (the key goes in .env).

PRICES_PER_MTOK: USD per million input/output tokens, used for budgets. They are
left empty on purpose: fill them from each provider's pricing page. A model
without a price is recorded with cost 0 (tokens are still recorded).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    provider: str  # key in app.llm.providers.PROVIDERS
    model: str


ROUTES: dict[str, list[Route]] = {
    # Groq retired Llama 3.1 8B and 3.3 70B for free and developer accounts on 2026-08-16,
    # and Gemini 2.5 Flash and Pro answer 404 to keys created since. Every chain below ends
    # in a model checked with a real key on 2026-10-22.
    # Classification, extraction, short replies
    "fast": [
        Route("groq", "openai/gpt-oss-20b"),
        Route("gemini", "gemini-3.5-flash-lite"),
    ],
    # Planning, drafting, multi-step reasoning
    "reasoning": [
        Route("groq", "openai/gpt-oss-120b"),
        Route("gemini", "gemini-3.8-flash"),
        Route("gemini", "gemini-3.5-flash-lite"),
    ],
    # Customer messages written natively in English and Indian languages (decision L5 A:
    # Gemini writes Indian languages). No Groq fallback: its models write Telugu poorly.
    "writing": [
        Route("gemini", "gemini-3.8-flash"),
        Route("gemini", "gemini-3.5-flash-lite"),
    ],
    # Reading incoming email (M1, F1.1): labelling and extracting the requirement from
    # English, Hindi, Telugu and mixed text. Flash first, Flash-Lite when Flash is busy
    # (it answers 503 at peak times on the free tier), then Groq.
    "extraction": [
        Route("gemini", "gemini-3.8-flash"),
        Route("gemini", "gemini-3.5-flash-lite"),
        Route("groq", "openai/gpt-oss-120b"),
    ],
    # Reading pictures: scanned PDFs, photos and drawings attached to enquiries (N10 A).
    # Gemini only; the Groq models do not read images.
    "vision": [
        Route("gemini", "gemini-3.8-flash"),
        Route("gemini", "gemini-3.5-flash-lite"),
    ],
    # Speech to text (decision L10 A): Whisper on Groq. Sarvam AI can be added as a
    # provider here if Telugu comes out poorly.
    "speech": [
        Route("groq", "whisper-large-v3"),
    ],
    # Text embeddings (Data Steward meaning match, decision S2A). Vectors from different
    # models cannot be compared, so callers store the model next to each vector.
    "embedding": [
        Route("gemini", "gemini-embedding-001"),
    ],
}

PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    # "groq/openai/gpt-oss-20b": (input_usd, output_usd),
}
