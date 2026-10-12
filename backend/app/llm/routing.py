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
    # Classification, extraction, short replies
    "fast": [
        Route("groq", "llama-3.1-8b-instant"),
        Route("gemini", "gemini-2.5-flash"),
    ],
    # Planning, drafting, multi-step reasoning
    "reasoning": [
        Route("gemini", "gemini-2.5-pro"),
        Route("groq", "llama-3.3-70b-versatile"),
    ],
    # Customer messages written natively in English and Indian languages (decision L5 A:
    # Gemini writes Indian languages). No Groq fallback: its models write Telugu poorly.
    "writing": [
        Route("gemini", "gemini-2.5-pro"),
        Route("gemini", "gemini-2.5-flash"),
    ],
    # Reading incoming email (M1, F1.1): labelling and extracting the requirement from
    # English, Hindi, Telugu and mixed text. Gemini Flash first (it reads Indian
    # languages well), the larger Groq model as the fallback.
    "extraction": [
        Route("gemini", "gemini-2.5-flash"),
        Route("groq", "llama-3.3-70b-versatile"),
    ],
    # Reading pictures: scanned PDFs, photos and drawings attached to enquiries (N10 A).
    # Gemini only; the Groq models do not read images.
    "vision": [
        Route("gemini", "gemini-2.5-flash"),
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
    # "groq/llama-3.1-8b-instant": (input_usd, output_usd),
}
