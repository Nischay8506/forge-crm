# Forge CRM: Handover

**Read this first if you are picking up the project.** It is kept current: every working day
ends with this file and [BUILD_LOG.md](BUILD_LOG.md) updated. If something here is out of date,
the build log's latest entry wins.

Last updated: 2026-10-12

---

## 1. Where things are

| What | Where |
|---|---|
| This repository | `C:\Users\nisch\OneDrive\Desktop\CognitBotz\Forge Project\forge-crm` (decision 1A). Since 2026-10-12 also on GitHub, in the **private** repo [Nischay8506/forge-crm](https://github.com/Nischay8506/forge-crm), so a teammate can review it (section 3) |
| Requirements (what to build) | `Forge Project\Forge_CRM_Requirements.txt`: 69 features, 6 phases. Ops (later): `Forge_Ops_Requirements.txt` |
| Architecture | `Forge Project\Forge_CRM_Architecture_Blueprint.html` (7 sheets) |
| UI designs | `Forge Project\design.html` (design tokens + 8 screens) |
| Build guide (source of the requirements) | `Forge Project\Forge_CRM_Developer_Build_Guide.docx` |
| Old prototype (reference only, never edit) | `Forge Project\CRM\CRM`. Review of it: `CRM_Code_Review.md` in the Claude project files |
| Phase 1 plan: starting point and flow | `Phase1_Starting_Point_and_Flow.md` in the Claude project files (`plans/`) |
| Each step's questions and the options Nischay picked | `plans/` in the Claude project files: `Step2_…` to `Step6_…`, `Phase2_M1_Enquiry_Questions.md` (N1-N15), `Phase2_M3_CPQ_Questions.md` (C1-C18, waiting for answers) |

The old prototype's `backend/.env` holds live keys. Do not copy it; use new keys in `backend/.env`.

## 2. What we are building, in order

Build order (requirements Section 4): **Phase 1 = Module 12 Platform Foundations**, then Phase 2 =
M1 Enquiry → M3 CPQ → M4 Order, then M2+M5, M8, M6+M7+M9, M10+M11.

Phase 1 steps:

| Step | What | Status |
|---|---|---|
| 0 | Foundations: repo, migrations, tenancy (RLS), login + RBAC, event outbox, worker, LLM gateway, UI shell | **Done 2026-10-08** (see section 6 for what is still open) |
| 1 | F12.1 Golden Record & Data Steward (dedup/merge/enrich, hygiene score, adjudication screen) | **Done 2026-10-08**, with one accepted gap: ≥95% auto-resolve not met on the synthetic set (54% with S3A); re-measure on real legacy data before Phase 2 (decision S5 A) |
| 2 | F12.2 Agent Runtime, Policy Engine, Autonomy **+ minimal audit chain** (decision 6A) | **Done 2026-10-08**: both F12.2 "Done When" checks pass (section 6). U1-U4 confirmed as built |
| 3 | F12.6 Integration Framework & Connector SDK, ERPNext adapter (decision 5A) | **Built 2026-10-09**: both F12.6 "Done When" checks pass in Claude's environment on a real ERPNext 16 (section 6). Waiting for Nischay's own check in Docker Desktop (`infra/erpnext/README.md`). Defaults kept as built (X1 A) |
| 4 | F12.4 Audit replay UI, redaction, retention | **Built 2026-10-09**: both F12.4 acceptance checks pass in Claude's environment (section 6), built to R1-R8 (all A) and Nischay's Step 4 safeguards. Retention periods need legal validation before production (section 7) |
| 5 | F12.3 Human Approval Inbox (Design File frame 05) | **Built 2026-10-09** to H1-H9 and Nischay's Step 5 safeguards; Claude's automated browser check passes (median decision 3 s, section 6). The Done-When stays **waiting for Nischay's usability run** with real people (H8 C, `docs/USABILITY_TEST.md`); the first session ran on his PC on 2026-10-10, results to come |
| 6 | F12.5 Multilingual & Channel Fabric (email now; WhatsApp and SMS on the later list) | **Email built 2026-10-10** to L1-L11 (section 3). F12.5 check 2 (limits enforced for every sender) is met for email; check 1 (the same message correctly toned in EN / HI / TE) waits for the Gemini key (D3) and native speakers' reviews, 10 per language (L11 A). F12.5 stays open for the WhatsApp half (later list) |

Phase 2 (the money path), in order:

| Module | What | Status |
|---|---|---|
| M1 | Enquiry & Lead Management (F1.1-F1.10, Design File frame 02) | **Built for email 2026-10-12** to N1-N15 and P1-P3 (section 3): F1.1, F1.4-F1.10. F1.2 (WhatsApp) and F1.3 (web form, marketplaces) are on the later list (N2 A, N14); F1.1's 95% check and scan reading wait for D3 and real samples (section 6) |
| M3 | CPQ: configure, price, quote (F3.1-F3.9, frame 04) | **Questions C1-C18 sent 2026-10-12**, waiting for Nischay's answers. Nothing is built until he answers |
| M4 | Order, contract & ERP handoff (F4.1-F4.5) | Not started |

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
| U4 A | Merging an account that has a stop or an autonomy override moves it to the kept account (undo moves it back) | 2026-10-09 |
| E1 A | A real ERPNext (official Docker image, demo data) runs in Claude's build environment for development and tests, plus a fake ERPNext in the test suite; Nischay runs the same setup in Docker Desktop for the final check (`infra/erpnext/`) | 2026-10-09 |
| E2 A | Proper tables per contract (ERP customer links, items, prices, orders, invoices, payments, stock), each with ERP id, connection and last-synced time. Stock and credit are read-only copies with an "as of" time. The account page gets read-only Orders, Invoices and Outstanding tabs | 2026-10-09 |
| E3 A | ERPNext → Forge for everything; the one write is "create this account as a customer in ERPNext", through the policy gate | 2026-10-09 |
| E4 A | The Data Steward matches ERP customers to accounts: confident match links, possible match waits for a person, no possible match creates a new account marked "from ERPNext"; several ERP codes can point to one account | 2026-10-09 |
| E5 A | ERPNext owns legal and money fields (legal name, GSTIN, PAN, billing address, credit limit, payment terms); Forge owns commercial fields. A contradiction the steward cares about goes to review | 2026-10-09 |
| E6 A | Check for changes every 5 minutes, connection check every minute, webhooks optional, full reconciliation every night | 2026-10-09 |
| E7 A | A broken connection raises an alert card in "Waiting for you" plus a red banner on the Integrations page naming the cause; records that keep failing become tasks (Retry, Fix mapping, Skip) | 2026-10-09 |
| E8 A | ERP logins entered in the setup screen, stored encrypted with a key that lives only in `backend/.env`; only the last 4 characters are shown; changes audited | 2026-10-09 |
| E9 A | A small MCP server now (find an account, its orders and outstanding invoices, plus the gated create-customer write), all through the policy gate and audit chain; GraphQL later | 2026-10-09 |
| V1 A | Support ERPNext v15 and v16; contract tests run against both | 2026-10-09 |
| V2 A | A company can have more than one ERP connection; the setup screen adds one at a time | 2026-10-09 |
| V3 A | The mapping studio maps customers → accounts plus the code lists Phase 2 needs (units, customer groups, territories, payment terms, warehouses, tax templates); rule-based suggestions until the D3 keys are set, then AI-assisted | 2026-10-09 |
| W1 A | Forge's own code lists for ERP mapping: units use the GST unit codes (UQC) and tax uses the GST rate slabs; customer groups, territories, payment terms and warehouses start as a copy of the first ERP's lists, and later ERPs map onto them | 2026-10-09 |
| X1 A | Integration defaults kept as built: ERP Connector agent at L2, Outside AI tools agent at L0; alerts after 3 minutes unreachable, 3 failed reads in a row, 15 minutes without a read, or a run stuck 30 minutes (a rejected key alerts at once); nightly comparison after 02:00 IST; ERP customers disabled in the ERP are not made into accounts; "create in ERPNext" sends name, type and GSTIN only | 2026-10-09 |
| Order | Step 4 starts before Nischay's own Step 3 check; he runs that check whenever he can and anything it finds is fixed alongside | 2026-10-09 |
| R1 A | Each audit record is split: the chain seals a short skeleton (who, what, when, which record, gate result) plus a fingerprint of the details; the details ("evidence": inputs, prompts, message text, personal data) sit next to it and can be removed by retention or erasure without breaking the chain. Records written before Step 4 stay as they are | 2026-10-09 |
| R2 A | The trail keeps a copy of exactly what an agent was given (record fields, documents, retrieved passages, the full prompt and the model's answer), each with its source record and when it was read, as evidence under R1 | 2026-10-09 |
| R3 A | The chain is sealed every hour: Forge signs the latest hash with a key kept outside the database (`backend/.env`, rotatable); the chain check also checks every seal | 2026-10-09 |
| R3 B later | At real deployment, before the first paying customer, each hourly seal is also copied to an outside write-once store (for example S3 Object Lock), so even someone with full server access cannot rewrite history and re-sign it. Not built now; the seals are built so they can be copied out as they are (section 7) | 2026-10-09 |
| R4 A | Personal data in the trail is hidden by default for everyone; the permission "See personal data in the audit trail" lets a person reveal it for one replay after typing a reason, and the reveal is recorded. Hiding uses known personal fields plus patterns in free text (emails, phones, PAN, Aadhaar) | 2026-10-09 |
| R5 A | Every company starts on an editable India retention profile: skeleton and evidence behind customer-facing or money actions 8 years; other evidence 3 years; ERP sync history and model-call logs 1 year; a legal hold on an account pauses deletion; a nightly job deletes and records each run. Periods are a starting suggestion, to be confirmed by Nischay's CA or lawyer | 2026-10-09 |
| R6 A | An admin can erase a contact: personal data is removed from the contact, their messages and the trail's evidence, what the law requires stays, a stub remains; a second person approves on a decision card; the erasure is recorded | 2026-10-09 |
| R6 ERP copy | Erasing a contact also blanks their data in Forge's copy of ERP records and blocks it from coming back on later syncs (keyed fingerprints of the email and phone, no plain values kept); the ERP itself is not changed ("Erase and block", Nischay's card, 2026-10-09 17:19Z) | 2026-10-09 |
| Retention minimums | Retention periods can never be set under 8 years for the audit skeleton or under 12 months for anything else, so a typo cannot make the nightly clean-up delete recent or legally required history ("Keep both", Nischay's card, 2026-10-09 17:25Z) | 2026-10-09 |
| R7 A | Message provenance built now (message record, "sent by our AI assistant" marker, takeover trail, replay from a message), proved with a Sandbox test message that is never sent; re-checked in Step 6 with real email and WhatsApp | 2026-10-09 |
| R8 A | An "Evidence" tab per agent, by month and agent version: actions, approvals, rejections (overrides), blocks, failures and override rate, linked to the audit records; agents get version numbers that change with their charter, tools or model tier | 2026-10-09 |
| Step 4 safeguards | Nischay's conditions on R1-R8 (2026-10-09 16:19Z): (1) evidence is encrypted and secrets are removed before it is stored; skeletons avoid directly identifying personal data; the trail keeps the exact context an agent saw, without credentials or unnecessary sensitive data; (2) signing keys stay outside the database, old keys stay usable for checking after rotation, missing, changed or reordered records **and seals** are detected, and sealing can later be anchored in an outside write-once store; (3) personal data hidden by default, revealed only with the permission, a reason and an audit record; (4) the India profile is an editable starting point, not a blanket legal rule: retention is set per record category, applicable law and legal hold, and is legally validated before production; (5) erasure needs a second authorised person and covers related personal data, evidence, embeddings and other Forge-controlled stores, subject to lawful retention, leaving a non-personal record of the erasure; (6) replay shows the historical inputs, retrieved evidence, model and version, policy checks, human decisions, tool calls and outcomes, and never re-runs a side-effecting action; (7) per agent version: actions, approvals, rejections, blocks and errors linked to their records; raising autonomy stays an explicit decision by an authorised person, never automatic from good numbers | 2026-10-09 |
| Safeguards | Nischay's conditions on E1-E9 (2026-10-09): (1) creating a customer in ERPNext passes duplicate checks, permission checks and idempotency; (2) matching puts verified identifiers first and ambiguous matches need a person's approval; (3) conflicting customer data from different ERP connections keeps its source and goes to review, never silently overwrites; (4) ERP logins use authenticated encryption, never appear in logs, and the key can be rotated; (5) monitoring catches sync delays, failed jobs and revoked logins. Full audit trail for ERP writes, mapping changes, merges and manual overrides | 2026-10-09 |
| H1 A | Approver chains live in the policy rule's "Who decides": one or more steps, each a role (e.g. over ₹5,00,000: Sales Head, then Managing Director). The card goes to step 1, then step 2; the action runs only after the last approval; any rejection ends it. In Phase 2, F3.4 shows the same rules as a DOA table | 2026-10-09 |
| H2 A | Counter: the approver changes the values the tool allows (e.g. amount) and approves the changed version; the changed action goes through the policy gate again, so a counter can never pass a block rule or skip a higher approver; the card keeps both what was asked and the counter | 2026-10-09 |
| H3 A | A person can hand one card to a named colleague, or set "out of office" with a stand-in and dates; the stand-in decides those cards on their behalf, recorded as "Priya for Ravi"; four-eyes holds (nobody decides a card they asked for, even on someone's behalf). Who sets the stand-in's scope: the person handing over picks the card types and, for cards with an amount, a maximum, never above their own authority; it takes effect at once and is audited ("Delegator sets scope", Nischay's card, 2026-10-09 18:11Z) | 2026-10-09 |
| H4 A | Each card has a deadline in working hours from its rule (default 4 working hours); each company has a calendar (Mon-Sat 09:30-18:30 IST plus an editable holiday list); reminder at 75% of the time; on a missed deadline the card turns red and also goes to the next role up (the chain's next role, or full-access admins); a card is never approved or rejected automatically. F1.10 reuses the calendar | 2026-10-09 |
| H5 A | Now: the inbox works in a phone's browser and sends browser push notifications (desktop and Android Chrome; iPhone after adding Forge to the home screen). WhatsApp buttons and email arrive in Step 6, where the 60-second check is repeated | 2026-10-09 |
| H6 A | A card's history comes from what Forge holds: account tier, earlier decisions on the account and how they ended, and for ERP-linked accounts the outstanding, overdue and last-12-months invoiced amounts from the ERP copy. Unknown shows "not known". Each agent gives its recommendation as one of the card's options plus a reason | 2026-10-09 |
| H7 A | Forge keeps a fingerprinted copy of the card as shown; the decision is sent with the fingerprint; if the card changed in between, the decision is refused and the card reloads ("this card changed while you were looking"); the copy goes into the audit evidence, and the card later shows what happened next | 2026-10-09 |
| H8 C | Claude's automated browser check now, plus Nischay's real 15-minute test with two or three colleagues on a demo company with about ten prepared cards; the Done-When stays "waiting for your run" until the real test is done | 2026-10-09 |
| H9 A | An "Approvals" view per decision type and per approver: counts, median waiting time, median decision time, override rate and missed deadlines; the inbox header shows your own median and override rate; a bottleneck alert card goes to full-access admins when a person or role has more than 5 cards past deadline, or a decision type's median wait doubles week on week | 2026-10-09 |
| Step 5 safeguards | Nischay's conditions on H1-H9 (2026-10-09 17:57Z): (1) mandatory DOA stages cannot be bypassed, a rejection ends the chain, the action runs only after all required approvals (built as: a different person for each step, and the asker never approves); (2) countered values are checked against the tool's allowed fields, pass the policy gate again and trigger any extra approvals; the original and countered values stay in the audit trail; (3) delegates need explicit authority for the approval scope, no unrestricted inheritance of financial authority, four-eyes holds during delegation and out-of-office; (4) configurable business calendars, reminders and escalation rules; a missed deadline never approves anything or skips a mandatory stage; (5) ERP figures on a card show their last-synced time, and stale ERP data is never presented as current; (6) the exact decision-relevant card content shown is recorded, its fingerprint checked when the decision is sent, and the resulting action and audit references kept; (7) automated tests first, then the Done-When stays pending until real users finish the 15-minute test with prepared cards; target median under 60 seconds; (8) analytics: decision counts, waiting times, decision times, overrides, missed deadlines and bottleneck alerts; overrides counted only where the agent's recommendation was recorded | 2026-10-09 |

| L1 (email only) | Step 6 builds email only. WhatsApp and SMS go on the later list (section 7), not built now (Nischay, 2026-10-10) | 2026-10-10 |
| L2 A | Email goes out over SMTP from the company's own mailbox or domain (Google Workspace, Microsoft 365, Zoho). Statuses: sent, and bounced when the mail server says so; no read tracking. Reading the sales inbox (Gmail or Microsoft Graph) comes with F1.1 in Phase 2 | 2026-10-10 |
| L3, L4 later | WhatsApp (Meta Cloud API was the recommendation) and SMS (MSG91 after DLT registration) are not added now; they wait on the later list in section 7 | 2026-10-10 |
| L5 A | Each message is written natively in the customer's language (not translated from English), following the company's tone guide and glossary for that language, and checked before sending. English, Hindi and Telugu now; more languages are added as settings, not code. Gemini writes Indian languages once the D3 keys are in | 2026-10-10 |
| L6 (major languages) | Forge recognises the language of incoming messages by script, for major languages only: English (Latin script) plus Hindi, Marathi, Telugu, Tamil, Kannada, Malayalam, Gujarati, Bengali, Punjabi and Odia (Nischay chose "Major scripts" on the card, 2026-10-10). Minor languages and Indian languages typed in English letters come later | 2026-10-10 |
| L7 A | Each company has a tone guide per language (formality, greeting and sign-off, words to avoid; starting versions for English, Hindi, Telugu) and one glossary (terms kept as they are, fixed translations, forbidden words). A message that breaks the glossary is rewritten once with the problem pointed out; if it still breaks, a person reviews it on a card. Every check is recorded | 2026-10-10 |
| L8 A | Per contact, across channels: promotional and follow-up messages at most 1 a day and 3 a week; transactional messages (a reply, order and invoice updates, approvals they asked for) not capped but counted. Quiet hours 21:00-09:00 for everything except replies to a message the customer just sent; promotional messages also wait on Sundays and company holidays. A held message goes out at the next allowed time unless a person sends it anyway, which is recorded. Each company can change the numbers | 2026-10-10 |
| L9 A | Consent is recorded per contact and channel (when, how, from whom). "STOP" or "unsubscribe" on any channel stops promotional messages on all channels at once, is confirmed to the customer and recorded; transactional messages still go. Erasure (Step 4) also removes consent records except what the law keeps. (WhatsApp's opt-in rule applies when WhatsApp is added) | 2026-10-10 |
| L10 A | Speech-to-text: Whisper on Groq through the LLM gateway, with the company's glossary as hints, built as an adapter so Sarvam AI can be added if Telugu quality is poor | 2026-10-10 |
| L11 A | Built and proved with a simulator first (a test email inbox with receipts, replies, unsubscribe and caps), then real accounts connected as they are created. Done-When: Claude's automated checks, then native speakers review a sample of 10 messages per language; F12.5 stays "waiting for review" until they have | 2026-10-10 |
| Step 6 Done-When | With WhatsApp on the later list, the email half of F12.5's Done-When is checked now and F12.5 stays open for the WhatsApp half (Nischay chose "Email now, keep open" on the card, 2026-10-10) | 2026-10-10 |
| GitHub (changes 1A) | The code and docs also live in the GitHub repo Nischay8506/forge-crm, so a teammate can review it and start their part (Nischay, 2026-10-12 08:25Z). It is meant to be **private** ("make it private"). No `.env` file or key ever goes in. Claude pushes the real history but cannot change the repo's settings (visibility, collaborators); Nischay does those | 2026-10-12 |
| Docs time | Documentation (this file, BUILD_LOG.md, README and the Build Map) is updated only after 17:30 IST, never before or while building; daytime commits hold code and tests only (Nischay, 2026-10-12) | 2026-10-12 |
| N1 A | M1 is built as one thin email path first (F1.1 → F1.5 → F1.9 → F1.10 → F1.8, on the Enquiry Queue), then F1.4, F1.6 and F1.7 add to it | 2026-10-12 |
| N2 A | WhatsApp enquiries (F1.2) wait with WhatsApp; M1 counts as done without them and stays open for them | 2026-10-12 |
| N3 A | The sales mailbox is read with the Gmail API (Google Workspace) or Microsoft Graph (Microsoft 365), signed in with OAuth, with instant notification plus a check every 60 s; any other provider over IMAP. Only the one sales/RFQ mailbox a company connects, never personal mailboxes | 2026-10-12 |
| N4 A | CognitBotz registers one Forge app with Google and one with Microsoft; each company's admin approves it. Until Google reviews it, Gmail runs in testing mode (up to 100 users, mailbox sign-in renewed every 7 days) | 2026-10-12 |
| N5 A | Emails that are not enquiries are labelled and kept in "Other mail"; one click turns one into an enquiry; a changed label is recorded; complaints notify a role the company picks | 2026-10-12 |
| N6 A | Claude's synthetic test set first (200 emails and BOMs); before an M1 feature is marked done it is measured again on about 100 real anonymised emails and real BOMs and drawings, and tried on Nischay's PC with a real test mailbox | 2026-10-12 |
| N7 A | The acknowledgement is sent on its own for non-strategic accounts and drafted for a person for strategic accounts and tenders. A hard check blocks any message about an enquiry that promises a price, discount, delivery date or that we can make or have something | 2026-10-12 |
| N8 A | A setup screen for the capability profile ("What we make") and product families, with starter templates (steel, fabrication, machined parts, castings); a sample steel and fabrication company for tests | 2026-10-12 |
| N9 A | Raw emails and attachments go through one file-storage interface (a local folder now, an S3-compatible bucket in deployment), encrypted with the company's key, under the same retention and erasure rules; at most 25 MB per email | 2026-10-12 |
| N10 A | PDFs with a text layer, Excel and CSV are read directly; scans, photos and drawings go to Gemini vision through the LLM gateway (needs D3); unsure fields are highlighted for a person | 2026-10-12 |
| N11 A | Enquiries merge on their own only on the same email thread, or the same phone with overlapping lines, within 30 days; other look-alikes are flagged for a person, whose answer is remembered; every merge can be undone | 2026-10-12 |
| N12 A | Owner: the account's owner → a product-family specialist → the customer's region → round-robin by fewest open enquiries; the dealer step waits (Q12); leave uses Forge's Away; a manager reassigns with a reason | 2026-10-12 |
| N13 A | Each person has a "reports to". Timers: first response 15 minutes around the clock (the automatic acknowledgement counts), owner's first look 2 working hours, quote sent 1 working day; owner reminded at 75%, manager at the deadline, head of department at twice it; app push and email | 2026-10-12 |
| N14 | "Test only for emails and later will add other platforms": the web form and the marketplaces (F1.3: IndiaMART, TradeIndia, Alibaba) go on the later list; M1 is built and tested for email only | 2026-10-12 |
| N15 A | Lead scoring v1: rules and weights the company can change, the top 3 reasons in words; v2 (learned from won and lost deals) later, after M2 and M3 | 2026-10-12 |
| P1 A | A "we can't make this" (no fit) decline is drafted by Forge and goes out only after a person approves it ("Draft first") | 2026-10-12 |
| P2 A | An enquiry that needs engineering gets a review step for the company's engineering role, with the lines, files and reasons and its own timer ("Review step") | 2026-10-12 |
| P3 A | The automatic acknowledgement goes to standard and not-yet-checked enquiries; configurable, needs-engineering and no-fit enquiries get a draft for a person ("Standard + unchecked") | 2026-10-12 |
| Decline only | A no-fit enquiry from a non-strategic account gets only the decline draft, not an acknowledgement draft too, so the customer gets one clear email (Nischay's card, 2026-10-12 09:01Z) | 2026-10-12 |
Still open (section 7): the M3 questions C1-C18, D3 new LLM keys, D4 daily update time.

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
uv run celery -A app.worker.celery_app worker --beat --loglevel=info   # Linux and macOS
```
On Windows, Celery cannot run the scheduler inside the worker (`-B option does not work on Windows`), so
run them in two windows:
```bash
uv run celery -A app.worker.celery_app worker --loglevel=info --pool=solo
uv run celery -A app.worker.celery_app beat --loglevel=info
```
With the Docker Postgres from `infra/`, the database passwords in `backend/.env` are `forge_owner` for
`MIGRATION_DATABASE_URL` (set by `infra/postgres-init.sql`) and whatever `APP_DB_PASSWORD` says for
`DATABASE_URL` (the first migration gives `forge_app` that password). A step-by-step Windows guide
for the Step 5 usability test, from installing the tools to reading the results, is the Claude Doc
"Step 5 Approval Test Guide": https://claude.ai/code/artifact/8a0fa397-9f36-403a-9bec-b91d11efcbde
ERP logins are stored encrypted (E8), so `backend/.env` needs `CREDENTIAL_KEYS` before an ERP can be
connected (the Integrations page says so until it is set):
```bash
uv run python -m app.cli new-credential-key     # prints a line like k20261009:<key>; put it in CREDENTIAL_KEYS
```
To rotate: run it again, put the new key **first** (`CREDENTIAL_KEYS=knew:...,kold:...`), restart the
backend and worker, run `uv run python -m app.cli rotate-credentials`, then remove the old key. Losing
every key means re-entering each ERP's API key and secret. Keep the key out of git and backups of the code.

The audit trail (Step 4) needs two more keys in `backend/.env`:
```bash
uv run python -m app.cli new-evidence-key   # prints e<date>:<key>; put it in EVIDENCE_KEYS (REQUIRED)
uv run python -m app.cli new-seal-key       # prints s<date>:<key>; put it in AUDIT_SEAL_KEYS
```
- `EVIDENCE_KEYS` encrypts the details behind each audit record (inputs, prompts, message text, personal
  data). **Required**: without it, anything that writes to the audit trail refuses to run, on purpose.
  **Back it up somewhere other than this server**: losing it makes the stored details unreadable (the
  chain still checks, but replays show "details unreadable"). Rotate: new key first
  (`EVIDENCE_KEYS=enew:...,eold:...`), restart, `uv run python -m app.cli rotate-evidence`, then remove the old key.
- `AUDIT_SEAL_KEYS` signs the hourly seal of each company's chain (R3). Without it the app runs but the
  Audit log says seals are off. Rotate: new key first and **keep the old one after it** so old seals can
  still be checked. Back it up off the server too.
- The worker (`--beat`) seals every hour and runs the retention clean-up at 03:00 IST.

Approval-inbox notifications (Step 5, H5) are optional. Step 5 added the `pywebpush` package, so run
`uv sync` once after copying. Then:
```bash
uv run python -m app.cli new-push-keys      # prints VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY lines
```
Put both lines (and `VAPID_SUBJECT=mailto:<your address>`) in `backend/.env`, restart the API and the
worker, and press "Turn on alerts on this device" on the Decisions page. Push needs HTTPS or
`localhost`: it works in the PC's own browser now, and on phones once Forge runs at an `https://` address.
Keep the private key out of git; new keys mean everyone turns alerts on again.

**Email and languages (Step 6).** Step 6 added migration 0009, so run `uv run alembic upgrade head` once
after copying. Email starts in **test mode**: everything Forge sends lands in Messages → Test inbox,
where you act as the customer (reply, make it bounce, press unsubscribe). Nothing reaches a real mailbox
until an admin switches it. To send real email:
1. `CREDENTIAL_KEYS` must be set (above): the mailbox password is stored encrypted with it.
2. Messages → Settings → Email account: choose the mail provider, fill in the login and the address to
   send from, Save, then **Check it works** (it logs in without sending). For Gmail or Google Workspace,
   turn on 2-step verification and make an app password at myaccount.google.com/apppasswords.
3. **Switch to live sending** (only possible after a passing check). Back to the Test inbox at any time.
The worker (`beat`) sends due messages every 10 seconds; without it messages wait as Queued. Two
addresses go in links inside emails: `APP_BASE_URL` (default `http://localhost:5173`) for links to
Forge's screens, e.g. an approval card emailed to the person who decides it, and `PUBLIC_BASE_URL`
(default `http://localhost:8000`) for the unsubscribe page. Customers cannot open a localhost address,
so real promotional email needs Forge at a public https address first (Settings says so until then).
"Draft it for me" (the Writing Assistant) needs `GEMINI_API_KEY` and dictation needs `GROQ_API_KEY`
(D3); without them those two buttons say so and everything else works.

**Enquiries (Phase 2, M1).** M1 added migrations 0010-0012 and three packages (`pypdf`, `openpyxl`,
`defusedxml`), so after copying run `uv sync` and `uv run alembic upgrade head` once, then restart the
API, worker and beat. To try it without a real mailbox, use **Enquiries → Test mailbox**: write an email
as a customer (with attachments) and watch it become an enquiry on the Queue. Setup first, under
**Enquiries → Setup**:
- **Product families**: "Start from a template" (steel, fabrication, machined parts, castings).
- **What we make**: "Fill in the sample steel and fabrication company" gives processes with their
  limits, materials, certificates and industries; pick the engineering role here. Without it, the
  "can we make this?" check says "Not checked".
- **Owners**: the routing order, and per person "Takes enquiries", product families, regions, a limit
  and **reports to** (who gets escalations). Accounts get an owner on the account page.
- **Timers and new mail**, **Scoring**: the defaults in section 6 can be changed here.
- **Mailbox**: connect the real sales mailbox. **IMAP** works now for any provider (Zoho, Rediff,
  hosting mail, or Gmail with an app password); the password is stored with
  `CREDENTIAL_KEYS`. **Gmail** and **Microsoft 365** sign-in need Forge's app registrations (N4 A):
  `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`
  (`MICROSOFT_TENANT`, default `common`) in `backend/.env`, with the redirect address
  `<PUBLIC_BASE_URL>/api/v1/public/mail/oauth/gmail/callback` (or `/microsoft/callback`) registered with
  Google or Microsoft. Without them the Connect button says what is missing. `GMAIL_PUBSUB_TOPIC` is
  optional (instant notification; Forge checks every `MAIL_POLL_SECONDS`, default 60, anyway).
Raw emails and attachments are kept encrypted under `FILE_STORE_DIR` (default `backend/var/files`, at
most `EMAIL_MAX_MB` = 25 per email). Their keys are wrapped with `EVIDENCE_KEYS`, so back that key up as
before; `rotate-evidence` also re-wraps the file keys. The worker reads waiting emails and runs the
enquiry timers every 10 seconds, checks the mailbox every 15 seconds (each at most once a minute unless a
push asks sooner) and sends the nudges and parks quiet enquiries every hour; without it emails wait
unread and no timer fires. Without the D3 keys the rules reader reads every email and keeps each one as
an enquiry marked "needs review" (nothing is lost; the label the rules would give is shown), and scans
and drawings are not read.

To try the ERP connection end to end, start a local ERPNext with `infra/erpnext/README.md`.
Outside AI tools (MCP, E9) connect to `http://localhost:8000/mcp/` with a token made on Integrations >
Outside AI tools; set `PUBLIC_BASE_URL` in `backend/.env` when the backend runs at another address.

The worker must be running for the Data Steward to act: new and edited records reach it through the
outbox, and CSV imports run in the worker. Without it records save fine but no duplicates are found.
It also runs the ERP schedule (`--beat`): every minute it checks each connection's login and starts the
reads that are due. Without it no ERP data arrives and no ERP alert is raised. And every minute it
checks approval deadlines (reminders, past-deadline marks, escalation, bottleneck alerts) and sends the
inbox notifications; without it cards still wait and can be decided, but nobody is reminded. Every 10
seconds it sends the messages that are due (Step 6); without it they wait as Queued.

Meaning match (S2A) switches itself on when `GEMINI_API_KEY` is set in `backend/.env`; nothing else
to configure. `STEWARD_EMBEDDINGS=false` turns it off.

A new company starts with one role, Admin (full access). To try approvals by role, create a role on
the Users screen (e.g. Finance with "decisions.decide"), add a user with it, and pick that role under
"Who decides, in order" in a policy rule (add a second step for a chain, and a deadline in working
hours). The Sandbox Agent's "Try it" box (Agents → Sandbox Agent) shows what your rules do with a given
amount. Remember four-eyes: whoever presses "Try it" asked for the payment, so someone else decides it.
The quickest way to see the whole inbox is the usability company: `uv run python -m app.cli
seed-usability --slug ut-demo` (logins are printed; see `docs/USABILITY_TEST.md`).

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
uv run python -m evals.enquiries               # M1 test set, rules reader only (N6 A)
uv run python -m evals.enquiries --pipeline    # the same 200 emails through Forge, in a throwaway company
# against a real ERPNext (skipped otherwise): set FORGE_ERPNEXT_URL, FORGE_ERPNEXT_KEY, FORGE_ERPNEXT_SECRET
uv run pytest tests/test_erp_sync.py -k live
uv run python -m app.integrations.testing.contract erpnext --url ... --key ... --secret ...
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
Since 2026-10-12 the same history also goes to the private GitHub repo (section 1): Claude hands the new
commits over as a git bundle and they are pushed as they are, never with a `.env` file or a key. A
teammate works from a clone of that repo, with their own `backend/.env` and `frontend/.env` made from
the `.env.example` files and their own keys (section 4).

## 5. Code map and the rules the code follows

```
backend/
  app/
    main.py            FastAPI app; no background loops in the web process
    config.py          settings from env / backend/.env
    db.py              engine, sessions, set_tenant(), tenant_session(), system_session()
    rls.py             enable_tenant_rls() helper for migrations
    cli.py             create-tenant (admin bootstrap), new-credential-key, rotate-credentials,
                       new-evidence-key, rotate-evidence, new-seal-key, new-push-keys,
                       seed-usability (prepared cards for docs/USABILITY_TEST.md)
    core/              security (JWT, bcrypt), permissions catalogue, deps (auth + RBAC), errors,
                       secrets.py (AES-256-GCM for stored ERP logins, key ring, rotation, E8),
                       keyring.py (parses "kid:key,kid:key" settings; first key is current)
    models/            SQLAlchemy models (tenancy, outbox, llm, golden = accounts/contacts/steward tables)
    api/v1/            routers: auth, users (+roles), accounts (+contacts), steward,
                       agents (+policies, kill-switches, sandbox), decisions, audit (+takeovers),
                       integrations (connections, wizard test, dead letters, mapping studio,
                       account ERP data, create-in-ERP, webhooks, MCP tokens),
                       audit (+retention, legal holds, reveal), privacy (erasure requests),
                       channels (messages, Test inbox, consent, channel and language settings),
                       enquiries (queue, one enquiry, lines, files, review, Other mail, Test
                       mailbox, setup), public (the unsubscribe page and the mailbox sign-in
                       return, no login)
    events/outbox.py   publish() + dispatch_batch() (transactional outbox)
    worker/            Celery app + tasks (outbox delivery every 2 s, CSV import, ERP tick every
                       60 s and the sync / reconcile runs it hands out, audit seal hourly,
                       retention clean-up 21:30 UTC = 03:00 IST)
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
      sandbox.py         test-only Sandbox Agent (pretend payments; test drafts, never sent, R7;
                         test emails that only reach the Test inbox, Step 6)
      versions.py        agent version numbers from a fingerprint of the definition (R8)
      inbox.py           F12.3: what a decision led to ("What happened next")
      scorecard.py       per agent, month and version: proposed, acted alone, asked, approved,
                         rejected, undone, blocked, failed, override rate (R8)
    approvals/         F12.3 approval inbox (start here: chain.py):
      chain.py           a card's steps (H1), who may decide the current step: four-eyes, role,
                         stand-ins (H3); deciders and escalation targets
      calendar.py        each company's working calendar; deadlines in working hours (H4)
      card.py            the card as shown, its history and ERP figures (H6), its fingerprint (H7)
      counter.py         counter = approve a changed version through the gate again (H2)
      delegation.py      hand one card on, out-of-office with card types and a maximum (H3)
      sla.py             the minute job: reminders, past-deadline marks and escalation,
                         bottleneck alerts (H4, H9); it never decides anything
      notify.py          who hears about a card, via the outbox event "inbox.notify"
      push.py            browser push (VAPID keys), drops dead subscriptions (H5)
      analytics.py       Approval numbers and the inbox header's own numbers (H9)
      usability.py       the company and 11 cards behind `cli seed-usability` (H8 C)
    audit/             F12.4 (start here: chain.py):
      chain.py           append() splits each record into a skeleton (hashed, kept 8 years) and
                         evidence (encrypted, removable), verify() checks records, evidence and seals
      evidence.py        AES-256-GCM for evidence (EVIDENCE_KEYS), rotate_all()
      seals.py           hourly HMAC seals chained to each other (AUDIT_SEAL_KEYS); SealAnchor and
                         ANCHORS = the hook for R3 B (outside write-once copy)
      redact.py          hiding personal data when shown (R4) and stripping secrets before storing
      replay.py          runs list, one run step by step, per-step intact check, hide/reveal
      retention.py       India profile, periods per category with their law, legal review, legal
                         holds, the nightly clean-up (R5)
    channels/          F12.5 sending path (Step 6; start here: send.py):
      send.py            the ONLY way a message is sent: draft() -> queue() (checks) -> deliver_due()
                         (lock, check again, adapter outside the transaction, record), receipts,
                         incoming messages, STOP, send anyway, cancel, resend
      checks.py          the checks in order: recipient, address, agent controls, content, consent,
                         timing (limits, quiet hours, rest days, reply window)
      rules.py           each company's limits and quiet hours; consent.py (opt-out for email,
                         STOP words, unsubscribe tokens); accounts.py (the SMTP account, encrypted
                         password, check, pause); statuses.py (common statuses, receipts never go back)
      adapters/          simulator.py (the Test inbox, fixed bounce/outage/lost addresses), smtp.py
      simulate.py        the customer's side of the Test inbox; staff.py (approval cards by email)
    language/          F12.5 language service: script.py (recognise the language by script),
                       tone.py (tone guides), glossary.py (glossary + text checks), writer.py and
                       agent.py (the Writing Assistant, writes natively in EN/HI/TE), review.py
                       (native-speaker review sampling and report), speech.py (dictation)
    privacy/           erasure.py (what an erasure scrubs, R6) and agent.py (the Privacy agent:
                       L0 only, a second person with "privacy.erase" approves)
    enquiries/         Phase 2 M1 (start here: intake.py):
      intake.py          an arrived email -> an enquiry, a reply on one, or Other mail; the 10 s
                         tick (read waiting emails, timers) and the hourly nudges
      mail/              the sales mailbox: gmail.py (Gmail API), microsoft.py (Graph), imap.py,
                         oauth.py (sign-in, N4 A), service.py (connect, check every minute)
      mime.py            a raw email -> sender, thread, text, attachments, automatic or not
      reader.py          what kind of mail it is and what an enquiry asks for (the AI through the
                         gateway); rules.py is the reader without AI
      documents.py       attachments -> requirement lines (PDF text, Excel, CSV; scans and
                         drawings to the vision model, N10 A), unsure fields, correction examples
      records.py         enquiry numbers, history, the customer behind one, manual enquiries
      dedupe.py          sure duplicates merge (same thread, or phone + lines, 30 days), the rest
                         are flagged; undo (N11 A)
      routing.py         who owns it (N12 A); timers.py the four stage timers (N13 A, P2 A)
      ack.py, guard.py   the acknowledgement, nudges, parking, decline; the commitment guard
      answers.py         a reply's answers into the enquiry and its lines, only where it is clear
                         and never over a known value (F1.8)
      families.py        product families and the details needed to quote each (N8 A)
      capability.py      the "What we make" profile; qualify.py "can we make this?" per line and
                         per enquiry (F1.6); reviews.py the engineering review (P2 A)
      scoring.py         lead scoring v1: Fit, Intent, Risk, priority and reasons (F1.7, N15 A)
      settings.py        each company's routing, timer, intake and scoring settings and defaults
      agent.py           the Enquiry Agent (default L2) and its tools: acknowledge, nudge, park,
                         merge, decline
    files/store.py     stored files (raw emails, attachments): encrypted, local folder now,
                       S3-compatible later (N9 A); rows never deleted, removal blanks the bytes
    sla/engine.py      the timer engine (F1.10): targets on an "always" or "working" clock,
                       pause, resume, reminders; later modules reuse it
    integrations/      F12.6 (Step 3; start here: adapter.py, then runner.py):
      contracts.py       the shared contracts (Customer, Item, Price, Order, Invoice, Receipt, Stock)
      adapter.py         the adapter interface, ConnectorError and its causes (for alerts)
      erpnext/           client.py (Frappe REST, errors -> causes), adapter.py (ERPNext -> contracts,
                         plus find_customers / create_customer for the one write)
      sync.py            run_flow() (one record type, from a high-water mark, resumable, retries)
                         and reconcile() (nightly comparison)
      connections.py     create / change / check a connection, encrypted logins, health problems
      store.py           DbSink: upserts into the erp_* tables, dead letters, "gone" marks
      runner.py          run_sync(), run_reconcile(), tick() (the minute schedule), dead-letter tasks
      alerts.py          the one alert card per connection and the task cards in "Waiting for you"
      linking.py         ERP customer -> account (E4, safeguards 2-3), ERP-owned fields (E5),
                         manual link / new account / ignore / unlink (audited)
      writes.py          "create this account as a customer in the ERP" (E3, safeguard 1)
      mapping.py         code lists and suggestions (V3, W1)
      agent.py           the ERP Connector agent (default L2) and its tools
      mcp_tools.py       the Outside AI tools agent (default L0) and its read tools (E9)
      mcp_server.py      the MCP server mounted at /mcp/ and its tokens
      testing/           contract.py (the certification suite + CLI), fake_erpnext.py, seed.py
  alembic/versions/    migrations (0001 foundation, 0002 golden records, 0003 embeddings,
                       0004 agent runtime + audit + account tier, 0005 integrations,
                       0006 audit evidence, seals, versions, retention, holds, messages, erasure,
                       0007 approval inbox, 0008 approval chains, steps, delegations,
                       inbox settings, push subscriptions, 0009 channel fabric: message
                       columns, message events, consents, channel settings, email account,
                       tone guides, glossary, language reviews), 0010 enquiries (stored files,
                       mailboxes, inbound emails, enquiries and lines, history, product
                       families, settings, SLA timers, account owner, users' reports-to),
                       0011 unsure fields on lines, 0012 capability profiles, the check's
                       stamps, scores, engineering reviews)
  evals/duplicates.py  synthetic duplicate set and quality report (S4A)
  evals/policy_gate.py chaos test: seeded over-limit calls must all be stopped and escalated
  evals/enquiries.py   M1 test set: 200 synthetic emails and BOMs (EN, HI, TE, mixed) measured
                       against the F1.1, F1.4 and F1.8 targets (N6 A)
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
  src/features/decisions Decisions: Inbox (DecisionCard: chain, deadline, history with ERP figures,
                       approve / counter / reject / hand on, "What happened next"), Away and
                       hand-overs, Approval numbers, Deadlines (calendar settings); push.ts +
                       public/sw.js (notifications), public/manifest.webmanifest (add to home screen)
  src/features/agents  Agents (what each can do, level, overrides, stop, sandbox "try it", Evidence
                       tab = scorecard), policy rules with a rule builder, stops, Runs + step-by-step
                       replay, audit log with chain and seal check, PersonalDataBar (hide/reveal),
                       TakeoverPanel (used on the account page)
  src/features/audit   Retention page, message trace (/agents/messages/<id>), and the account-page
                       cards: Messages, Legal hold, "Erase…" on a contact
  src/features/messages Messages (Step 6): list, one message (checks, what happened, send anyway,
                       cancel, resend), New message (MessageEditor with live language and glossary
                       checks, dictation, "Draft it for me"), Test inbox, Language review, Languages
                       (tone guides, glossary), Settings (live switch, email account, limits and
                       quiet hours); ConsentPanel ("Consent" on a contact on the account page)
  src/features/integrations Connections, setup wizard, connection page, Customers and Code lists
                       (mapping studio), Outside AI tools (MCP tokens), AccountErpCard (account page)
  src/features/enquiries Enquiries (M1): Queue (priority, timers, filters, Engineering view), one
                       enquiry (lines with unsure fields, thread, customer, priority, "can we make
                       it", engineering review, acknowledgement), manual enquiry, Other mail,
                       Test mailbox, Setup (mailbox, owners, timers, product families, what we
                       make, scoring), AccountOwnerCard (account page)
infra/                 local Postgres + Redis (Docker); erpnext/ = a local ERPNext 15/16 to test with
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
15. **Nothing writes to an ERP except `app/integrations/writes.py`**, and only through the policy gate
    (tool `erp.create_customer`). It checks duplicates in Forge and in the ERP, refuses a second request
    with the same idempotency key, and looks the customer up by its Forge reference before resending
    after a lost answer. A new ERP write must follow the same pattern (Nischay's safeguard 1).
16. **ERP logins only through `app/core/secrets.py`.** Never log, print, return or audit a key or
    secret; screens and audit records get `secrets.hint()` (last four characters). Models and settings
    objects hide them from `repr`. `tests/test_erp_sync.py` checks logs and the audit log for leaks.
17. **No ERP failure is silent.** A connection problem becomes the connection's alert card
    (`alerts.sync_alert`); a record that cannot be read becomes a dead letter and a task. Anything new
    that can fail in the background must end in one of the two.
18. **Personal data and free text go in evidence, never in the skeleton.** `chain.append()` keeps only
    `SKELETON_KEYS` and `*_id` keys in the hashed skeleton and moves any value that looks personal to
    evidence. Add a key to `SKELETON_KEYS` only if it can never hold personal data.
    `tests/test_audit_trail.py` checks it.
19. **Secrets never reach the trail.** `redact.strip_secrets()` runs before anything is stored; add new
    secret shapes to `SECRET_PATTERNS`.
20. **Anything shown from the trail goes through `replay.shown()`** (hidden by default); revealing needs
    `audit.personal_data`, a reason, and appends `audit.personal_data_revealed`.
21. **Any new store that holds a contact's personal data must be scrubbed by `privacy/erasure.py`**
    (and registered for merges, rule 9). Customer-facing and money evidence is kept by law until its
    retention period ends; a legal hold stops both retention and erasure.
22. **Replay never re-runs anything.** It only reads the trail.
23. **Nobody decides a card they asked for, and nobody decides two steps of one card** (Step 5
    safeguards 1 and 3), not even as someone's stand-in. A full-access admin is no exception.
    `app/approvals/chain.py` (`conflict()`) is the one place this is checked.
24. **A missed deadline only tells people.** Escalation shows the card to the next role and notifies
    them; it never lets them decide a step that is not theirs and never approves anything (safeguard 4).
25. **A stand-in has at most the authority of the person they stand in for**, within the card types
    and maximum amount that person chose. `chain.Inbox.authority()` checks the delegator's own right
    to decide the step every time.
26. **Every decision is sent with the fingerprint of the card as shown** (`card.build()`); a mismatch
    is refused (409 `card_changed`). Anything new shown on a card that matters to the decision goes into
    the fingerprinted content; the ERP figures' read time is shown but not fingerprinted, so a routine
    sync does not refuse decisions, while figures turning stale (or fresh) do change it.
27. **When several rules ask for people, every role they name must approve** (`policy.chain_for`): the
    longest chain sets the order and roles from other rules follow it, each once.
28. **Every message goes through `app/channels/send.py`.** Nothing else may call an adapter or move a
    message past "draft"; `test_only_the_channel_package_reaches_adapters_or_moves_a_message_on` fails
    otherwise. New senders (Phase 2 enquiry replies, quotes, reminders) call `draft()` then `queue()`,
    so consent, limits and quiet hours apply to them without extra code (F12.5 check 2).
29. **A message whose fate is unknown is never sent again by itself.** If the connection drops while
    sending, it ends as failed with "check with the customer"; only a person presses "Send it again".
30. **Send anyway skips only timing** (quiet hours, limits, rest days), is recorded with a reason, and
    never passes a block (consent, a stop, an erased or merged contact).
31. **Consent records are append-only** (the app role may only re-point them on a merge and blank their
    note on an erasure); the latest record per channel, or for "all", wins. Text written with the AI
    always carries its marker in the message's language (R7), checked before sending.
32. **Native-speaker review samples only text the Writing Assistant wrote**; an agent's AI-written
    message waits for the review, a person's own AI draft does not (the person has read it).
33. **Nothing Forge sends about an enquiry may promise a price, a discount, a delivery date or that
    we can make or have something** (N7 A). `app/enquiries/guard.py` checks every acknowledgement,
    nudge and decline before it is queued, and a hit blocks it with a record; nobody can send it
    anyway. `test_no_message_about_an_enquiry_may_promise_a_price` checks it. M3's quote cover note
    will use the same guard for anything beyond the quote itself.
34. **An enquiry is never silently dropped.** An email that cannot be read, or is read with low
    confidence, still becomes an enquiry marked "needs review", or waits in Other mail with its reason.
35. **Stored files keep their row.** Retention and erasure remove the bytes through
    `app/files/store.remove()`, which records when and why; files are only ever downloaded, never
    opened inside a page. Erasing a contact also blanks their enquiry emails and files
    (`test_erasing_a_contact_blanks_their_enquiry_emails_and_files`).
36. **Timers go through `app/sla/engine.py`**, on the "always" clock (the 15-minute first response) or
    the company's working calendar; escalation only tells people (as rule 24).

## 6. Current state (end of 2026-10-12)

Phase 1 (Steps 0 to 6) is built; its open checks are listed per step below and in section 7. Phase 2's
first module, **M1 Enquiry, is built for email (2026-10-12)**, and the **M3 CPQ questions** (C1-C18) are
waiting for Nischay's answers. Tests: 264 backend (plus 3 that run only against a live ERPNext) and 31
frontend, all passing; every new screen was checked in a browser, on a desktop and at phone width (390 px).
Nischay's PC needs `uv sync` and `uv run alembic upgrade head` (migrations 0010-0012) after the copy.

### Phase 2, M1 Enquiry: built for email
Built to N1-N15, P1-P3 and "Decline only" (section 3) in commits 613bdec (the email path and screens),
c8a727f (test set), dc45bc9 (document reader), ef660dc (can we make this, scoring). Menu **Enquiries**:
- **Queue** (Design File frame 02): every open enquiry with its priority (0-100, coloured by grade), the
  "can we make it" stamp, owner, source, the running timer (green, amber, red) and status; views (Open,
  Mine, No owner, To check, Possible duplicates, Engineering, Waiting on customer, Parked, Closed, All) and sorting by priority (default),
  newest or oldest. The menu shows how many have no owner.
- **One enquiry**: the customer (matched to an account and contact, or new), the requirement lines with
  unsure fields highlighted (click to correct; corrections teach the reader), the email thread and
  attachments (download only), the questions asked and which the customer answered, the timers, the
  owner with the rule that chose them (reassign with a reason), "can we make it" per line with reasons
  and "Check again", the priority with its top three reasons and "How it was worked out", the
  engineering review when there is one (Take it, We can / can't make it), the acknowledgement (sent, or
  a draft to edit and send), and the history. Buttons: Acknowledge, Quote sent (until M3 sends quotes), Give to someone else, Not an
  enquiry, Close, Reopen, and Same enquiry / Different enquiries on a possible duplicate (with undo).
- **+ Manual enquiry**: for a phone call or a visit; it is routed and timed like an email.
- **Other mail**: payments, complaints, order status, vendors, spam, with label and confidence; one click
  makes one an enquiry, a changed label is recorded; complaints tell the chosen role. Emails that could
  not be read wait here too, with the reason.
- **Test mailbox**: send Forge an email as a customer (with attachments) without a real mailbox.
- **Setup**: Mailbox (IMAP now; Gmail and Microsoft 365 once the app registrations exist), Owners
  (routing order, people, regions, limits, reports to), Timers and new mail, Product families (with
  templates, how each is supplied, minimum and best order size, the details needed to quote), What we
  make (processes and limits, materials, certificates, industries, engineering role; sample button),
  Scoring (weights).
- **Account page**: the account's owner. **Decisions**: decline drafts and drafted acknowledgements wait
  here as cards that show the exact email.

Engineering defaults Claude chose inside N1-N15 and P1-P3 (each can be changed under Setup, or by asking):
- Routing: at most 25 open enquiries per person (a person's own limit overrides it); if no step finds
  anyone, a fallback person, else the escalation role or admins are told.
- Timers as N13 A, plus the engineering review: 1 working day. Owner reminded at 75%, manager at 100%,
  head of department at 200%. "Waiting on customer" pauses the quote timer.
- Acknowledgement: asks only the missing details from the family's checklist; nudges on day 2 and day 5,
  parked after day 7; a reply ticks off what it answers and restarts the quote timer. Fields read below
  70% confidence count as unsure.
- Reading: the latest 5 corrections go to the reader as examples; a model that cannot be reached is
  tried 3 times, then the rules read the email. The rules read the signature after the sign-off
  ("Regards", "धन्यवाद", "ధన్యవాదాలు" …).
- "Can we make this?": the rules never decline on a limit. Beyond a process limit, an unknown grade or a
  missing certificate means "needs engineering"; only an excluded industry or a family the company does
  not make is "no fit". A line below the minimum order is only noted and lowers the score. A line that matches
  no family is "not checked". With an AI key the model may make a line more demanding, never less, and
  may call a line the rules could not place "no fit". With no setup at all, enquiries are "Not checked".
- Scoring v1 weights: Fit = can we make it 50, order size 25, industry 25; Intent = formality 40,
  timeline 30, seniority 30; Risk = overdue money in the ERP 50, export or watched country 20, new or
  unknown customer 30. Risk lowers the priority by up to 50%. A change of 20 points tells the owner.
  Everything is scored again on each new signal and every hour.
- No fit, non-strategic: only the decline draft (Nischay's "Decline only"). Strategic: never declined;
  the owner and manager are told and the acknowledgement stays a draft.

M1 Done-When (requirements Section 3). Under N6 A, no M1 feature is marked done until it is measured
again on Nischay's real samples and tried on his PC; until then the checks below are proved on Claude's
test set and in tests:
- **F1.1** under 60 s (slowest 2.9 s) and nothing lost: met on the test set. **≥95% classification: 73%
  with the rules alone**, because without the AI every email is kept as an enquiry for a person to check
  (the rules' own label is right 98% of the time and is shown to that person). Needs D3, then the real
  emails. Replies in the same thread join the same enquiry; low-confidence reading still creates the
  enquiry marked "needs review": both met in tests.
- **F1.4** 40-line Excel BOM: 40 of 40 lines, 100% of fields on the test set. A scanned PDF spec needs the
  vision model (D3); unreadable parts are flagged, never guessed (met with a stand-in model).
- **F1.5** sure duplicates merge, look-alikes are flagged and a person's answer is remembered: met. "Email
  and WhatsApp within a day = one enquiry" waits for WhatsApp (F1.2).
- **F1.6** no fit with the right reason, and strategic accounts never declined: both met in tests.
- **F1.7** reasons in words and the Enquiry Queue sorted by priority: met (the only worklist so far).
- **F1.8** on the test set: first response within seconds, 0 of 140 acknowledgements promised anything,
  the right language for 99.3% (one mixed Telugu-English email was answered in English). **Answers
  updating the lines**: met in tests and in the browser (f66dcfe). A reply's answers go into the lines
  and the enquiry without re-typing (`enquiries/answers.py`): a quantity, size, grade or standard into
  the line it names (or the only line, or the only line still missing it), the delivery place and date
  into the enquiry. Only empty fields are filled; a different value, or an answer that does not say
  which line, is flagged for a person ("check what was read"). Each question keeps the customer's words,
  and the history says what was filled in. Without the AI the rules do the same and add a new line only
  when it matches a product family.
- **F1.9** an owner within a minute with the rule that fired: met. The dealer step waits (Q12, Phase 5).
- **F1.10** the ladder (owner, manager, head of department) and the pause: met in tests, including the
  quote and first-look timers across a night, a Sunday and a company holiday (f66dcfe).
- **F1.2** (WhatsApp) and **F1.3** (web form, marketplaces) are on the later list (N2 A, N14).

Gaps: Gmail and Microsoft 365 need CognitBotz's app registrations (N4 A) and were tested against
stand-ins only; Gmail's instant notification needs a Google Pub/Sub topic; files are stored in a local
folder (S3-compatible storage comes with deployment); one sales mailbox per company; the synthetic emails
are cleaner than real mail (N6 A asks for about 100 real ones); account owners are set by hand.

### Step 6 (F12.5 Multilingual & Channel Fabric): email built, two things to wait for
Built to L1-L11 (section 3): email only, over SMTP, with WhatsApp and SMS on the later list. What a
person sees (menu **Messages**):
- **Messages**: every message to and from customers, newest first, with filters (Waiting, Sent,
  Problems, Received, Drafts) and a switch to the notifications sent to staff. A held message says why
  and until when ("Already had today's promotional message: goes at 09:00 on Mon 12 Oct").
- **One message**: what the customer sees (text, the AI marker, the unsubscribe line on promotional
  email), the six checks with a tick or a cross and the reason, "What happened" (every status with who or
  what caused it), the conversation, language reviews, and the buttons its status allows: Reply,
  Send it now anyway (with a reason), Send it again (failed only), Cancel it. Links to the audit trace.
- **New message**: pick a contact (or reply to a customer's message), the kind (about their business,
  promotional, reply), the language, subject and text. While typing, Forge says which language it reads
  and what breaks the glossary or the language ("It is written in English, not Telugu"). Dictate (needs
  the Groq key) and **Draft it for me** (the Writing Assistant writes it natively in English, Hindi or
  Telugu from a few lines; needs the Gemini key) give a draft the person reads, changes and sends.
- **Test inbox**: what Forge "sent" while email is in test mode, and the customer's side: reply (a reply
  of just STOP stops promotional messages everywhere and is confirmed), make it bounce, press unsubscribe.
  Test addresses: `bounce@simulator.test`, `outage@simulator.test` (retried 1, 5, 15, 60 min, then
  failed), `lost@simulator.test` (connection drops: failed, never resent by itself).
- **Language review**: AI-written messages for a native speaker (permission `languages.review`): score
  1-5, comment, correct it (the original is kept) or stop it. A report per language counts reviews
  against the 10 the Done-When needs.
- **Languages**: the eleven recognised languages, tone guides for English, Hindi and Telugu (tone,
  greeting, sign-off, words to avoid, notes), and the glossary (keep as it is, always translate as, never
  use).
- **Settings**: Test inbox or live, the email account (encrypted password, check, pause), promotional
  limits, quiet hours, rest days and holidays, the reply window, how many AI messages are reviewed, and
  whether approval cards are also emailed.
- **Account page**: each contact has **Write** and **Consent** (agreed / stopped per channel, history,
  record what the customer told you); the Messages card lists the account's messages.
- **Agents → Sandbox Agent → Try an email**: the agent emails a contact through every check, Test inbox only.

Engineering defaults Claude chose inside L1-L11 (each can be changed on Settings, or by asking):
- Every AI-written message is reviewed until 10 per language have been, then 1 in 20; a message whose
  checks still find a problem after one rewrite is always reviewed. An agent's message waits for the
  review; a person's own AI draft goes and is reviewed afterwards. Fixed test text is not sampled.
- Email is in the Test inbox until an admin switches it live, which needs an account that passed its check.
- Promotional email is opt-out (it may go until the customer asks to stop); L9 A asks for opt-in only on
  WhatsApp. Messages about their business with us always go, but wait for the end of quiet hours.
- A reply goes at once, even in quiet hours, within 24 hours of the customer's message (the reply window).
- The daily limit counts calendar days in the company's time zone; the weekly one any 7 days.
- Two AI markers: "Sent by our AI assistant" on an agent's message, "Written with help from our AI
  assistant" on a person's AI draft (English, Hindi and Telugu texts).
- Approval cards also go by email to the people who decide them (on by default); the email links to
  the card and cannot approve anything.
- Until Forge reads the company inbox (F1.1, Phase 2), the unsubscribe link is how a customer stops
  promotional email, and bounces that arrive later by email are not seen; bounces the mail server
  refuses at once are. **Since M1 (2026-10-12)**, a connected sales mailbox brings customer replies in
  through the same path (`send.receive()`, so a STOP reply works) and late bounces become receipts.
- An erasure keeps consent rows (what was agreed or stopped, and when) but blanks their notes, and
  clears message addresses.

F12.5 Done-When (section 2 of the requirements), email half:
- **Check 2, limits enforced for every sender: met for email.** One sending path
  (`app/channels/send.py`), a test that fails if any code sends another way, and tests for consent,
  quiet hours, rest days, holidays, daily and weekly limits, the reply window and send anyway.
- **Check 1, the same message correctly toned in EN / HI / TE: waiting.** The Writing Assistant is built
  and tested with a stand-in model; it needs the Gemini key (D3) to write for real, and then native
  speakers review 10 messages per language (L11 A). The Hindi and Telugu fixed texts (AI markers,
  unsubscribe line, STOP confirmation) also need a native speaker's check.
- The WhatsApp half waits for WhatsApp (later list, section 7).

Gaps: replies and late bounces arrive only once a sales mailbox is connected (M1, section 4); one email
account per company; no open or read tracking for email (L2 A); a
message held for its time is checked again when the time comes, so changed limits apply then.

### Step 5 (F12.3 Human Approval Inbox): built, waiting for the usability run
Built to H1-H9 and Nischay's eight Step 5 safeguards (section 3). What a person sees:
- **Decisions → Inbox**: cards they can decide first, then the most urgent. Each card shows its
  deadline ("Due in 2 h 10 min", amber under an hour, red once past), the chain ("● Sales Manager →
  ○ Finance"), the facts, why it is asking, the customer's history (tier, earlier cards this year) and,
  for ERP-linked accounts, outstanding, overdue and invoiced figures with when they were read. Figures
  older than 15 minutes say so in amber and are not presented as current (safeguard 5). Then the
  agent's recommendation (approve, reject or counter, with its reason) and the buttons: Approve,
  Counter…, Reject, Hand to…. Banners say when you stand in for someone, when a card past its deadline
  was escalated to you, and when you handed a card on. "For you" / "Everyone's" switch; `?card=<id>`
  opens one card (push notifications link there).
- **Chains (H1)**: the policy rule editor has "Who decides, in order" (up to 5 roles) and a deadline in
  working hours. A card moves to the next step only after approval; a rejection ends it; the action
  runs after the last approval. A different person decides each step, and the person who asked never
  decides (safeguard 1). When several rules match, every role they name must approve (rule 27).
- **Counter (H2)**: change the allowed values (the amount, for payments); the changed version goes
  through the gate again, may need more approvals, and the original card ends as "countered" with both
  values in the audit trail. The counter is also the counterer's approval of the changed card's first
  step they may decide, and is counted once in the numbers. The changed card carries the agent's
  reasoning but no recommended option of its own.
- **Away and hand-overs (H3)**: hand one card to a colleague, or set an out-of-office with a stand-in,
  dates (at most 90 days), card types and, for cards with an amount, a maximum. The stand-in decides
  "for" you, only cards you could decide yourself; four-eyes still holds. End it at any time.
- **Deadlines (H4)**: the working calendar (time zone, days, hours, holidays), default deadline,
  reminder point, who hears about missed deadlines, bottleneck limits. Changing them needs the new
  permission `decisions.manage` and is audited; running deadlines keep theirs. The worker's minute job
  reminds, marks past-deadline steps and tells the next role up; it never decides (safeguard 4).
- **The card as shown (H7)**: each decision carries the fingerprint of the card the person saw; if
  anything decision-relevant changed (figures, history, steps), it is refused with "This card changed
  while you were looking" and reloaded. Deciding a card reloads the other waiting cards for the same
  account, because their history just changed. The content and fingerprint go into the audit evidence;
  the resulting action and audit record numbers are kept with the decision (safeguard 6).
- **Push and phone (H5)**: the inbox fits a phone (checked at 390 px); "Turn on alerts on this
  device" subscribes the browser (needs the VAPID keys, section 4). New cards, next steps, reminders,
  past deadlines and hand-overs are notified to the people who may decide (and their stand-ins).
  Forge can be added to a phone's home screen (manifest and icon).
- **Approval numbers (H9)**, for `decisions.manage`: per card type and per approver, decisions, median
  wait (working hours), median decision time, overrides (only where the agent recorded a
  recommendation, safeguard 8), missed deadlines, cards decided as a stand-in, and what waits now per
  role. A bottleneck alert card appears when a role has more than 5 cards past deadline or a card
  type's median wait doubles week on week, and clears itself. The inbox header shows your own median
  decision time and override rate.
- **Automated check (H8, safeguard 7)**: a browser script decided all 11 prepared cards as two people
  (desk and phone): stale ERP figures refusing a decision and reloading, both counters, the two-step
  chain, a hand-over decided by the stand-in on a phone, the participant's own card refused. Median
  decision time 3 s, no page errors. **The Done-When stays pending** until real people do
  `docs/USABILITY_TEST.md` (`cli seed-usability` makes a fresh company with the 11 cards per person).
- Card timing (`first_shown_at`, `decision_ms`) and "What happened next" from earlier today are part of
  it; done cards now also list who decided each step, for whom, and how long they took.
- Migration 0008. Tests: `tests/test_approvals.py` (12), `tests/test_inbox.py`, and the updated agent,
  audit and integration tests; frontend `features/decisions/labels.test.ts`, `push.test.ts`.

### Step 4 (F12.4 Audit replay, redaction, retention)
Built to R1-R8 (all A) and Nischay's seven Step 4 safeguards (section 3).

**F12.4 acceptance checks, both pass (Claude's environment, 2026-10-09):**
1. *Any customer-facing message reconstructable to full provenance in under 5 minutes.* From the
   message on an account page, one click on "Trace this message" opens everything behind it: the message
   with its "Sent by our AI assistant" marker, which agent and version wrote it, what it was given, model
   calls with prompt and answer, every gate check, the person's decision, the takeover trail of the
   account and contact, and whether every step still matches its fingerprint. Browser check: 0.2 s.
   Proved with a Sandbox test message (never sent); re-checked with real email and WhatsApp in Step 6 (R7).
2. *Tamper attempts detectable in chain validation.* Tests change a record, change, forge or delete its
   evidence, delete a record, delete or forge a seal in the middle (or put seals out of order), re-hash
   the chain after a change (caught by the seal), and cut records off the end; "Check the chain" names
   each one (`tests/test_audit_trail.py`, `tests/test_agents.py`).

What a person sees:
- **Agents > Runs / replay**: as before, plus what the agent was given (each record or passage, its
  source and when it was read), model calls with the exact prompt and answer, the agent version, and a
  note when details were removed by retention or erasure. Personal data is hidden; "Show personal
  data…" needs the permission "See personal data in the audit trail" and a reason, and is recorded.
  Name parts are hidden too ("Meera" in a note about Meera Sharma), but company names stay readable.
- **Agents > Audit log**: "Check the chain" now also checks the seals (signed, linked, in order),
  the evidence fingerprints, and says when seals are off, a seal key is missing, sealing has stopped
  for over 2 hours, records were trimmed by retention, or details were removed.
- **Agents > [agent] > Evidence tab**: per month and version, proposed / acted alone / asked / approved
  / rejected / undone / blocked / failed and the override rate; each number opens the runs behind it.
  The numbers never change a level (safeguard 7).
- **Agents > Retention**: periods per category with the law each follows (editable by `audit.manage`;
  never under 8 years for the skeleton or 12 months for anything else), a banner until someone records
  who confirmed them (a CA or lawyer), active legal holds, and "Run clean-up now".
- **Account page**: Messages card (with "Draft a test message" for the Sandbox Agent and "Trace this
  message"), Legal hold card, and "Erase…" on each contact (`privacy.erase`).
- **Erasure**: asking creates a card for the Privacy agent (L0, never higher). Only another person with
  `privacy.erase` can approve; the asker sees "A second person must decide this: you asked for it". It
  scrubs the contact (a stub "Erased contact" stays), contacts merged into it, field history, match
  candidates, merge snapshots, import rows, decision cards, outbox events, takeovers, ERP sync history
  and ERP mirror data, messages, and audit evidence, except customer-facing and money evidence (kept by
  law until its period ends) and anything under a legal hold. The ERP sync then masks the person's email
  and phone if they come back from the ERP (keyed fingerprints, no plain values kept). The record of the
  erasure holds ids and counts only. There are no embeddings of contacts yet; when added, they go in
  `privacy/erasure.py` (rule 21).

**Known gaps (Step 4)**
- Records written before Step 4 (format 1) keep their details inline, unencrypted, and cannot be erased
  without breaking the chain. Only dev databases have them.
- Deleting the newest seals cannot be detected until R3 B (outside copy); the chain check warns when the
  last seal is over 2 hours old.
- The "feeds the eval harness" part of F12.4 waits for an eval harness (not in Phase 1 yet).
- Hiding GSTINs built on an individual's PAN was Claude's choice; Nischay can change it. (The retention
  minimums were confirmed by Nischay, section 3.)
- Personal data in free text is found by known names on the same page and by patterns (email, phone,
  PAN, Aadhaar); a name the page does not know about (say, a third person mentioned in a note) is not
  hidden.

### Step 3 (F12.6 Integration Framework + ERPNext)
What a person sees (menu **Integrations**, visible with `integrations.read`):
- **Connect an ERP** (wizard): name, ERPNext address, API key and secret, **Test connection** (logs
  in, reads the version, checks every record type can be read, and on failure says what to fix), pick
  record types, **Save**. The first full read starts at once in the worker.
- **Connection page**: health, each record type's state (up to date / reading / failing with the
  cause), last read, record count, last nightly comparison; recent runs; **records that failed** with
  what the ERP sent, Retry and Skip; settings, **change key**, optional webhook. Buttons: check login,
  read changes now, compare with ERP, pause.
- **Red banner** on every Integrations page and a red dot on the menu while a connection has a problem;
  the same problem is an **alert card** in Decisions > Waiting for you (red, "I'm on it"), which closes
  on its own when the connection works again. Records that keep failing and customers to match are
  **task cards** that close on their own (E7).
- **Customers** (mapping studio): every ERP customer and its link. Forge links on its own only when a
  verified ID (GSTIN, PAN or CIN) matches exactly one account with no GSTIN contradiction; any other
  possible match waits here with the candidates (link, link to another account, new account, ignore,
  unlink later). A customer with no possible match becomes a new account marked "from ERPNext"; a
  disabled one does not (E4, safeguard 2).
- **Code lists** (mapping studio): each ERP unit, customer group, territory, payment term, warehouse and
  tax template mapped to Forge's list (W1). Suggestions are rule-based (same code, known alias, same GST
  rate, similar name) until the D3 keys arrive; select and **confirm in bulk**; every change is a new
  version with its history (V3).
- **Account page > From the ERP**: the ERP customer codes (several per account allowed), credit limit
  and payment terms, Orders, Invoices, Outstanding (owed and overdue) and Payments, each "as of" the
  last read (E2). **Create in ERPNext** for an account that is not a customer there yet (E3).
- **Outside AI tools**: tokens for the MCP server (`/mcp/`): find accounts, an account's ERP orders and
  outstanding invoices, and (only with a write token) ask to create an ERP customer, which a person
  approves (E9). Tokens are shown once and can be stopped.

How it works underneath:
- Tables (migration 0005): `erp_connections` (logins AES-256-GCM encrypted, bound to tenant, connection
  and field), `sync_flows` (one per record type, with its saved place), `sync_runs`, `sync_dead_letters`,
  one table per contract (`erp_customers` ... `erp_stock`), `erp_writes` (idempotency keys),
  `code_maps` + `code_map_history`, `forge_codes`, `mcp_clients`. Decisions gained the kinds `alert`
  and `task`.
- Schedule (E6), from the worker's beat every 60 s: check each login; start reads due (every 5 minutes,
  or at once when asked, by a webhook, or when a fixed login makes failed reads worth re-running); one
  full comparison per record type per night after 02:00 IST, which repairs differences and marks records
  the ERP no longer has as gone (never deleted).
- Alerts (safeguard 5): a rejected key or missing permission alerts at once; unreachable / timeouts
  after 3 minutes; a record type failing 3 times in a row; no successful read for 15 minutes (lag); a
  run stuck for 30 minutes. One alert card per connection names every current problem.
- ERP-owned fields (E5, safeguard 3): legal name, GSTIN, PAN and billing address come from the ERP. An
  empty field is filled; a different GSTIN or PAN, or a value that another ERP connection supplied,
  becomes an approval card showing both values and their source instead of overwriting.
- Create customer (safeguard 1): permission `erp.write`; refused when the account is merged, not a legal
  entity, already linked in that ERP, or when a synced customer in Forge or the ERP has the same name,
  GSTIN or PAN; goes through the gate (ERP Connector, L2 by default, so it is sent at once for a person
  with `erp.write`; outside AI tools are L0, so a person approves); the same request is never sent twice,
  and after a lost answer Forge finds the customer by its "Forge ref" before trying again.
- Audit: connection, login, flow, sync request, retry/skip, alert, task, link/unlink/ignore/new account,
  every ERP write step, every mapping change, MCP token changes.

**F12.6 "Done When" checks** (checked in Claude's environment with a browser, ERPNext 16.50.0 with
demo data):
- "Reference ERP connected to full sync (customers, items, orders, invoices) in under one day by a
  non-developer": the wizard takes five fields and two clicks; the full read of all seven record types
  finished **51 seconds** after saving. **Met in Claude's run; Nischay's own run in Docker Desktop
  (`infra/erpnext/README.md`) is the real check.**
- "Broken sync (auth revoked) alerts with specific cause within 5 minutes; no silent failure paths":
  regenerating the ERPNext key raised "ERPNext rejected Forge's API key" with what to do in **36 s and
  41 s** (two runs); entering the new key cleared it within seconds. **Met.**

Known gaps (none block Step 4):
- Code suggestions are rules only; AI-assisted suggestions wait for the D3 keys (V3).
- "Create in ERPNext" sends the name, type (company) and GSTIN, not the address yet.
- Approving a "create customer" card calls ERPNext inside the web request (up to 20 s, then it is
  marked "outcome unknown" and looked up before any retry). Move it to the worker if ERPs get slow.
- Webhooks only make Forge read sooner; their content is never trusted.
- In the demo data, numbered customers ("Tie Test Customer 02" ... "12") all went to review against the
  account made from "Tie Test Customer 01": the matcher treats near-identical names as possible
  duplicates (S3A), so such lists need one review pass.
- The frontend bundle is now 605 kB (Vite warns above 500 kB); split it by route soon.
- No GraphQL gateway (E9: later). SAP, Dynamics, NetSuite and Tally adapters are not started.

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

Known gaps from Steps 1 and 2 (none block Step 4):
- F12.2 "Supervision" is only partly built: per-tenant LLM budgets and model routing exist (Step 0), but
  golden-set evals per agent per release and drift monitors wait until there are model-driven agents
  (Phase 2). Per-step tracing is the audit chain plus `agent_runs`; there is no OpenTelemetry yet.
- Approving a decision runs the action inside the web request. Fine for merges and test payments;
  slow actions (ERP pushes) must be handed to the worker when they arrive. Sending messages already is
  (Step 6: the request only queues; the worker sends).
- A stop takes effect at the next action; it does not interrupt an action already running.
- A pair whose merge was blocked or stopped stays in the review queue; it is tried again only when a
  record changes or someone presses "Check all records again".
- Approval inbox usability finding (Nischay, 2026-10-10), **fixed 2026-10-12** (e0f86d1): on **Counter…**,
  "Approve with this change" stayed grey while the amount equalled what was asked, with nothing saying
  why; the form now says "Change a value above to approve a different one."
- Approval inbox (Step 5) gaps: approval cards now also go by email with a link (Step 6); decision
  buttons in WhatsApp come with WhatsApp (later list); push on a
  phone needs Forge at an `https://` address; the inbox list does not refresh by itself (the menu
  count does, every 30 s), so a card changed by someone else is caught by the fingerprint check
  when deciding, not shown live; the calendar has one time zone per company.
- Meaning match is built and tested with a stand-in model, but is **off until a Gemini key is set (D3)**.
  The vector column has no fixed size, so there is no ANN index yet; fine for thousands of accounts.
  The duplicate check on the create form uses the rules only, not meaning match.
- No registry lookups (MCA, GST portal): S1A is offline checks only.
- No contact detail/edit screen (the API supports it); contacts are added from Contacts or an account.
- Model prices (`PRICES_PER_MTOK`) are empty and model names need confirming against the current
  Groq/Gemini catalogues; observability beyond `llm_calls` is not started.

## 7. Next step and open questions

**Now: Nischay answers the M3 CPQ questions.** C1-C18 are in `plans/Phase2_M3_CPQ_Questions.md` in the
Claude project files, each with a recommended A (answer like "C1 A, C2 A, ..."), plus one question:
does his first customer keep prices in ERPNext price lists or in Excel? Nothing for M3 is built until he
answers. Then the first things built are the ones every answer needs: the Quote record with its
revisions (unchangeable once made), the quote events (M2 needs `quote.sent`) and the Quote Builder
screen (Design File frame 04).

**M1 on his PC:** after the copy, `uv sync` and `uv run alembic upgrade head` (0010-0012), restart the
API, worker and beat, then Enquiries → Setup (templates, the sample button under What we make) and
Enquiries → Test mailbox (section 4).

**For M1 to be marked done (N6 A):** the D3 keys (the ≥95% classification and reading scans need the
AI), about 100 real anonymised enquiry emails and a few real BOMs and drawings to measure again, and a
real test mailbox on his PC. Gmail and Microsoft 365 sign-in need CognitBotz's two app registrations
(N4 A; IMAP works without them). The two gaps found while writing the 12 Oct notes (answers into
existing lines, F1.8; an enquiry timer across a holiday, F1.10) were fixed the same evening (f66dcfe).

**Still open from Phase 1**, alongside:
- The Step 5 usability run (`docs/USABILITY_TEST.md` and the guide linked in section 4): two or three
  people, 15 minutes each, a fresh test company each (`uv run python -m app.cli seed-usability --slug
  ut-<name>`), results in the table at the end of the guide. F12.3 is done when the overall median is
  under 60 seconds and nobody got stuck. The first session (`ut-priya`) ran on 2026-10-10 on his phone
  too; more testers are still needed. Its one finding so far (the counter hint) is fixed.
- Step 6: the Gemini key (D3), then native speakers review 10 AI-written messages each in English, Hindi
  and Telugu on Messages → Language review (give them a role with `languages.review`).
- His own check of Step 3 in Docker Desktop with `infra/erpnext/README.md` (connect, read, revoke the
  key, see the alert).
- The F12.1 re-measure on his real customer export (S5 A).

**Later list (agreed, not built yet):**
- **WhatsApp enquiries (F1.2)** come with WhatsApp (N2 A); M1 stays open for them.
- **Web form and marketplaces (F1.3)**: the website form, IndiaMART, TradeIndia, Alibaba (N14: email
  first, other platforms later).
- **Lead scoring v2** (learned from won and lost deals), after M2 and M3 (N15 A).
- **The dealer-territory routing step** (F1.9, Q12), with the dealer modules in Phase 5.
- **WhatsApp** (L3, Nischay 2026-10-10: not now). When it comes: Meta's WhatsApp Cloud API was the
  recommendation (templates, buttons, delivery and read receipts, incoming messages by webhook); it needs a
  Meta Business account, a WhatsApp Business phone number and approved message templates. It plugs into the
  same sending path, consent (WhatsApp's own opt-in rule) and caps built in Step 6, and brings the decision
  buttons for the approval inbox (H5) and the WhatsApp half of F12.5's Done-When.
- **SMS** (L4, same as WhatsApp). Needs the company's DLT registration with TRAI (company, sender ID and every
  template) before any SMS goes out; MSG91 was the recommendation. TRAI also forbids promotional SMS between
  21:00 and 09:00.

**Before real deployment (first paying customer)**, planned and agreed but not built yet:
- **R3 B: outside copy of the audit seals.** Copy each hourly seal (tenant, record number, hash, time, key id,
  signature: one `audit_seals` row) to a write-once store such as S3 Object Lock, alert when an upload fails,
  and let the chain check compare against that copy. Needs a cloud account and credentials. Until then the
  seal key in `backend/.env` sits on the same server as the data, so someone with full server access could
  rewrite records and re-sign the seals (Nischay, 2026-10-09).
- **Legal validation of the retention periods** (Step 4 safeguard 4): Nischay's CA or lawyer confirms the
  periods per record category and applicable law; record who confirmed it on the retention settings screen.

Open questions for Nischay:
- **M3 C1-C18** (above), and whether his first customer keeps prices in ERPNext or Excel.
- **D3** New Groq/Gemini keys for `backend/.env` (the old ones in `CRM\CRM\backend\.env` should be
  rotated, never copied). Meaning match (S2A) and AI code suggestions (V3) start working once set,
  and so do "Draft it for me" (Gemini) and dictation (Groq) from Step 6; F12.5's check 1 needs them.
- **D4** Daily handover update time: 18:50 IST set by default; change?
