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
| 1 | F12.1 Golden Record & Data Steward (dedup/merge/enrich, hygiene score, adjudication screen) | Next |
| 2 | F12.2 Agent Runtime, Policy Engine, Autonomy **+ minimal audit chain** (decision 6A) | Not started |
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

Defaults chosen during the build, **waiting for Nischay's confirmation** (see section 7):
login asks for a company code + email; local dev database choice; daily update time.

## 4. How to run it

### Prerequisites (Windows)
- Python 3.12 and [uv](https://docs.astral.sh/uv/) (`pip install uv`)
- Node 22+
- PostgreSQL 16 and Redis: either Docker Desktop (`docker compose -f infra/docker-compose.yml up -d`)
  or a Neon database + a Redis service. See open question D1.

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
cd frontend && npm test && npm run build
```
Tests use a real PostgreSQL on purpose: row-level security cannot be tested on SQLite.

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
    models/            SQLAlchemy models (tenancy, outbox, llm)
    api/v1/            routers: auth, users
    events/outbox.py   publish() + dispatch_batch() (transactional outbox)
    worker/            Celery app + tasks (outbox delivery every 2 s)
    llm/               gateway.py (only way to call a model), routing.py (tiers -> models), providers.py
  alembic/versions/    migrations (0001_foundation)
  tests/               pytest, real Postgres
frontend/
  src/components/ui    design components carried over (Button, Card, Tag, KpiCard, AgentBadge, Nameplate)
  src/components/layout AppBar (permission-filtered nav), MainLayout (auth guard)
  src/stores/authStore login / refresh / me
  src/pages            Home, admin/Users
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

## 6. Current state (end of 2026-10-08)

Done in Step 0:
- Repo, Alembic migration `0001`, tenants/users/roles, RLS on all tenant tables, `forge_app` role
- Login with company code + email, JWT access/refresh, `/auth/me`, permission checks, `/users`
- Transactional outbox + Celery beat delivery with retry/backoff/dead status
- LLM gateway: tiers, fallback, per-call recording, monthly budget (OpenAI-compatible client for Groq,
  Gemini, Anthropic)
- Frontend: login, permission-filtered nav, Home, Users page; design tokens from the old code
- 15 backend tests + 2 frontend tests passing; browser check of login → Users page

Not done yet in Step 0 (carried to the next session):
- pgvector extension (needed in Step 1 for embedding matching)
- Model prices in `app/llm/routing.py` (`PRICES_PER_MTOK`) so budgets count cost; model names to
  confirm against current Groq/Gemini catalogues
- Observability (structured logs, tracing) beyond `llm_calls`

## 7. Next step and open questions

**Next:** answers to the open questions below, then Step 1 (F12.1 Golden Record & Data Steward):
Account/Contact golden records with plants and ship-tos, GSTIN/CIN validation, matching
(exact keys → fuzzy/phonetic → embeddings), confidence bands, merge/unmerge with survivorship,
hygiene score, adjudication screen.

Open questions for Nischay:
- **D1** Database and Redis for running on your machine: (a) Docker Desktop with
  `infra/docker-compose.yml` (rec), (b) a new Neon project + a hosted Redis
- **D2** Login asks for a company code + email + password (one email can belong to two companies).
  Keep (rec), or email + password only?
- **D3** New Groq/Gemini keys for `backend/.env` (the old ones should be rotated)
- **D4** Daily handover update time: 18:50 IST set by default; change?
