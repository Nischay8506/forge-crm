"""Small TF-IDF nearest-centroid model, loaded from inspectable JSON (no pickle).

Scores are routing heuristics, NOT calibrated probabilities. Unknown vocabulary,
non-English text, short messages and conflicting evidence abstain to review.
The model only reads content; it never executes instructions found in email.
"""

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DIRECTORY = Path(__file__).parent
LABELS = ("enquiry", "other", "review")
STOP = set("a an the to of for in on at and or is are be it this that we you your our i "
           "with from by as please hello hi dear regards thanks thank sir team".split())


def features(subject: str, body: str) -> Counter:
    text = (subject[:2000] + "\n" + body[:20000]).lower()
    words = re.findall(r"[a-z]+", text)
    words = [w for w in words if w not in STOP and len(w) > 1]
    # Binary term presence keeps long signatures / repeated words from dominating.
    return Counter(set(words + [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]))


def train(rows: list[dict]) -> dict:
    centroids = {label: Counter() for label in LABELS}
    documents = Counter()
    frequency = Counter()
    for row in rows:
        documents[row["label"]] += 1
        frequency.update(features(row["subject"], row["body"]).keys())
    if any(not documents[label] for label in LABELS):
        raise ValueError("Training needs examples of all three labels")
    idf = {w: math.log((1 + len(rows)) / (1 + n)) + 1 for w, n in frequency.items()}
    for row in rows:
        vector = {w: idf[w] for w in features(row["subject"], row["body"])}
        norm = math.sqrt(sum(v * v for v in vector.values())) or 1
        for word, value in vector.items():
            centroids[row["label"]][word] += value / norm
    for label in LABELS:
        norm = math.sqrt(sum(v * v for v in centroids[label].values())) or 1
        centroids[label] = {w: v / norm for w, v in centroids[label].items()}
    return {"version": 1, "algorithm": "tfidf_nearest_centroid",
            "language": "English demo data", "minimum_score": 0.07,
            "minimum_margin": 0.025, "documents": dict(documents),
            "idf": idf, "centroids": centroids}


@dataclass(frozen=True)
class Decision:
    category: str
    score: float
    reason: str


@lru_cache(maxsize=1)
def load() -> dict:
    return json.loads((DIRECTORY / "model.json").read_text(encoding="utf-8"))


def predict(model: dict, subject: str, body: str) -> Decision:
    text = (subject + "\n" + body).lower()
    # Direction matters: a seller's catalogue is not a customer asking us to quote.
    # Do not suppress mixed messages that also explicitly ask to buy from us.
    if (re.search(r"\b(we sell|our company sells|buy from (?:us|our))\b", text)
            and not re.search(r"\b(we need|we require|please quote|request a quotation)\b", text)):
        return Decision("other", 1.0, "Explicit supplier sales pitch, not a buying request")
    terms = features(subject, body)
    vocab = set(model["idf"])
    known = terms.keys() & vocab
    letters = [c for c in subject + body if c.isalpha()]
    if (len(known) < 2 or (terms and len(known) / len(terms) < 0.08)
            or (letters and sum(ord(c) > 127 for c in letters) / len(letters) > 0.25)):
        return Decision("review", 0.0, "Too little familiar English content; check manually")
    vector = {w: model["idf"][w] for w in known}
    norm = math.sqrt(sum(v * v for v in vector.values())) or 1
    ranked = sorted(((sum(v * model["centroids"][label].get(w, 0)
                          for w, v in vector.items()) / norm, label)
                     for label in LABELS), reverse=True)
    score, label = ranked[0]
    if score < model["minimum_score"] or score - ranked[1][0] < model["minimum_margin"]:
        return Decision("review", round(score, 4), "Mixed evidence; check manually")
    return Decision(label, round(score, 4),
                    f"Local classifier: {label} (routing score {score:.2f}; not calibrated)")


def classify(subject: str, body: str, *, attachment_text: str = "",
             has_attachments: bool = False) -> Decision:
    try:
        decision = predict(load(), subject, body + "\n" + attachment_text[:18000])
    except (OSError, ValueError, KeyError, TypeError):
        return Decision("review", 0.0, "Local classifier unavailable; check manually")
    if has_attachments and not attachment_text.strip() and decision.category != "enquiry":
        return Decision("review", decision.score,
                        "Unread attachment may contain a requirement; check manually")
    return decision
