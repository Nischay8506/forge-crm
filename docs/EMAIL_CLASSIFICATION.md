# Email classification and extraction

New messages use a local, English-demo-trained classifier before any LLM call.
Existing email conversations continue through the original reply pipeline.

## Routing

1. Capture the email once using the existing mailbox connector and encrypted store.
2. Classify subject + body + directly readable attachment text.
3. `enquiry`: invoke the existing LLM reader with both subject and body, plus documents.
4. `other`: store under **Enquiries > Other mail**, without LLM extraction.
5. `review`: store under **Other mail > Review mail**; no enquiry/customer/quote is created.

Review is also used for missing/corrupt model files, unfamiliar or predominantly
non-English text, unread attachments that may contain a request, missing AI credentials,
exhausted AI budget, invalid extraction after retries, missing product lines, and a
classifier/extractor disagreement. Review mail is counted in the Enquiries header.
People can inspect the original, convert to an enquiry, or label it as other mail.
Manual conversion retains the existing human-reviewed fallback for unavailable AI.

The model is deliberately not a general industrial-language understanding system.
Confidence values are similarity scores, not calibrated probabilities. The local
score is recorded in the reason, never in the database's probability field.
Supplier pitches have an explicit directional rule, with a mixed buying-intent exception.
No emails are deleted or moved in Gmail. No historical records are automatically reclassified.

## Files

| Path | Purpose |
| --- | --- |
| `backend/app/enquiries/classification/model.py` | Train TF-IDF centroids; classify or abstain using similarity/margin/vocabulary checks |
| `backend/app/enquiries/classification/__init__.py` | Small public classification interface |
| `backend/app/enquiries/classification/demo_train.jsonl` | 122 synthetic English training messages, three labels |
| `backend/app/enquiries/classification/demo_validation.jsonl` | 36 development examples, used for model selection |
| `backend/app/enquiries/classification/demo_test.jsonl` | 60 separate final synthetic examples; excluded from training |
| `backend/app/enquiries/classification/model.json` | Portable learned model; JSON instead of executable pickle |
| `backend/app/enquiries/classification/*evaluation.json` | Reproducible reports, including the unsuccessful initial development run |
| `backend/scripts/train_email_classifier.py` | Training, split-overlap check, confusion matrix and per-class metrics |
| `backend/tests/test_email_classification.py` | Classifier and pipeline regression tests |
| `backend/alembic/versions/0019_email_classification.py` | Extend existing check constraints for local classification and review outcomes |
| `backend/app/enquiries/intake.py` | New-message gate; extraction fallback goes to review; preserves reply handling |
| `backend/app/enquiries/reader.py` | Explicit subject/body extraction and unknown-company instructions |
| `backend/app/api/v1/enquiries.py` | Review-mail filtering and counts |
| `frontend/src/features/enquiries/OtherMailPage.tsx` | Review filter and local-classifier attribution |
| `frontend/src/features/enquiries/EnquiriesLayout.tsx` | Review count and link |
| `frontend/src/features/enquiries/types.ts` | API type updates |

No new dependencies or tables are needed. TF-IDF/centroid training uses Python's
standard library; migration 0019 extends the existing email outcome/source constraints.

## Reproduce

From `backend`:

```text
uv run python scripts/train_email_classifier.py --split validation
uv run python scripts/train_email_classifier.py
uv run alembic upgrade head
uv run pytest tests/test_email_classification.py
```

The script exits nonzero unless three-way routing accuracy is **strictly above 95%**.
It reports review as a real category, not as a successful abstention for every label.
It does not silently count uncertain enquiries as correct predictions.

The final synthetic test scored 60/60 (20 of each class). Validation scored 35/36.
These samples were authored for this demonstration and share a business domain and
vocabulary. They are not independent customer data, and their small size does not
establish >95% production accuracy. Collect anonymised, manually labelled real mail
(including reply chains, multilingual text, attachment-only RFQs, spam and supplier
offers), keep customer threads grouped by split, and evaluate on a locked holdout
before deploying autonomous filtering. Inspect missed enquiries as well as precision.
Do not tune on a test set and continue calling it untouched; create a new holdout.

## Run locally

Restart the API and Celery worker after changing code or the model (it is cached in
each process). Run Celery beat for scheduled polling. The classification stage needs
no API key. Extraction uses the existing LLM gateway: configure an appropriate
`GROQ_API_KEY` or `GEMINI_API_KEY` for the currently configured extraction routes.
Set keys locally, not in committed training data. Vision extraction also needs a
configured vision-capable route. The existing policy/approval/message gates remain
responsible for customer-facing actions.

Extraction routes (verified 2026-10-19): `gemini-3.8-flash`, then
`gemini-3.5-flash-lite`, then Groq's `llama-3.3-70b-versatile` if configured.
Vision uses `gemini-3.8-flash`. The former `gemini-2.5-flash` returned 404 with an
explicit "no longer available to new users" message. Both replacement text routes
returned 200 and extracted synthetic pipe/quantity/company fields correctly; the
primary also briefly returned a capacity-related 503, motivating the lighter fallback.
Real customer-document vision extraction has not been verified.

Update 2026-10-22: Groq removed `llama-3.3-70b-versatile` from free and developer
accounts, so the extraction route's last step is now Groq's `openai/gpt-oss-120b`, and
vision gained `gemini-3.5-flash-lite` as a fallback. The full list of routes is in
`backend/app/llm/routing.py` and HANDOVER section 4.

Expected fresh-mail smoke tests:

- Newsletter: **Other mail**, no product extraction call.
- "Can you help us?": **Review mail**, no product extraction call.
- Pipe RFQ with working LLM: structured item line using subject/body, company null
  unless explicitly supplied.
- Same RFQ without working LLM: **Review mail**, with extraction failure reason.
- Reply to an existing enquiry: attached to its conversation, not independently rejected.

"Check now" continues to report only emails fetched by that particular click; this
change does not alter mailbox polling counts.

## Verification performed

- Classifier + email workflow: 48 backend tests passed.
- Enquiry qualification/scoring + existing rules evaluation: 21 backend tests passed.
- Frontend: 42 tests passed; TypeScript and production build passed.
- Ruff and whitespace checks passed; Alembic has one head (`0019`), applied locally.
- Extraction contract tests use a fake provider. No live extraction or vision key
  was configured during verification; real-provider extraction is not yet verified.
