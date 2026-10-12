"""The commitment guard (decision N7 A): until the product knowledge base (F1.6)
exists, nothing Forge sends about an enquiry may promise a price, a discount, a
delivery date or that we can make or have something. A message that does is
blocked outright; no one can approve it as it stands, so a person edits it first.

The acknowledgement Forge writes itself never contains these; the guard is for text
a person changed (a counter on the card) and anything written later.
"""

import re

_NUM = r"\d[\d,]*(?:\.\d+)?"

CHECKS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("a price", re.compile(
        rf"(₹|\brs\.?\s*{_NUM}|\binr\s*{_NUM}|{_NUM}\s*(?:/-|rupees|lakhs?|crores?)\b|"
        rf"\b(?:price|rate|cost|quoted?)\s+(?:is|of|will be|would be|comes to|at)\s+"
        rf"(?:rs|inr|₹)?\s*{_NUM}|"
        rf"\bper\s+(?:kg|mt|tonne|ton|piece|pc|nos?|unit)\b.*{_NUM}|"
        # 'रेट 52000', 'कीमत: रु. 52000', 'ధర రూ. 52000'; not 'का रेट [ENQ-2026-00012]'
        r"(?:कीमत|रेट|दाम|ధర|రేటు)\s*(?:[:=-]|है|होगा|रहेगा)?\s*(?:₹|रु\.?|రూ\.?|rs\.?)?\s*\d)",
        re.IGNORECASE)),
    ("a discount", re.compile(
        rf"(\bdiscount|\b{_NUM}\s*%\s*off\b|\bspecial (?:price|rate|offer)\b|"
        r"छूट|डिस्काउंट|తగ్గింపు|డిస్కౌంట్)", re.IGNORECASE)),
    ("a delivery date", re.compile(
        rf"(\b(?:deliver|delivered|dispatch|dispatched|ship|shipped|supply|supplied|ready)\s+"
        rf"(?:it\s+|them\s+|the material\s+|the goods\s+)?(?:by|within|on|before|in)\s+"
        rf"(?:{_NUM}|\w+day|tomorrow|next|the end|end of|\d{{1,2}}(?:st|nd|rd|th)?)|"
        rf"\blead\s*time\s+(?:is|of|will be)\b|\bin\s+{_NUM}\s+(?:working\s+)?(?:days|weeks)\b|"
        r"\bex[- ]?stock\b|\bimmediate (?:delivery|dispatch)\b|"
        r"दिनों में|दिन में डिलीवरी|రోజుల్లో)", re.IGNORECASE)),
    ("that we can make or supply it", re.compile(
        r"(\bwe (?:can|will|shall|are able to) (?:supply|make|manufacture|deliver|provide|"
        r"meet|fabricate|machine|cast)\b|\b(?:is|are) (?:available|in stock)\b|"
        r"\bwe have (?:it|them|this|stock|the material)\b|\bin stock\b|"
        r"हम (?:सप्लाई|बना|दे) सकते|स्टॉक में|మేము (?:సరఫరా|తయారు) చేయగలము|స్టాక్‌లో)",
        re.IGNORECASE)),
)


def claims(*texts: str | None) -> list[str]:
    """What the text promises that it must not, e.g. ["a price", "a delivery date"]."""
    text = "\n".join(t for t in texts if t)
    return [what for what, rx in CHECKS if rx.search(text)]


def reason(found: list[str]) -> str:
    return ("This message promises " + _join(found) + ". Until Forge has the product "
            "knowledge base (F1.6), messages about an enquiry must not promise prices, "
            "discounts, delivery dates or what we can supply (decision N7 A). Edit it out "
            "first.")


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
