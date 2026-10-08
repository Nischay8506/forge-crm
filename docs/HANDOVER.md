# Forge CRM: Handover

**Read this first if you are picking up the project.** It is kept current: every working day
ends with this file and [BUILD_LOG.md](BUILD_LOG.md) updated. If something here is out of date,
the build log's latest entry wins.

Last updated: 2026-10-08

---

## 1. Where things are

| What | Where |
|---|---|
| This repository | `C:\Users\nisch\OneDrive\Desktop\CognitBotz\Forge Project\forge-crm` (decision 1A: lives in this folder, no GitHub) |
| Requirements (what to build) | `Forge Project\Forge_CRM_Requirements.txt`: 69 features, 6 phases. Ops (later): `Forge_Ops_Requirements.txt` |
| Architecture | `Forge Project\Forge_CRM_Architecture_Blueprint.html` (7 sheets) |
| UI designs | `Forge Project\design.html` (design tokens + 8 screens) |
| Build guide (source of the requirements) | `Forge Project\Forge_CRM_Developer_Build_Guide.docx` |
| Old prototype (reference only, never edit) | `Forge Project\CRM\CRM`. Review of it: `CRM_Code_Review.md` in the Claude project files |
| Phase 1 plan: starting point and flow | `Phase1_Starting_Point_and_Flow.md` in the Claude project files (`plans/`) |

The old prototype's `backend/.env` holds live keys. Do not copy it; use new keys in `backend/.env`.

## 2. What we are building, in order

Build order (requirements Section 4): **Phase 1 = Module 12 Platform Foundations**, then Phase 2 =
M1 Enquiry → M3 CPQ → M4 Order, then M2+M5, M8, M6+M7+M9, M10+M11.

Phase 1 steps:

| Step | What | Status |
|---|---|---|
| 0 | Foundations: repo, migrations, tenancy (RLS), login + RBAC, event outbox, worker, LLM gateway, UI shell | **Done 2026-10-08** (see section 6 for what is still open) |
| 1 | F12.1 Golden Record & Data Steward (dedup/merge/enrich, hygiene score, adjudication screen) | **Done 2026-10-08** (meaning match waits for the Gemini key, D3; see section 6) |
| 2 | F12.2 Agent Runtime, Policy Engine, Autonomy **+ minimal audit chain** (decision 6A) | Next |
| 3 | F12.6 Integration Framework & Connector SDK, ERPNext adapter (decision 5A) | Not started |
| 4 | F12.4 Audit replay UI, redaction, retention | Not started |
| 5 | F12.3 Human Approval Inbox (Design File frame 05) | Not started |
| 6 | F12.5 Multilingual & Channel Fabric (email, WhatsApp, SMS, caps) | Not started |

Phase 1 is done when: all six F12 "Done When" checks pass, ERPNext syncs customers, items, orders
and invoices, and one test agent runs policy gate → decision card → audit chain end to end.

## 3. Decisions (do not re-open without asking Nischay)

Nischay's rule: **no decision is taken without asking him.** Ask with short lettered options and a
recommendation, and record the answer here.

| # | Decision | Date |
|---|---|---|
| 1A | Repo is a subfolder of the `Forge Project` folder; no GitHub | 2026-10-08 |
| 2B | Lean infra: PostgreSQL (Neon in the cloud), outbox table instead of Kafka, Celery + Redis instead of Temporal, policies stored as rules in the DB instead of OPA, app-level RBAC instead of Keycloak. Each sits behind an interface so it can be swapped later | 2026-10-08 |
| 3C | One LLM gateway with routing by tier; Groq + Gemini now, Claude added later as the frontier tier | 2026-10-08 |
| 4A | Frontend is Vite + React (not Next.js) | 2026-10-08 |
| 5A | Reference ERP for F12.6 is ERPNext | 2026-10-08 |
| 6A | Minimal audit chain is built together with the agent runtime (Step 2) | 2026-10-08 |
| 7A | Screens the Design File does not cover are built as plain screens using the design tokens | 2026-10-08 |
| 8A | From the old code: auth + UI shell/components now; parsing, extraction, matching, CPQ maths in Phase 2 | 2026-10-08 |
| — | Fresh repo on the same stack, old code kept as reference (option B of the code review) | 2026-10-08 |
| D1 A | Local database and Redis run in Docker Desktop (`infra/docker-compose.yml`) | 2026-10-08 |
| D2 A | Login asks for company code + email + password (one email can belong to two companies) | 2026-10-08 |
| S1 A | GSTIN/PAN/CIN are checked offline (format + GSTIN check digit); no government API calls yet | 2026-10-08 |
| S2 A | Meaning match uses Gemini embeddings through the LLM gateway, as a **review-only** signal (never merges by itself) | 2026-10-08 |
| S3 A | The steward **auto-merges only on a hard identifier** (same GSTIN/PAN/CIN, or same company email domain with a near-identical name) with no contradiction. Name/phone/pincode-only matches go to the review queue. Switch for option b: `STEWARD_NAME_ONLY_AUTO_MERGE` (off) | 2026-10-08 |
| S4 A | Matching quality is measured on a synthetic duplicate set (`backend/evals/duplicates.py`) | 2026-10-08 |

Still open (section 7): D3 new LLM keys, D4 daily update time.

## 4. How to run it

### Prerequisites (Windows)
- Python 3.12 and [uv](https://docs.astral.sh/uv/) (`pip install uv`)
- Node 22+
- Docker Desktop (decision D1): `docker compose -f infra/docker-compose.yml up -d` starts PostgreSQL 16
  with pgvector and Redis. The first start runs `infra/postgres-init.sql` (roles, the `forge` and
  `forge_test` databases, and the `vector` extension, which only a superuser can install).
  If your database was created before Step 1, add the extension once by hand:
  `docker compose -f infra/docker-compose.yml exec postgres psql -U postgres -d forge -c "CREATE EXTENSION IF NOT EXISTS vector"`
  (and the same with `-d forge_test`). On Neon the project owner can run `CREATE EXTENSION vector`.

### Backend
```bash
cd backend
copy .env.example .env          # then fill in passwords, JWT secret, LLM keys
uv sync
uv run alembic upgrade head      # creates tables, RLS policies and the forge_app role
uv run python -m app.cli create-tenant --name "Demo Manufacturing" --slug demo \
    --admin-email admin@demo.example.com --admin-name "Demo Admin" --admin-password "<choose>"
uv run uvicorn app.main:app --reload --port 8000     # API docs at http://localhost:8000/docs
uv run celery -A app.worker.celery_app worker --beat --loglevel=info --pool=solo   # --pool=solo on Windows
```
The worker must be running for the Data Steward to act: new and edited records reach it through the
outbox, and CSV imports run in the worker. Without it records save fine but no duplicates are found.

Meaning match (S2A) switches itself on when `GEMINI_API_KEY` is set in `backend/.env`; nothing else
to configure. `STEWARD_EMBEDDINGS=false` turns it off.

### Frontend
```bash
cd frontend
copy .env.example .env
npm install
npm run dev                      # http://localhost:5173, sign in with company code "demo"
```

### Tests
```bash
cd backend && uv run pytest      # needs Postgres with the forge_test database (infra/postgres-init.sql)
uv run ruff check .
uv run python -m evals.duplicates --seed 7 --companies 400   # matching quality report (S4A)
cd frontend && npm test && npm run build
```
Tests use a real PostgreSQL on purpose: row-level security cannot be tested on SQLite. Tests never call
a real model provider (the keys are blanked in `tests/conftest.py`). The eval script creates a new
tenant named `eval…` in the dev database each run; it needs no keys.

### How Claude works on this repo
Claude builds and runs the tests in its own cloud workspace, then copies the source (with the
`.git` history, without `node_modules`/`.venv`) into this folder. Each copy is one git commit, so
`git log` in this folder shows every step. Your machine needs `npm install` / `uv sync` after a copy.
The bundle used for the copy is left at `.sync/forge-crm-sync.tgz` (git-ignored, overwritten each time).
If you edit files here yourself, tell Claude before the next session so it starts from your version.

## 5. Code map and the rules the code follows

```
backend/
  app/
    main.py            FastAPI app; no background loops in the web process
    config.py          settings from env / backend/.env
    db.py              engine, sessions, set_tenant(), tenant_session(), system_session()
    rls.py             enable_tenant_rls() helper for migrations
    cli.py             create-tenant (admin bootstrap)
    core/              security (JWT, bcrypt), permissions catalogue, deps (auth + RBAC), errors
    models/            SQLAlchemy models (tenancy, outbox, llm, golden = accounts/contacts/steward tables)
    api/v1/            routers: auth, users, accounts (+contacts), steward
    events/outbox.py   publish() + dispatch_batch() (transactional outbox)
    worker/            Celery app + tasks (outbox delivery every 2 s, CSV import)
    llm/               gateway.py (complete() and embed(); only way to call a model),
                       routing.py (tiers -> models), providers.py
    steward/           F12.1 Data Steward:
      identifiers.py     GSTIN/PAN/CIN checks (offline, S1A), phone/email/pincode/domain normalising
      normalize.py       company-name cleaning (M/s, Pvt Ltd, abbreviations), phonetic keys
      matching.py        compare two records -> band (auto_merge / review / distinct) + reasons
      candidates.py      blocking (which records to compare) + the review-queue table
      embeddings.py      meaning match (S2A), optional, review only
      records.py         create/update with validation, provenance and outbox events
      merge.py           merge with survivorship, branch-site rule, undo; REFERENCES registry
      hygiene.py         hygiene score per record and tenant dashboard
      service.py         the steward loop for one record (find -> auto-merge or queue -> rescore)
      handlers.py        outbox subscriptions; importer.py CSV migration mode; enrichment.py
  alembic/versions/    migrations (0001 foundation, 0002 golden records, 0003 embeddings)
  evals/duplicates.py  synthetic duplicate set and quality report (S4A)
  tests/               pytest, real Postgres
frontend/
  src/components/ui    design components (Button, Card, Tag, KpiCard, AgentBadge, Nameplate, Field,
                       ScoreBadge)
  src/components/layout AppBar (permission-filtered nav), MainLayout (auth guard)
  src/stores/authStore login / refresh / me
  src/pages            Home, admin/Users
  src/features/accounts Accounts list, account detail, create/edit form with live duplicate check,
                       Contacts
  src/features/steward Data Steward: review queue + side-by-side review, merge history + undo,
                       CSV import, hygiene dashboard
infra/                 local Postgres + Redis (Docker)
docs/                  this file, BUILD_LOG.md
```

**Rules (each one is enforced by code or a test; keep them):**
1. **Tenant isolation is in the database.** Every table with `tenant_id` must call
   `enable_tenant_rls(op, "<table>")` in its migration. `tests/test_tenancy.py` fails if one is missed.
   The app connects as `forge_app` (no superuser, no BYPASSRLS); migrations run as `forge_owner`.
2. **Every endpoint declares its permission** with `Depends(require_permission("<code>"))`. New codes
   go in `app/core/permissions.py`. Roles hold code lists; `"*"` = all. Superusers do not exist.
3. **Events go through the outbox** (`publish()` in the same transaction as the change). Handlers must
   be idempotent. Failed events retry with backoff and end as `dead` (visible, never silent).
4. **All model calls go through `app/llm/gateway.complete()`** with a tier, never a model name.
   Every attempt is recorded in `llm_calls`; monthly per-tenant budget is enforced.
5. **Background work runs in the Celery worker**, never inside FastAPI.
6. **Money is `Numeric`, never float.** Schema changes only through Alembic migrations.
7. From Step 2 on: **no side-effecting agent tool call without the policy gate and an audit record.**
8. **Records are never deleted by a merge.** The merged record stays with status `merged`; every merge
   stores before/after snapshots so it can be undone.
9. **Any new table pointing at `accounts` or `contacts` must be registered** with
   `app.steward.merge.register_reference(...)` so merges move its rows (or listed in `OWN_ROWS` if the
   row belongs to the record itself). `tests/test_steward.py` fails if one is missed.
10. **Every account/contact write goes through `app/steward/records.py`** (validation, provenance,
    hygiene, outbox event). Do not set fields on the models directly from new code.
11. **Only hard identifiers auto-merge (S3A).** Names, phones, pincodes and embeddings only queue a pair
    for review. A change to the matching rules must be re-checked with `evals/duplicates.py`: wrong
    merges must stay at 0.

## 6. Current state (end of 2026-10-08)

Step 0 (foundations) is done; see the build log. Step 1 (F12.1) is done:
- Accounts as group → legal entity → sites (plant, ship-to, bill-to, office, warehouse, branch) and
  contacts, with the source and verified date of every key field (`field_provenance`)
- Offline GSTIN/PAN/CIN checks; PAN and state filled from the GSTIN; phone, email, pincode normalised
- Matching in three stages (identifiers → fuzzy + phonetic name → meaning match), each pair with a
  band and plain-language reasons; review queue of uncertain pairs
- Merge with field-by-field survivorship (more trusted source, then more recently verified), contacts
  and sites moved to the kept record, full undo. Same PAN with another state's GSTIN becomes a branch
  site under the company instead of overwriting its GSTIN
- Hygiene score per record (completeness 40, validity 30, verified in the last year 20, no open
  duplicates 10) and a tenant dashboard
- CSV import from the old system (runs in the worker), enrichment with proposals a person accepts
- Screens: Accounts, account detail, create/edit with live duplicate check, Contacts, Data Steward
  (review queue, side-by-side review, merge history with undo, imports, hygiene)
- 41 backend tests + 6 frontend tests passing; every screen checked in a browser

Matching quality on the synthetic set (seed 7, 400 companies, 660 records):
- Wrong merges: **0**
- Duplicates auto-merged when both records carry a GSTIN/PAN: **98.8%**
- All duplicates auto-merged: 50% (the rest go to review, as S3A intends: 80 of 80 duplicates with no
  identifiers are in the review queue)
- Look-alike companies with different PANs: all kept apart. 154 pairs open for review in total
- With S3 option b (name-only auto-merge) the same set gives 0 wrong merges, 96% of duplicates merged
  and 34 open reviews. Nischay chose A; the switch exists if that changes.

Known gaps (none block Step 2):
- **Auto-merges run without the policy gate** until Step 2 builds it. They are controlled by
  `STEWARD_AUTO_MERGE` (on), recorded and undoable. Step 2 must route `service.process_record`'s merge
  through the gate.
- Meaning match is built and tested with a stand-in model, but is **off until a Gemini key is set (D3)**.
  The vector column has no fixed size, so there is no ANN index yet; fine for thousands of accounts.
  The duplicate check on the create form uses the rules only, not meaning match.
- No registry lookups (MCA, GST portal): S1A is offline checks only.
- No contact detail/edit screen (the API supports it); contacts are added from Contacts or an account.
- Model prices (`PRICES_PER_MTOK`) are empty and model names need confirming against the current
  Groq/Gemini catalogues; observability beyond `llm_calls` is not started.

## 7. Next step and open questions

**Next: Step 2, F12.2 Agent Runtime, Policy Engine, Autonomy + minimal audit chain (6A).** Agents run
as Celery tasks, every side-effecting tool call passes a policy gate (rules stored in the database,
decision 2B) and writes an audit record. The first agent to put behind it is the Data Steward's
auto-merge. Before building, ask Nischay the design questions for this step (policy rule format,
autonomy levels per agent, what the audit chain records), with options and a recommendation.

Open questions for Nischay:
- **D3** New Groq/Gemini keys for `backend/.env` (the old ones in `CRM\CRM\backend\.env` should be
  rotated, never copied). Meaning match (S2A) starts working as soon as the Gemini key is set.
- **D4** Daily handover update time: 18:50 IST set by default; change?
