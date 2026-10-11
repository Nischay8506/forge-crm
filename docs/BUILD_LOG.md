# Build Log

One entry per working day, newest first. Each entry: what was done, decisions, problems, what is next.
The current overall state is in [HANDOVER.md](HANDOVER.md).

---

## 2026-10-11 (Day 4): nothing built; paused by Nischay

Nothing was built or changed in the code today. On 2026-10-10 at 15:35Z Nischay chose to start Phase 2
with the M1 Enquiry questions, then at 15:36Z asked to pause ("pause for now we will continue later");
the questions have not been written. Waiting on Nischay: the word to continue (then the M1 questions come
first, nothing is built before he answers them), his Step 5 usability runs, his Step 3 Docker check, the
D3 keys for the Step 6 language reviews, and `uv run alembic upgrade head` (migration 0009) on his PC.

---

## 2026-10-10 (Day 3, evening): Step 6 built for email; F12.5 check 2 met, check 1 waits for D3 and reviews

**Decisions (Nischay):** L1 email only, L2 A (SMTP), L3 and L4 WhatsApp and SMS on the later list, L5 A,
L6 major languages (card: "Major scripts", English plus ten Indian scripts, no Indian languages typed in
English letters), L7-L11 A; Done-When card: "Email now, keep open" (14:30-14:34Z).

**Done** (commit 5e08996 backend, and today's last commit for the screens)
- Migration 0009: message columns (addresses, language, purpose, provider, send-after, tries, checks),
  message events (append-only), consents (append-only), channel settings, email account (encrypted
  password), tone guides, glossary, language reviews; all with row-level security.
- `app/channels/`: one sending path (draft, queue with six checks, deliver in three phases so a
  message goes at most once), simulator (the Test inbox) and SMTP adapters, consent with STOP words and
  an unsubscribe page, limits, quiet hours, rest days and holidays, the reply window, send anyway,
  retries (1, 5, 15, 60 min) and "lost" messages that only a person resends; approval cards by email.
- `app/language/`: language by script (11 languages), tone guides, glossary and text checks, the Writing
  Assistant (writes natively in EN/HI/TE, one rewrite), native-speaker review sampling and report,
  dictation through the LLM gateway with glossary hints.
- Screens: Messages (list, one message, New message, Test inbox, Language review, Languages, Settings),
  Write and Consent on the account page's contacts, the Sandbox Agent's "Try an email".
- Tests: 49 new backend (210 in all, plus 3 live-ERPNext ones) and 4 new frontend (25). Browser check
  as an admin, desktop and phone: send, reply as the customer in Telugu (recognised), reply in her
  language with the wrong-language warning, Hindi promotional email, unsubscribe, consent shown as
  stopped, the next promotional email stopped by consent, bounce, language review approved (report
  1 of 10), a promotional email held by the daily limit and sent anyway with a reason, settings
  validation and save, consent recorded by a person, glossary term, no page errors, no sideways scroll.

**Problems found and fixed**
- Claude's own dev database had an early copy of migration 0009 without `override_by`; re-ran
  0009 down and up. Nischay's PC gets 0009 fresh, so nothing to do there.
- The Sandbox Agent's fixed test text was being sampled for language review, which would have
  counted towards the 10 per language without testing the AI's writing. Only text the Writing
  Assistant wrote is sampled now.
- Unsubscribe links point at `PUBLIC_BASE_URL`, which is localhost on a PC; Settings now warns that
  real promotional email needs a public https address first.
- Tests: quiet hours vs the reply window, the weekly limit taking over from the daily one, Hindi
  words shared with Marathi, and the glossary check, all corrected (the code was right or now is).

**Not done / next**
- F12.5 check 1 (EN/HI/TE correctly toned) needs the Gemini key (D3) and native speakers' reviews,
  10 per language; the Hindi and Telugu fixed texts need a native check too. WhatsApp half: later list.
- Nischay: `uv run alembic upgrade head` after the copy, then try Messages; the Step 5 usability runs;
  the Step 3 Docker check; D3, D4.
- Claude: record the Step 5 results when they come; the counter hint; agree the next build.

## 2026-10-10 (Day 3): Forge running on Nischay's PC, Step 5 usability run started; no code changes

**Decisions (Nischay):** Step 6 waits until he has finished the Step 5 test and answered L1-L11
(2026-10-09 19:25Z). Claude sent the Step 6 questions L1-L11 just before that.

**Done**
- Forge set up on Nischay's Windows PC for the first time: Docker Postgres and Redis, `uv sync`,
  migrations 0001-0008, `npm install`. Claude wrote `backend/.env` and `frontend/.env` there (local
  database passwords, keys generated on the PC itself; both git-ignored).
- A step-by-step guide for the test, as a Claude Doc ("Step 5 Approval Test Guide", linked in HANDOVER
  section 4), with a results table to fill in.
- First session started on test company `ut-priya`; the counter and the ERP figures worked as built.
- Docs: HANDOVER section 4 and README now give the Windows worker commands (below).

**Problems found**
- `celery worker --beat` fails on Windows ("-B option does not work on Windows"). The worker and the
  scheduler now run in two windows; HANDOVER section 4, README and the guide are fixed.
- Usability finding: on **Counter…**, "Approve with this change" stays grey while the amount equals
  what was asked, and nothing says why. To fix after the runs, so the screens don't change mid-test.
- An "ERPNext cannot be reached" alert appeared in the test company: the paused pretend ERP connection
  had been resumed from the Integrations page, so Forge tried its made-up address (erp.invalid).
  Expected behaviour; pausing it again clears the alert. Nothing outside the PC was called.

**Not done / next**
- Nischay: finish the usability sessions (2-3 people) and fill in the results table; answer L1-L11;
  his Step 3 Docker check; D3 and D4.
- Claude: record the results and mark F12.3 on the Build Map; fix the counter hint; then Step 6.

## 2026-10-09 (Day 2, night): Step 5 built, automated check passes, usability run pending

**Decisions (Nischay):** H1-H8 A, H8 C, H9 A with eight safeguards (17:57Z, handover section 3);
the stand-in's scope is set by the person handing over ("Delegator sets scope", 18:11Z).

**Done** (commits 78b249b, 7892ffa and today's last commit)
- Approver chains in policy rules, a different person per step, the asker never decides; counter
  through the gate again; hand-over and out-of-office with card types and a maximum; working
  calendar, deadlines, reminders, escalation that only notifies; card fingerprint checked on every
  decision; ERP figures with their read time and a stale warning; push notifications and a phone
  layout; Approval numbers and bottleneck alerts (migration 0008, `app/approvals/`).
- Screens: the new card, Away and hand-overs, Approval numbers, Deadlines, and the chain and deadline
  in the rule editor. App icon and "add to home screen".
- `cli seed-usability` and `docs/USABILITY_TEST.md` for Nischay's 15-minute test.
- Browser check of all 11 prepared cards as two people (desk and phone): median decision 3 s.
- 161 backend + 21 frontend tests passing.

**Problems found and fixed**
- Deciding one card changed the history shown on the next card for the same account, so that card's
  fingerprint no longer matched and the next decision was refused. The inbox now reloads the other
  waiting cards for that account after each decision.
- A counter was counted twice in the numbers (the counter and its approval of the changed card), and
  the changed card inherited the "counter" recommendation, so it showed as an override. Now counted
  once, and the changed card has no recommended option of its own.

**Not done / next**
- Nischay: the usability run with real people (F12.3 Done-When stays pending until then); optional
  push keys (`new-push-keys`); his Step 3 Docker check; D3 and D4.
- Claude: Step 6, F12.5 Multilingual & Channel Fabric, when Nischay says go.

## 2026-10-09 (Day 2, late evening): Step 5 started

**Decisions (Nischay):** go ahead with Step 5 (17:27Z).

**Done**
- Step 5 questions H1-H9 sent (`plans/Step5_Approval_Inbox_Questions.md` in the project files): approver
  chains, counter, delegation and out-of-office, deadlines, channels, the card's history, the card as
  shown, the 60-second test, analytics. Answer pending (card: "Recommended" or his own letters).
- Built meanwhile: card timing (first on screen, time to decide) and "What happened next" on decided
  cards (migration 0007). Browser check: two cards decided in 3 s and 2 s, both recorded.
- 149 backend + 18 frontend tests passing.

**Not done / next**
- Nischay's answers to H1-H9, then the rest of the inbox.

## 2026-10-09 (Day 2, evening): Step 4 built, both F12.4 checks pass

**Decisions (Nischay):** R1-R8 all A; seven Step 4 safeguards (handover section 3); R3 B (outside
write-once copy of the seals) recorded for real deployment, before the first paying customer.
17:19Z: erasing a contact also blanks Forge's copy of ERP records and blocks it ("Erase and block", as built).
17:25Z: the retention minimums stay (8 years skeleton, 12 months anything else; "Keep both").

**Done** (commits 503a84e, d5e4ce0 and today's last commit)
- Audit records split into a hashed skeleton and encrypted evidence (EVIDENCE_KEYS, required);
  secrets stripped before storing; personal-looking values kept out of the skeleton.
- Hourly signed seals chained to each other (AUDIT_SEAL_KEYS, rotatable); the chain check catches
  changed, deleted or cut records, changed or missing evidence, and missing, forged or reordered seals.
- Replay shows what the agent was given and the exact model prompt and answer; agents have versions.
- Personal data hidden by default, revealed with a permission and a recorded reason; name parts
  ("Meera") hidden too, company names kept readable.
- Retention: India profile per category with its law, editable, unconfirmed until a CA or lawyer is
  recorded; legal holds; nightly clean-up at 03:00 IST that trims only up to a seal.
- Erasure through the Privacy agent at L0 with four eyes; keeps money and message evidence by law.
- Message provenance: Messages card, test drafts (never sent), "Trace this message" page.
- Evidence tab per agent (by month and version, links to the runs behind each number).
- Screens checked in a browser on a fresh company: trace opens in 0.2 s; erasure blocked for the asker,
  approved by a second person; chain intact after erasure and sealing.
- 148 backend + 16 frontend tests passing.

**Problems found and fixed**
- New tables got DELETE/UPDATE for the app role through default privileges; revoked in 0006.
- A first name in a takeover reason showed in the trace before reveal; name parts are now hidden.
- The sealing-stopped test was passing for the wrong reason; rewritten.

**Not done / next**
- Nischay: D3 keys; D4 time; his Step 3 check; legal validation of the periods.
- Step 5 (F12.3 approval inbox): questions first.
- Copied to Nischay's PC at 17:20 UTC (after his PC came back online).

## 2026-10-09 (Day 2, afternoon): Step 4 started, run replay built

**Decisions (Nischay):** X1 A (integration defaults kept); start Step 4 before his own Step 3 check.

**Done**
- Step 4 questions R1-R8 sent (`plans/Step4_Audit_Replay_Questions.md` in the project files). The main
  finding: Step 2's chain hashes personal data inside each record, so retention and erasure can't remove
  it without breaking the chain (R1); model prompts aren't kept (R2); a database owner could rewrite and
  re-hash (R3).
- Agents > Runs and the step-by-step replay of a run, with a per-step intact check, linked from decision
  cards, the audit log, agent pages and accounts. Two tests (a full ask-approve-act run, and a changed
  plus a removed record named in the replay); 129 backend + 13 frontend tests passing.

**Problems found and fixed**
- Runs started by outside AI tools and by "create in ERPNext" never recorded that they finished.

**Not done / next**
- Nischay's answers to R1-R8, then the evidence store, redaction, retention and message provenance.
- Nischay's own Step 3 check in Docker Desktop (his PC needs Forge set up first).
- Copy to Nischay's PC: his PC was offline at 12:40 and 13:22 UTC, so the device folder is still at
  a32ba4a; the next run copies everything since.

## 2026-10-09 (Day 2, continued): Step 3 built, both F12.6 checks pass

**Decisions (Nischay):** E1-E9, V1-V3 and U4 all A, with five safeguards (handover section 3);
W1 A (code lists: GST standards for units and tax, the rest copied from the first ERP);
X1 A (the integration defaults stay as built: agent levels, alert limits, 02:00 IST comparison,
disabled ERP customers skipped, create-in-ERPNext sends name, type and GSTIN only).

**Done**
- Tables (migration 0005), encrypted ERP logins with key rotation (`new-credential-key`,
  `rotate-credentials`), the minute schedule in the worker, alert and task cards in Waiting for you,
  dead letters with retry and skip, nightly comparison that marks gone records (commit b8042ff).
- Customer linking by verified IDs with review for anything less certain, ERP-owned fields with
  approval cards on conflicts, the gated "create in ERPNext" with duplicate checks and idempotency,
  code lists with suggestions and versions.
- Integrations API, MCP server at `/mcp/` with tokens, and the screens: connections, setup wizard,
  connection page, Customers and Code lists (mapping studio), Outside AI tools, ERP section on the
  account page, alerts and tasks on Decisions, audit labels (commits 76b8e22, dad9e0c).
- `infra/erpnext/` (compose files for ERPNext 16 and 15, and a step-by-step README for Docker Desktop).
- F12.6 Done-When, checked in a browser on ERPNext 16.50.0: the full read finished 51 s after the
  wizard saved; a regenerated ERPNext key raised the alert naming the cause in 36 s and 41 s.
- Tests: 127 backend + 11 frontend passing; the live tests pass on ERPNext 16.50.0 and 15.122.0.

**Problems found and fixed**
- The dead-letter upsert failed after a few runs on the same connection: Postgres stops matching a
  bound predicate to a partial unique index once a prepared statement switches to its generic plan.
  It now uses a literal predicate (the API tests caught it).
- After a fixed login, read failures from the outage kept the alert open until the next scheduled
  read; reads that failed only because of the login now re-run at the next minute check.
- A connection without a time zone used Asia/Kolkata even when ERPNext could tell its own; it now
  takes ERPNext's when readable.

**Not done / next**
- Nischay's own run of `infra/erpnext/README.md` in Docker Desktop (his PC needs Forge set up first).
- Step 4, F12.4 (audit replay, redaction, retention): Nischay chose to start it before his Step 3
  check. Questions R1-R8 sent (`plans/Step4_Audit_Replay_Questions.md`); building starts on his answers.

## 2026-10-09 (Day 2): Step 3 sync loop and reconciliation

**Decisions (Nischay):** do not use the GitHub repos another thread looked at; continue with the
original plan. E1-E9, V1-V3 and U4 are still open (reminded in the thread).

**Done**
- `app/integrations/sync.py`: `run_flow()` (high-water mark with a 2-minute overlap, resumable after
  every page, retries with growing waits for failures that may pass, immediate stop on a rejected key
  or missing permission, unreadable records to the dead-letter list) and `reconcile()` (nightly
  comparison of ids and modified times, repairs missing and out-of-date records, reports records the
  ERP no longer has). `MemorySink` stands in for the tables until E2 is answered.
- 11 new tests on the fake ERPNext, plus a live test: full sync and reconciliation of all seven record
  types pass on the real ERPNext v16 site.
- Yesterday's doc refresh reached Nischay's folder (his computer was offline at the evening update).
- Ran a second real site, ERPNext v15.122.0 with demo data: the contract suite and the full sync and
  reconciliation pass on it unchanged, so the reader works on both current versions (input for V1).

**Problems found and fixed**
- Docker Hub refused the ERPNext v15 image download at first (429 Too Many Requests); it went
  through on a later retry.
- After restarting ERPNext's backend containers, its web server answered 502 until it was restarted
  too (it keeps the old backend address). Worth a line in the setup steps.

**Not done / next**
- Tables, schedule, connection check every minute, alerts, setup wizard and mapping studio wait for
  E1-E9 and V1-V3.

## 2026-10-08 (Day 1, continued): Step 3 questions and groundwork

**Decisions (Nischay):** U1 A, U2 A, U3 A (built that way already). U4 still open.

**Done**
- Step 3 design questions E1-E9 and small defaults V1-V3 sent
  (`plans/Step3_Integration_Questions.md` in the Claude project files).
- Groundwork that does not depend on the answers: shared contracts, the adapter interface with
  failure causes, the ERPNext adapter (reads all seven contracts), a fake ERPNext, seed data and the
  generic contract-test suite with a command-line runner. 31 new tests.
- Ran a real ERPNext v16.50.0 (official Docker image, demo data) in the build environment and passed
  the contract suite against it, including 12 customers sharing one modified time.

**Problems found and fixed**
- ERPNext's list API only returns child-table rows (order lines, payment allocations) when asked for
  the child type directly with `parent=<parent type>`; the adapter fetches them that way in one call
  per page.
- Item Price needs the Sales Master Manager role (Sales User alone gets "Insufficient Permission");
  the full connection check now lists unreadable record types by name.
- The site time zone is only readable by System Managers, so it is a connection setting
  (Asia/Kolkata by default) and the check says where it came from.
- On a freshly created site, an API key generated before the workers restarted failed with "Failed to
  decrypt key"; restarting ERPNext and generating the key again fixed it. Worth a line in the setup
  steps.

**Not done / next**
- Tables, sync engine, mapping studio, health page and alerts wait for E1-E9 and V1-V3.
- End of day: waiting on Nischay for E1-E9 and V1-V3 (Step 3 design), U4 (stops and overrides on a
  merge), D3 (new Groq/Gemini keys) and D4 (daily update time). Nothing is blocked on anyone else.

## 2026-10-08 (Day 1, continued): Step 2 Agent Runtime, Policy Gate, Autonomy, Audit Chain

**Decisions (Nischay):** T1-T8 all A (structured rules as sentences; Decision + "Waiting for you" now;
autonomy per agent with tier/account overrides; definitions in code; own runtime on Celery; one hash
chain per tenant; Data Steward + test-only Sandbox Agent first; takeover of a whole record).
S5 A: accept the unmet 95% auto-resolve check for now.

**Done**
- Migration `0004_agent_runtime`: agent_settings, autonomy_overrides, kill_switches, policy_rules,
  record_takeovers, agent_runs, decisions, audit_records (append-only: privileges revoked + trigger),
  sandbox_actions, and `accounts.tier`. All with forced RLS.
- `app/agents/`: registry (agents, tools, facts, levels), the policy gate, `call_tool()` runtime with
  decisions and resume-on-approval, rule matching/validation/sentences, controls (levels, overrides,
  kill-switches, takeovers, tier inheritance), the Sandbox Agent. `app/audit/chain.py`: append + verify.
- Data Steward: merge tool + charter (`app/steward/agent.py`); `process_record` proposes merges through
  the gate; manual merges and "not a duplicate" close waiting cards; merges and undos are audited;
  stops and overrides follow the kept account on a merge. `STEWARD_AUTO_MERGE` removed.
- API: `/agents`, `/agents/{key}/level`, overrides, `/agents/sandbox/try`, `/kill-switches`,
  `/policies` (+ catalog, preview), `/decisions` (+ count, approve, reject, acknowledge), `/audit`
  (+ verify), `/takeovers`, `/roles`, `/permissions`, `POST /users`, `PUT /users/{id}/roles`.
- Frontend: Decisions page and nav count; Agents section (agent page, policy rule builder, stops, audit
  log with chain check, sandbox "Try it"); takeover banner and tier on accounts; Users with roles.
- `evals/policy_gate.py` chaos test: 312/312 over-limit calls correct, 400/400 overall, 10/10
  approvals executed once, chain verified. Duplicate eval unchanged through the gate (0 wrong merges).
- Tests: 58 backend (17 new: gate, rules, overrides, stops, takeover, approvals by role, resume
  re-check, steward behind the gate, tamper detection, chaos), 10 frontend. Browser walkthrough: rule
  builder → sandbox try → Decisions approve (payment and merge) → notices → takeover → stop all →
  audit chain check → users and roles.

**Problems found and fixed**
- A new amount condition defaulted to "is less than"; the builder now starts with "is more than".
- Sentences lower-cased "INR"; rupee amounts now use Indian grouping (₹1,50,000).
- A decided card vanished from "Waiting for you" before showing its outcome; it now stays, marked.
- Concurrent approvals could both run the action; the decision row is now locked and re-read first.
- `pgrep -f`/`pkill -f` with the pattern in the same shell command matches the shell itself; stop
  servers by PID from `ps | grep "[p]attern"`.

**Not done / next**
- U1-U4 (tier names, tier inheritance, admins deciding role-named cards, controls following a merge)
  were built as option A. Nischay confirmed U1 A, U2 A, U3 A the same day (no code change needed);
  U4 still waits for his answer (HANDOVER section 7).
- Drift monitors and per-release golden-set evals wait for model-driven agents; slow actions approved
  from a card will need to run in the worker. See HANDOVER section 6 "Known gaps".
- Next: Step 3, F12.6 Integration Framework and the ERPNext adapter, starting with design questions.

## 2026-10-08 (Day 1, continued): Step 1 Golden Record & Data Steward

**Decisions (Nischay):** D1 A Docker Desktop for local Postgres + Redis; D2 A login with company code +
email + password; S1 A offline GSTIN/PAN/CIN checks; S2 A Gemini embeddings as a review-only signal;
S3 A auto-merge only on hard identifiers; S4 A synthetic duplicate set.

**Done**
- Migration `0002_golden_records`: accounts (group/entity/site), contacts, field_provenance,
  match_candidates, merge_events, import_batches, all with forced RLS.
- Migration `0003_account_embeddings`: pgvector extension and name vectors per account.
- `app/steward/`: identifier checks, name normalising, three-stage matching with bands and reasons,
  review queue, merge with survivorship and undo, branch-site rule (same PAN, other state's GSTIN),
  hygiene score, CSV import through the outbox and worker, enrichment proposals.
- LLM gateway: `embed()` next to `complete()`, with the same budget, fallback and call recording;
  new `embedding` tier (Gemini `gemini-embedding-001`).
- API: `/accounts` (+ `/accounts/check` duplicate check before saving), `/contacts`, `/steward/*`
  (candidates, merge, not-duplicate, merges + undo, imports, enrich, proposals, hygiene, rescan).
- Frontend: Accounts, account detail, create/edit form with live checks, Contacts, Data Steward
  (review queue, side-by-side review, merge history with undo, imports, hygiene dashboard).
- `evals/duplicates.py`: 0 wrong merges; 98.8% of duplicates with GSTIN/PAN auto-merged; duplicates
  without identifiers all go to review (HANDOVER section 6 has the full numbers).
- Tests: 41 backend, 6 frontend, all passing. Browser walkthrough: import CSV → auto-merges and branch
  site → review queue → merge → undo; new-account form flags an existing record and a bad phone.

**Problems found and fixed**
- Different spellings of the same company scored too low on the core-name ratio and too high on
  domains shared by unrelated companies; auto decisions now use the full-name ratio, and the eval set
  gives each company its own domain.
- A GSTIN with a typo used to yield a wrong PAN; invalid GSTINs are now ignored for PAN and flagged as
  a "probable typo" review signal instead.
- `updated_at` refreshed by the database caused async lazy-load errors; it is now set in Python.
- The test database reset dropped the pgvector extension (only a superuser can re-create it); the
  reset now drops tables only.
- A company picker inside a form `<label>` cleared itself (the browser re-sends a click inside a label
  to its first control); `Field` has a `group` mode for such widgets.

**Not done / next**
- The F12.1 "≥95% auto-resolved" check is not met with S3A (54.2%). Nischay chose S5 A: accept for now
  and measure again on the real customer export before Phase 2.
- Meaning match waits for the Gemini key (D3). Auto-merge goes behind the policy gate in Step 2.
- Next: Step 2 (agent runtime, policy gate, autonomy, audit chain), starting with design questions.

## 2026-10-08 (Day 1): Step 0 foundations

**Decisions (Nischay):** 1A repo in the `Forge Project` folder, no GitHub; 2B lean infra; 3C LLM
gateway (Groq/Gemini now, Claude later); 4A Vite + React; 5A ERPNext; 6A audit with the runtime;
7A plain screens with design tokens; 8A carry auth + UI shell now.

**Done**
- Created `forge-crm` with `backend/` (FastAPI, Python 3.12, uv), `frontend/` (Vite, React 19,
  Tailwind 4), `infra/`, `docs/`.
- Migration `0001_foundation`: tenants, users, roles, user_roles, outbox_events, llm_calls,
  tenant_llm_budgets. Forced row-level security on every tenant table; app role `forge_app`.
- Auth: login (company code + email + password), refresh, `/auth/me`; `require_permission()` on
  endpoints; `/users` as the first protected endpoint.
- Transactional outbox with Celery beat delivery (retry, backoff, dead status).
- LLM gateway with tier routing, fallback, call recording and monthly budget.
- Frontend: carried over design tokens and UI components from the old CRM; login, nav filtered by
  permission, Home and Users pages.
- Tests: 15 backend (auth, RBAC, tenant isolation, outbox, gateway), 2 frontend. All passing.
  Browser check: login → Users page works.

**Problems found and fixed**
- The old frontend `tsconfig.json` had no `jsx` setting, so `tsc` could not type-check JSX; added
  `"jsx": "react-jsx"` and `strict`.
- Tables use FORCE RLS, so even the owner (migrations / CLI) must set the tenant before inserting.

**Not done / next**
- pgvector, model prices, observability (see HANDOVER section 6).
- Waiting on D1–D4 (HANDOVER section 7), then Step 1: F12.1 Golden Record & Data Steward.
