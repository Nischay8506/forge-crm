"""Which language a text is in, from its script (decision L6).

Each script but one belongs to one language. Devanagari is shared by Hindi and
Marathi, so a few very common words decide between them; with too little to go on
the answer is Hindi, marked unsure. Latin script is English (romanised Indian
languages come later). A message mixing scripts goes by the script most of its
letters are in; English words inside an Indian-language message are normal.
"""

import unicodedata
from dataclasses import dataclass

# Unicode blocks of the major Indian scripts.
BLOCKS = (
    (0x0900, 0x097F, "Deva"), (0x0980, 0x09FF, "Beng"), (0x0A00, 0x0A7F, "Guru"),
    (0x0A80, 0x0AFF, "Gujr"), (0x0B00, 0x0B7F, "Orya"), (0x0B80, 0x0BFF, "Taml"),
    (0x0C00, 0x0C7F, "Telu"), (0x0C80, 0x0CFF, "Knda"), (0x0D00, 0x0D7F, "Mlym"),
)
SCRIPT_LANGUAGE = {"Latn": "en", "Beng": "bn", "Guru": "pa", "Gujr": "gu", "Orya": "or",
                   "Taml": "ta", "Telu": "te", "Knda": "kn", "Mlym": "ml"}

# Very common words found in one of the two and not the other.
HINDI_WORDS = frozenset("है हैं और नहीं का की के को में से तक पर ने मैं हम था थी थे भी रहा "
                        "रही क्या यह वह जो भेजें भेजिए".split())
MARATHI_WORDS = frozenset("आहे आहेत आणि नाही मी तुम्ही आम्ही काय होते झाले केले करा "
                          "पर्यंत आपण पाठवा पाठवावे आहोत".split())


@dataclass(frozen=True)
class Detection:
    language: str | None  # None: no letters to go on
    script: str | None
    sure: bool
    share: float  # of letters in that script

    @property
    def label(self) -> str:
        from app.language import name

        return name(self.language) + ("" if self.sure else " (unsure)")


def script_of(ch: str) -> str | None:
    cp = ord(ch)
    for lo, hi, sc in BLOCKS:
        if lo <= cp <= hi:
            return sc
    if ch.isalpha() and unicodedata.name(ch, "").startswith("LATIN"):
        return "Latn"
    return None


def counts(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for ch in text:
        if not (ch.isalpha() or unicodedata.category(ch) in ("Mn", "Mc")):
            continue
        sc = script_of(ch)
        if sc:
            out[sc] = out.get(sc, 0) + 1
    return out


def _devanagari(text: str) -> tuple[str, bool]:
    words = {w.strip("।,.!?;:\"'()") for w in text.split()}
    hi, mr = len(words & HINDI_WORDS), len(words & MARATHI_WORDS)
    if "ळ" in text:
        mr += 2  # the letter ळ is common in Marathi and rare in Hindi
    def sure(won: int, lost: int) -> bool:
        return won - lost >= 2 or (won >= 1 and lost == 0 and len(words) <= 6)

    if mr > hi:
        return "mr", sure(mr, hi)
    return "hi", sure(hi, mr)


def detect(text: str | None) -> Detection:
    c = counts(text or "")
    total = sum(c.values())
    if not total:
        return Detection(None, None, False, 0.0)
    # Indian scripts first: a Telugu message with English product names is Telugu.
    indian = {k: v for k, v in c.items() if k != "Latn"}
    if indian and sum(indian.values()) >= max(3, 0.2 * total):
        sc = max(indian, key=lambda k: indian[k])
    else:
        sc = "Latn"
    share = c[sc] / total
    if sc == "Deva":
        lang, sure = _devanagari(text or "")
        return Detection(lang, sc, sure and share >= 0.5, share)
    return Detection(SCRIPT_LANGUAGE[sc], sc, share >= 0.5 and c[sc] >= 3, share)
