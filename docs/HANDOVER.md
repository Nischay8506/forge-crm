# Forge CRM: Handover

**Read this first if you are picking up the project.** It is kept current: every working day
ends with this file and [BUILD_LOG.md](BUILD_LOG.md) updated. If something here is out of date,
the build log's latest entry wins.

Last updated: 2026-10-09

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
| 1 | F12.1 Golden Record & Data Steward (dedup/merge/enrich, hygiene score, adjudication screen) | **Done 2026-10-08**, with one accepted gap: ≥95% auto-resolve not met on the synthetic set (54% with S3A); re-measure on real legacy data before Phase 2 (decision S5 A) |
| 2 | F12.2 Agent Runtime, Policy Engine, Autonomy **+ minimal audit chain** (decision 6A) | **Done 2026-10-08**: both F12.2 "Done When" checks pass (section 6). U1-U3 confirmed as built; U4 still waits for Nischay (section 7) |
| 3 | F12.6 Integration Framework & Connector SDK, ERPNext adapter (decision 5A) | **In progress**: groundwork in (shared contracts, ERPNext adapter, contract tests passing on a real ERPNext v16). Tables, sync engine and screens wait for Nischay's answers E1-E9, V1-V3 (section 7) |
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
| S5 A | The unmet F12.1 "≥95% auto-resolved" check is accepted for now: keep S3A and measure again on Nischay's real customer export before Phase 2 | 2026-10-08 |
| T1 A | Policy rules are structured rows (agent, action, conditions from a fixed list of facts, outcome), shown and edited as plain sentences. A new kind of condition needs a small code change (`FACTS` in `app/agents/registry.py`) | 2026-10-08 |
| T2 A | Decision object + a simple "Waiting for you" list now; approving lets the agent continue. Channels, SLA and delegation come with F12.3 (Step 5) | 2026-10-08 |
| T3 A | Autonomy level per agent per tenant, with overrides for an account tier or one named account | 2026-10-08 |
| T4 A | Agent definitions (charter, tools, model tier) live in code; what a tenant changes (level, stops, rules) lives in the database | 2026-10-08 |
| T5 A | Our own small runtime on Celery; no LangGraph | 2026-10-08 |
| T6 A | One audit hash chain per tenant | 2026-10-08 |
| T7 A | First behind the gate: the Data Steward's merges, plus a test-only Sandbox Agent with amounts for the over-limit test | 2026-10-08 |
| T8 A | Human takeover = a person takes over a whole record; agents do nothing on it until it is handed back; both steps audited | 2026-10-08 |
| U1 A | Account tiers are Strategic, Key, Standard (blank counts as Standard) | 2026-10-08 |
| U2 A | A site or group company with no tier of its own takes its parent's tier (a plant of a strategic customer is treated as strategic) | 2026-10-08 |
| U3 A | When a rule routes a decision to a role (e.g. Finance), people with that role and full-access admins can decide it | 2026-10-08 |

Still open (section 7): D3 new LLM keys, D4 daily update time, U4 (stops and overrides on a merge), Step 3 questions E1-E9 and V1-V3.

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

A new company starts with one role, Admin (full access). To try approvals by role, create a role on
the Users screen (e.g. Finance with "See what agents are waiting on, and approve or reject it"), add a
user with it, and pick that role under "Who decides" in a policy rule. The Sandbox Agent's "Try it"
box (Agents → Sandbox Agent) shows what your rules do with a given amount.

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
uv run python -m evals.policy_gate --seed 11 --calls 400      # F12.2 chaos test (over-limit calls)
cd frontend && npm test && npm run build
```
Tests use a real PostgreSQL on purpose: row-level security cannot be tested on SQLite. Tests never call
a real model provider (the keys are blanked in `tests/conftest.py`). The eval script creates a new
tenant named `eval…` (`gate…` for the policy gate) in the dev database each run; it needs no keys.

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
    api/v1/            routers: auth, users (+roles), accounts (+contacts), steward,
                       agents (+policies, kill-switches, sandbox), decisions, audit (+takeovers)
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
      agent.py           the Data Steward as an agent: its merge tool and charter (T7)
    agents/            F12.2 runtime (start here: registry.py):
      registry.py        AgentDef / ToolDef, the FACTS rules can test, levels L0-L3,
                         DEFINITION_MODULES (where agents are registered)
      gate.py            the policy gate: charter -> kill-switch -> takeover -> rules -> autonomy
      runtime.py         call_tool() (the only way an agent acts), decide(), supersede()
      policy.py          rule matching, validation, rules as sentences
      controls.py        levels and overrides, kill-switches, takeovers, account tiers
      sandbox.py         test-only Sandbox Agent (pretend payments with amounts)
    audit/chain.py     append() and verify() for the per-tenant hash chain (T6)
    integrations/      F12.6 (Step 3, groundwork only so far; start here: adapter.py):
      contracts.py       the shared contracts (Customer, Item, Price, Order, Invoice, Receipt, Stock)
      adapter.py         the adapter interface, ConnectorError and its causes (for alerts)
      erpnext/           client.py (Frappe REST, errors -> causes), adapter.py (ERPNext -> contracts)
      sync.py            run_flow() (one record type, from a high-water mark, resumable, retries)
                         and reconcile() (nightly comparison); records go to a Sink (storage: E2)
      testing/           contract.py (the certification suite + CLI), fake_erpnext.py, seed.py
  alembic/versions/    migrations (0001 foundation, 0002 golden records, 0003 embeddings,
                       0004 agent runtime + audit + account tier)
  evals/duplicates.py  synthetic duplicate set and quality report (S4A)
  evals/policy_gate.py chaos test: seeded over-limit calls must all be stopped and escalated
  tests/               pytest, real Postgres
frontend/
  src/components/ui    design components (Button, Card, Tag, KpiCard, AgentBadge, Nameplate, Field,
                       ScoreBadge)
  src/components/layout AppBar (permission-filtered nav), MainLayout (auth guard)
  src/stores/authStore login / refresh / me
  src/pages            Home, admin/Users (users, roles, role assignment)
  src/features/accounts Accounts list, account detail, create/edit form with live duplicate check,
                       Contacts
  src/features/steward Data Steward: review queue + side-by-side review, merge history + undo,
                       CSV import, hygiene dashboard
  src/features/decisions Decisions ("Waiting for you", notices, done) and the nav count
  src/features/agents  Agents (what each can do, level, overrides, stop, sandbox "try it"),
                       policy rules with a rule builder, stops, audit log with chain check,
                       TakeoverPanel (used on the account page)
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
7. **No side-effecting agent action outside `app/agents/runtime.call_tool()`.** It runs the gate and
   writes the audit records. A tool computes its own facts and touched records from its arguments
   (never trust what an agent says about amounts or tiers). New agents and tools register in a module
   listed in `DEFINITION_MODULES` (`app/agents/registry.py`). Agents act as `agent:<key>`, never as a
   person.
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
12. **The audit log is append-only.** Write with `app.audit.chain.append()` inside the same transaction
    as the change. The app role cannot UPDATE/DELETE `audit_records` and a trigger refuses it for
    everyone; `GET /audit/verify` (and the "Check the chain" button) recomputes every hash.
13. **A change to the gate must be re-checked with `evals/policy_gate.py`**: every over-limit call must
    still be stopped with the right escalation (100%).
14. **Adapters only translate.** An adapter maps its system's records to `app/integrations/contracts.py`
    and its failures to a cause; retries, dead-letter, alerts and the policy gate on writes belong to
    the sync engine. An adapter counts as certified only when `app/integrations/testing/contract.py`
    passes against a real instance of its system.

## 6. Current state (end of 2026-10-08)

Steps 0, 1 and 2 are done; Step 3 has its groundwork in (below). Tests: 100 backend (plus 2 that run
only when a live ERPNext is configured), 10 frontend, all passing; every new screen was checked in a
browser.

### Step 3 groundwork (F12.6), built while E1-E9 wait for answers
Only the parts that do not depend on the open questions: no tables, screens, schedule or alerts yet.
- **Sync loop** (`app/integrations/sync.py`, 2026-10-09). `run_flow()` reads one record type from
  the last high-water mark, page by page, into a Sink (the storage interface; real tables come with
  E2). It re-reads a 2-minute overlap so records written while a run was reading are not missed,
  saves its place after every page so a stopped or failed run resumes from the next page, retries
  failures that may pass (unreachable, timeout, rate limited, server error) up to 4 tries with waits
  of 2, 4, 8 s (capped at 60 s), and stops at once on a rejected key or missing permission. Records
  that cannot be read go to the sink's problem list (the dead-letter queue) and the run carries on.
  `reconcile()` compares every id and modified time in the ERP with what Forge holds and fetches the
  missing and out-of-date ones (up to 500; more means a full re-read is due); records the ERP no
  longer has are only reported, since what to do with them depends on E2. Both run end to end on the
  real ERPNext v16 site for all seven record types.
- **Shared contracts** for Customer, Item, Price, Order, Invoice, Receipt and Stock
  (`app/integrations/contracts.py`): ERP ids, UTC times, Decimal money, the ERP's own codes, and one
  canonical status list per contract with the ERP's own word kept beside it.
- **Adapter interface** (`app/integrations/adapter.py`): `check()`, `changes()` (keyset paging on
  modified time + id, so a bulk import sharing one time is neither skipped nor repeated), `fetch()`,
  `stamps()` (for nightly reconciliation). Every failure carries a cause (auth rejected, permission
  denied, unreachable, timeout, rate limited, server error, bad response, rejected, not configured)
  so an alert can name it. A record that cannot be translated is reported with its reason (for
  example "qty is not a number: 'ten'") and the rest of the page still arrives.
- **ERPNext adapter** reads all seven contracts from ERPNext's REST API with an API key. Checked
  against a real ERPNext v16.50.0 site with demo data (official Docker image `frappe/erpnext`),
  including 12 customers forced to share one modified time. The ERPNext user needs the roles Sales
  User, Accounts User, Stock User and Sales Master Manager (prices); `check(full=True)` names any
  record type it cannot read. ERPNext only lets System Managers read the site time zone, so the
  connection carries it (Asia/Kolkata unless set). The local ERPNext used for this is described by
  `dev/erpnext/compose.yml` in the Claude project files; it moves into this repo once E1 is answered.
- **Contract-test suite** (`app/integrations/testing/contract.py`), generic for any adapter: connects,
  wrong key gives "auth_rejected", unreachable gives "unreachable", paging returns each record once and
  matches reconciliation, `since` is exact, `fetch` matches the listing. Passes on the fake ERPNext in
  CI and on the real v16 site. Run it against any site with
  `uv run python -m app.integrations.testing.contract erpnext --url ... --key ... --secret ...`.

### Step 2 (F12.2 + minimal F12.4)
- **Agents** are defined in code (T4): charter text, tools, default and highest level, triggers, model
  tier, service identity. Two exist: the **Data Steward** (merges, default L2) and the test-only
  **Sandbox Agent** (pretend payments with an amount, default L2).
- **Policy gate** before every side-effecting action, in this order: in the agent's charter → no
  kill-switch on (all agents / this agent / a touched account) → no touched record taken over by a
  person → policy rules (Block beats Ask a person beats Let it act) → autonomy level (L0 asks a person,
  L1 acts and leaves a notice, L2/L3 act). Kill-switches and takeovers are hard stops nobody can
  approve; a rule block also leaves a notice for a person (the escalation).
- **Policy rules** (T1) test facts the tool computes itself: amount, discount %, account tier, match
  score, linked records moved, record type. Shown everywhere as sentences, e.g. "When Sandbox Agent
  wants to record a test payment and amount (INR) is more than ₹1,00,000, ask a person first (Finance
  decides)." A rule can name the role that decides.
- **Autonomy** (T3): a level per agent, plus overrides for a tier (strategic / key / standard) or one
  named account. A named account beats its tier; when an action touches several accounts the most
  careful level wins. Accounts have a new **tier** field (account form); a site or company without
  one takes its parent's.
- **Decisions** (T2): a card per action waiting for a person, with the numbers, why it is asking, the
  agent's recommendation and every gate check. Approving runs the action as proposed, through the gate
  again (a stop or block rule added since still wins). Merging or rejecting the same pair from the
  review queue closes the card (superseded). Notices cover L1 actions and rule blocks.
- **Takeover** (T8): "Take over from agents" on the account page; agents do nothing on that account
  until it is handed back (by the same person or an agents admin).
- **Audit chain** (T6): every proposal, gate result, approval, rejection, merge, undo, rule change,
  level change, stop, takeover and role change is a hash-chained record per tenant. The database
  refuses changes; "Check the chain" on the Audit log recomputes it and names the first altered record.
- **Data Steward behind the gate** (T7): its automatic merges now go through `call_tool()`; the old
  `STEWARD_AUTO_MERGE` setting is gone (stop it on the Agents screen instead). The duplicate eval gives
  exactly the Step 1 numbers through the gate.
- **Users screen**: add users, create roles from the permission list, change a user's roles (a rule's
  "who decides" is a role). New permissions: `agents.read`, `agents.manage`, `decisions.decide`,
  `audit.read`, `records.takeover`.
- Screens: Decisions (nav item with a count), Agents (list, agent page that answers "what can it do",
  policy rules with a rule builder, stops, audit log), takeover banner and tier on accounts, Users.

**F12.2 "Done When" checks:**
- "Admin screen answers 'what can agent X do' without reading a prompt": **met**. The agent page shows
  its actions, what rules can check, triggers, model, service identity, level and overrides, the rules
  that apply (as sentences), stops and recent activity.
- "Chaos test: 100% of seeded threshold-violating calls blocked with correct escalations": **met**.
  `evals/policy_gate.py --seed 11 --calls 400`: 312 of 312 over-limit calls handled correctly (asks
  routed to Finance, blocks logged with a notice, stops logged), 400 of 400 calls overall, 10 of 10
  approvals then executed exactly once, audit chain verified (1,325 records). A test runs it on every
  `pytest` and a second test proves it fails when the gate ignores block rules.

### Step 1 (F12.1)
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

Matching quality on the synthetic set (seed 7, 400 companies, 660 records):
- Wrong merges: **0**
- Duplicates auto-merged when both records carry a GSTIN/PAN: **98.8%**
- All duplicates auto-merged: 50% (the rest go to review, as S3A intends: 80 of 80 duplicates with no
  identifiers are in the review queue)
- Look-alike companies with different PANs: all kept apart. 154 pairs open for review in total
- With S3 option b (name-only auto-merge) the same set gives 0 wrong merges, 96% of duplicates merged
  and 34 open reviews. Nischay chose A; the switch exists if that changes.

**F12.1 "Done When" check:** "≥95% auto-resolved correctly, zero wrong merges". Zero wrong merges is
met. ≥95% is **not** met: 54.2% of all planted cases are auto-resolved with S3A (89.6% with S3b),
because S3A sends every duplicate without a GSTIN/PAN/CIN to a person. The result depends on the mix
in the synthetic set, which Claude chose: 40% of its duplicates carry no identifier. Real legacy data
may have a different mix. Decision S5 A: accepted for now; measure again on the real customer export
before Phase 2 (load it with the Imports screen, then compare auto-merged vs review counts).

Known gaps (none block Step 3):
- F12.2 "Supervision" is only partly built: per-tenant LLM budgets and model routing exist (Step 0), but
  golden-set evals per agent per release and drift monitors wait until there are model-driven agents
  (Phase 2). Per-step tracing is the audit chain plus `agent_runs`; there is no OpenTelemetry yet.
- Approving a decision runs the action inside the web request. Fine for merges and test payments;
  slow actions (ERP pushes, sending messages) must be handed to the worker when they arrive.
- A stop takes effect at the next action; it does not interrupt an action already running.
- A pair whose merge was blocked or stopped stays in the review queue; it is tried again only when a
  record changes or someone presses "Check all records again".
- Audit replay screen, PII redaction and retention rules are Step 4 (F12.4). The full approval inbox
  (WhatsApp, mobile, SLA, delegation, counter-offers) is Step 5 (F12.3).
- The frontend bundle is 540 kB (Vite warns above 500 kB); split it by route when it grows further.
- Meaning match is built and tested with a stand-in model, but is **off until a Gemini key is set (D3)**.
  The vector column has no fixed size, so there is no ANN index yet; fine for thousands of accounts.
  The duplicate check on the create form uses the rules only, not meaning match.
- No registry lookups (MCA, GST portal): S1A is offline checks only.
- No contact detail/edit screen (the API supports it); contacts are added from Contacts or an account.
- Model prices (`PRICES_PER_MTOK`) are empty and model names need confirming against the current
  Groq/Gemini catalogues; observability beyond `llm_calls` is not started.

## 7. Next step and open questions

**Next: Step 3, F12.6 Integration Framework & Connector SDK with the ERPNext adapter (decision 5A).**
The design questions E1-E9 and small defaults V1-V3 were sent to Nischay on 2026-10-08
(`plans/Step3_Integration_Questions.md` in the Claude project files). Parts that don't depend on
the answers (shared contracts, adapter interface, test harness) can start; the rest waits for them.

Open questions for Nischay:
- **D3** New Groq/Gemini keys for `backend/.env` (the old ones in `CRM\CRM\backend\.env` should be
  rotated, never copied). Meaning match (S2A) starts working as soon as the Gemini key is set.
- **D4** Daily handover update time: 18:50 IST set by default; change?
- **U4** A default Claude chose while building Step 2 (U1-U3 were answered A and are in section 3).
  It is built as option A and is a small change to switch:
  - **U4 Merging an account that has a stop or an autonomy override.** A: the stop or override moves to
    the kept account (undo moves it back). B: it stays on the merged record and stops applying.
