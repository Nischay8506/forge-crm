"""Languages (Step 6, F12.5; decisions L5-L7, L10).

Forge recognises the language of incoming messages by script, for English and the
major Indian languages (L6, Nischay chose "Major scripts" on 2026-10-10): Hindi,
Marathi, Telugu, Tamil, Kannada, Malayalam, Gujarati, Bengali, Punjabi and Odia.
Latin script is read as English; Indian languages typed in English letters, and
minor languages, come later.

Forge writes natively (not translated from English) in English, Hindi and Telugu
for now (L5 A); more writing languages are settings, not code: add the language to
WRITING and give it a starting tone guide in tone.py.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    native: str
    script: str


LANGUAGES: dict[str, Language] = {lang.code: lang for lang in (
    Language("en", "English", "English", "Latn"),
    Language("hi", "Hindi", "हिन्दी", "Deva"),
    Language("mr", "Marathi", "मराठी", "Deva"),
    Language("te", "Telugu", "తెలుగు", "Telu"),
    Language("ta", "Tamil", "தமிழ்", "Taml"),
    Language("kn", "Kannada", "ಕನ್ನಡ", "Knda"),
    Language("ml", "Malayalam", "മലയാളം", "Mlym"),
    Language("gu", "Gujarati", "ગુજરાતી", "Gujr"),
    Language("bn", "Bengali", "বাংলা", "Beng"),
    Language("pa", "Punjabi", "ਪੰਜਾਬੀ", "Guru"),
    Language("or", "Odia", "ଓଡ଼ିଆ", "Orya"),
)}

# Languages Forge writes in (L5 A). Recognising covers all of LANGUAGES.
WRITING = ("en", "hi", "te")


def name(code: str | None) -> str:
    lang = LANGUAGES.get(code or "")
    return lang.name if lang else (code or "Unknown")
