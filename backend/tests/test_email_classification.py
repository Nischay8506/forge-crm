"""Classification, abstention and the classifier -> extraction integration."""
# ruff: noqa: F811

import json

import pytest

from app.channels import send
from app.enquiries import intake, reader
from app.enquiries.classification import classify
from app.enquiries.classification.model import DIRECTORY, features, load, predict, train
from tests.test_enquiries import RFQ, E, _enquiry, _mail, _seller, ai, ctx, reading  # noqa: F401


def test_demo_holdout_accuracy_and_no_exact_overlap():
    def rows(split):
        return [json.loads(x) for x in (DIRECTORY / f"demo_{split}.jsonl").read_text(
            encoding="utf-8").splitlines()]
    training, validation, test = rows("train"), rows("validation"), rows("test")
    for left, right in ((training, test), (training, validation), (validation, test)):
        assert not ({(r["subject"], r["body"]) for r in left}
                    & {(r["subject"], r["body"]) for r in right})
    model = train(training)
    assert model["centroids"] == load()["centroids"]
    assert sum(predict(model, r["subject"], r["body"]).category == r["label"]
               for r in test) / len(test) > 0.95


@pytest.mark.parametrize("subject,body,expected", [
    ("Weekend reading: productivity tips", "Enjoy our newsletter. Unsubscribe below.", "other"),
    ("RFQ for 40 steel pipes", "Please quote ASTM A106 Grade B, 6 metres each.", "enquiry"),
    ("Hello", "Please help.", "review"),
    ("", "", "review"),
    ("मूल्य", "हमें स्टील पाइप चाहिए", "review"),
])
def test_three_way_routing(subject, body, expected):
    assert classify(subject, body).category == expected


def test_attachments_and_model_failure_abstain(monkeypatch):
    assert classify("See attached", "Please check.", has_attachments=True).category == "review"
    from app.enquiries.classification import model
    def unavailable():
        raise OSError("missing model")
    monkeypatch.setattr(model, "load", unavailable)
    assert classify("RFQ", "Please quote steel pipes.").category == "review"


def test_repetition_does_not_inflate_features():
    assert features("", "pipes pipes pipes") == features("", "pipes pipes")


async def test_unrelated_and_ambiguous_mail_do_not_call_llm(client, ctx, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Rejected or uncertain mail must not call the LLM reader")
    monkeypatch.setattr(reader, "read", forbidden)
    for subject, body, expected in [
        ("Weekend reading: productivity tips", "Enjoy our newsletter. Unsubscribe below.", "other"),
        ("Hello", "Can you help us?", "review"),
    ]:
        out = await _mail(client, ctx["h"], subject=subject, body=body)
        assert out["outcome"] == expected and out["enquiry_id"] is None
        listed = await client.get(f"{E}/mail/other?show={expected}", headers=ctx["h"])
        assert out["email_id"] in [m["id"] for m in listed.json()]
    counts = (await client.get(f"{E}/counts", headers=ctx["h"])).json()
    assert counts["mail_review"] >= 1


async def test_accepted_without_llm_goes_to_review(client, ctx):
    out = await _mail(client, ctx["h"], subject="RFQ for 40 steel pipes",
                      body="Please quote 40 steel pipes, ASTM A106 Grade B, 6 metres each.")
    assert out["outcome"] == "review" and out["enquiry_id"] is None
    row = (await client.get(f"{E}/mail/{out['email_id']}", headers=ctx["h"])).json()
    assert "no AI key" in row["label_reason"]


async def test_accepted_email_extracts_subject_and_body(client, ctx, ai):
    fake = ai(reading(customer={"contact_name": "Manas", "company": None}, lines=[{
        "description": "Steel pipes", "qty": 40, "unit": "nos",
        "dimensions": "6-inch diameter; length 6 metres", "standard": "ASTM A106 Grade B",
    }]))
    out = await _mail(client, ctx["h"], subject="RFQ for 40 steel pipes",
                      body="ASTM A106 Grade B, 6-inch diameter, 6 metres each. Deliver Hyderabad.")
    assert out["outcome"] == "enquiry"
    enquiry = await _enquiry(client, ctx["h"], out["enquiry_id"])
    assert enquiry["customer"].get("company") is None
    assert enquiry["lines"][0]["qty"] == "40.000"
    prompt = fake.calls[0]
    assert "Subject: RFQ for 40 steel pipes" in prompt[1]["content"]
    assert "ASTM A106 Grade B" in prompt[1]["content"]
    assert "BOTH the subject and body" in prompt[0]["content"]


@pytest.mark.parametrize("answer", [reading(lines=[]), reading(label="spam", lines=[])])
async def test_missing_lines_and_extractor_disagreement_need_review(client, ctx, ai, answer):
    ai(answer)
    out = await _mail(client, ctx["h"])
    assert out["outcome"] == "review" and out["enquiry_id"] is None


async def test_person_can_dismiss_review(client, ctx):
    out = await _mail(client, ctx["h"], subject="Hello", body="Can you help us?")
    response = await client.post(f"{E}/mail/{out['email_id']}/label", headers=ctx["h"],
                                 json={"label": "spam"})
    assert response.status_code == 200
    assert response.json()["outcome"] == "other"


async def test_existing_thread_bypasses_new_email_classifier(client, ctx, ai, monkeypatch):
    await _seller(client, ctx)
    ai(reading(), reading(updates=[], lines=[]))
    first = await _mail(client, ctx["h"], body=RFQ)
    enquiry = await _enquiry(client, ctx["h"], first["enquiry_id"])
    original = next(m for m in enquiry["messages"] if m["direction"] == "out")
    await send.deliver_due(ctx["tid"])
    def forbidden(*args, **kwargs):
        pytest.fail("Existing conversations must be processed as replies")
    monkeypatch.setattr(intake, "classify", forbidden)
    reply = await _mail(client, ctx["h"], subject="Re: RFQ", body="Yes, please proceed.",
                        reply_to=original["id"])
    assert reply["outcome"] == "reply"
