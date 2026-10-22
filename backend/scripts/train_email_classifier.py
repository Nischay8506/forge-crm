"""Train only on demo_train; report routing accuracy on untouched demo_test.

Run from backend: uv run python scripts/train_email_classifier.py
An exit status of 1 means the >95% synthetic benchmark target was not reached.
"""

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.enquiries.classification.model import DIRECTORY, LABELS, predict, train  # noqa: E402


def read(name):
    return [json.loads(line) for line in (DIRECTORY / name).read_text(
        encoding="utf-8").splitlines() if line.strip()]


def fingerprint(row):
    return row["subject"].strip().lower(), row["body"].strip().lower()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("validation", "test"), default="test")
    args = parser.parse_args()
    training, test = read("demo_train.jsonl"), read(f"demo_{args.split}.jsonl")
    if {fingerprint(r) for r in training} & {fingerprint(r) for r in test}:
        raise ValueError("Train/test overlap")
    model = train(training)
    model["training_sha256"] = hashlib.sha256(
        (DIRECTORY / "demo_train.jsonl").read_bytes()).hexdigest()
    confusion = {k: Counter({label: 0 for label in LABELS}) for k in LABELS}
    failures = []
    for row in test:
        result = predict(model, row["subject"], row["body"])
        confusion[row["label"]][result.category] += 1
        if row["label"] != result.category:
            failures.append({"id": row["id"], "expected": row["label"],
                             "actual": result.category, "score": result.score})
    accuracy = (len(test) - len(failures)) / len(test)
    per_class = {}
    for label in LABELS:
        tp = confusion[label][label]
        predicted = sum(confusion[k][label] for k in LABELS)
        actual = sum(confusion[label].values())
        per_class[label] = {"precision": tp / predicted if predicted else 0,
                            "recall": tp / actual if actual else 0, "support": actual}
    report = {"scope": "English synthetic demonstration only; not real-mail accuracy",
              "split": args.split,
              "training_rows": len(training), "held_out_rows": len(test),
              "accuracy": accuracy, "target": ">0.95", "passes": accuracy > 0.95,
              "per_class": per_class, "confusion": confusion, "failures": failures,
              "training_sha256": model["training_sha256"]}
    (DIRECTORY / f"{args.split}_evaluation.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    if report["passes"]:
        (DIRECTORY / "model.json").write_text(json.dumps(model, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passes"] else 1


if __name__ == "__main__":
    sys.exit(main())
