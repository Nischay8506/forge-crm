# Build Log

One entry per working day, newest first. Each entry: what was done, decisions, problems, what is next.
The current overall state is in [HANDOVER.md](HANDOVER.md).

---

## 2026-10-19 (Day 12): nothing built

Nothing was built today, and nothing is in progress.

Yesterday evening Nischay ran `uv run alembic upgrade head` on his PC and it went from 0015 to 0016,
so his copy is current with F3.3 at commit 4fc8f9e. His first attempt failed with a refused
connection, because the PostgreSQL container was not running; starting Docker Desktop and the
containers (or `scripts\setup.cmd`, which does both) was all it needed.

**Waiting on Nischay**, in the order the build needs them: **Y1-Y8**, the eight details F3.3 leaves
open; **G1-G7**, the freight answers and the last piece of F3.2; and his trial of F3.1, the cost
engine and the new prices on his PC.

**Next:** freight (K1 D) if G1-G7 comes first, otherwise **F3.4, discount approvals**, which is the
next feature in M3's order (C1 B). Nothing in either depends on Y1-Y8, which are settings.

---

## 2026-10-18 (Day 11): the price and the floor (F3.3)

**Decisions (Nischay):** after two quiet days he wrote one word at 16:40Z, "go", taken as: carry on
with the build. The next feature in M3's order is F3.3, and how it works was already settled on
2026-10-14 (C2 A). The eight details that decision leaves open were written up as **Y1-Y8**
(`plans/Phase2_M3_F3.3_Price_Questions.md` in the Claude project files) and asked in the thread;
they are **still open**, so what is built follows the recommendations and each one is a setting, not
a rewrite, if he chooses differently.

**Done**
- **F3.3, the price and the floor** (`app/cpq/pricing.py`, migration 0016). A line's list price comes
  from the first source that covers it: a selling price in the ERP **for this very customer**, valid
  today; else the item's rate on the connection's **default selling price list** (a new setting on
  the ERP connection; with none set, Forge says so rather than guessing which list to use); else the
  **cost at the family's target margin**. Then the **tier discount** (strategic, key, standard), the
  **quantity break** the line reaches, and the **rep's own ask** (a discount or a price of their
  own), each off the running price. The price is rounded **up** as the policy says, so rounding can
  never push a line under its floor.
- **The floor is checked on every line and on the whole quote.** A line below it, a line whose cost
  is not known, a line with no policy to give it a floor, and a line nothing could price all go to a
  person on a Decisions card (the new **Quote agent**, `app/cpq/price_agent.py`), who approves the
  price, counters with one, or sends it back. The card carries the price's fingerprint, so an
  approval only ever covers the price it was shown. The whole quote is weighed against the floor its
  own lines come to.
- **Pricing policies** (`price_policies`): target margin, floor, tier discounts, quantity breaks and
  rounding, per family with one company-wide policy behind them. Never edited: a change is a new row
  from a date, like a material rate, so a quote made in March can still be explained with March's
  numbers. Saving one prices every open draft again, and says which quotes changed.
- **Each price is sealed in the audit chain**, as costs are since 5d8450f: `quote.priced` holds every
  line's fingerprint in the chain's plain skeleton, and `GET /quotes/{id}/price-seal` works them out
  again from the kept waterfalls, so a price edited in the database cannot pass as one Forge made.
- **On screen:** each quote line now shows its price, the margin it leaves, and "why this price" (the
  list price with its source, every discount with its amount, the margin against the floor, and the
  sync time of an ERP price); an **Ask** button for the rep's discount or price; the quote's total
  with its margin against the weighted floor, naming any lines left out of it. A new **Quotes →
  Pricing policy** screen sets the numbers, with its versions and a sample to start from.
- Prices follow costs automatically: every path that works out costs (a quote starting, a line added,
  changed or removed, a configuration arriving, a rate or BOM change, a cost approved on a card)
  prices the draft again straight after.

**Problems**
- The first version stored the price twice, at four decimals inside the waterfall and two on the
  line, so a quote's total disagreed with its lines by paise. Every price is now one number, to the
  paisa, rounded up; "no rounding" was dropped as a choice, because it only ever meant "to the paisa"
  anyway.
- Price seals came back as "not sealed" at first: a key is only kept in an audit record's plain data
  if it is in `chain.SKELETON_KEYS`, so `price_seals` had to be added there, as `cost_seals` was.

**Not done / next**
- **Y1-Y8 wait on Nischay**, and so do **G1-G7** (freight, the last piece of F3.2). F3.4, the
  discount approval matrix (C5 A), is the next feature in order after them.
- GST is deliberately not in F3.3: margins and the floor are worked out before tax (C8 A), and the
  tax lines come with the quote document, F3.5.
- The two quiet days before this: no message, no build and no teammate pushes on 17 and 18 October
  until the evening.

---

## 2026-10-17 (Day 10): nothing built, waiting on answers

Nothing was built and nothing was decided today: no message from Nischay, and no push from either
teammate (GitHub `main` is still at cae6ff9, yesterday evening's documentation commit). Waiting on
**Nischay**: the **G1-G7** freight answers, which are the last piece of F3.2 and the gate to F3.3
pricing; his own run of F3.1 and the cost engine on his PC, with the step-by-step guide linked in
[HANDOVER](HANDOVER.md) section 7; and the five choices at the end of that section (the "Check costs"
button, the merge cadence for three people, a build check on GitHub, M6's folder name, the D3 keys).
Waiting on **the two teammates**: the trivial pull request that proves the loop, then F2.1 for stream B
and F5.1 for stream C, both briefed on 2026-10-16. His PC is still at 5415e96, because the laptop has
been off the bridge since yesterday afternoon, so `git pull` in that folder is what brings it up to
date.

---

## 2026-10-16 (Day 9): the cost snapshot sealed; errors that say what is wrong; one-command setup;
the work split three ways

**Decisions (Nischay):** "go" at 05:38Z resumed the build paused the evening before. At 06:18Z, after a
friend's laptop refused to save an enquiry, he asked what had gone wrong and what other keys a teammate
must generate. At 07:05Z: "prepare a project copy which I can share with teammates and they can run in
their system, and a startup guide so that they can run the website by reading it", with "it should not
have issues which we currently facing". At 09:32Z he asked for this documentation update, ahead of the
usual 17:30 IST slot. In the afternoon he turned to working with two teammates: at 10:43Z how the three
streams integrate at the end, worried about moving a teammate's database onto his; at 12:36Z what to
tell each teammate and where they start; at 12:48Z that both teammates had run `scripts\setup.cmd` on
their own laptops, **inside OneDrive**, and Forge runs with no errors; and at 12:51Z he chose **A** on
the platform owner, which is **himself**.

**Done**
- **5d8450f, each line's worked-out cost is sealed in the audit chain** (the fifth of his extra J3
  points). Every cost breakdown now carries a fingerprint of everything that made it, and the audit
  record of the costing holds each line's fingerprint in its plain skeleton, where the chain's hashes
  and hourly signatures protect it. `GET /api/v1/quotes/{id}/seal` works the fingerprints out again from
  the kept breakdowns and answers, line by line, **sealed**, **changed**, **not sealed** or **not
  costed**; it needs `costs.view`. A review added to a cost afterwards leaves the seal standing, and the
  check never trusts the seal stored beside the cost: it recomputes it.
- **e22eb9c, errors that say what went wrong.** Every API answer now keeps the one `{"error": {...}}`
  shape the screens read, whoever raised it. A missing or malformed key setting is a 503 naming the
  setting and the command that makes a key; FastAPI's own 422 names the field ("customer phone: String
  should have at most 30 characters"); anything unexpected is a 500 with a reference that is also in the
  server's log next to the traceback. The server lists its unusable key settings as it starts, and a
  request that never reached the API says so with the API's address instead of blaming the form.
- **47e8eb3, one command to make a clone runnable, and a page that says how.** `scripts\setup.cmd`
  starts the PostgreSQL and Redis containers, writes `backend\.env` with values that match them and a
  freshly generated key for each setting that needs one, installs both sides and creates the tables. It
  can be run again any time: it fills in what is missing and never changes a setting somebody already
  filled in, so it also repairs a half-set-up machine. **START_HERE.md** in the repository root is the
  page a new teammate reads: the four tools to install, the one command, the three terminals, the four
  development logins, what works without an AI key, what to run before pushing, and the three things
  that actually go wrong.
- **3622cc8, setup works in a OneDrive folder**, where hardlinks do not. Setup tells uv to copy instead
  (`UV_LINK_MODE=copy`), and says, when the project sits inside OneDrive, that a folder such as
  `C:\dev\forge-crm` is an easier place to work.
- A shareable copy for teammates at `forge-crm-share/` in the Claude project files: `forge-crm.zip`
  (the git history, no `.env` and no keys) and `START_HERE.md` beside it.

**Done, afternoon**
- Answered the integration question: there is no database to migrate. The schema is the files in
  `backend/alembic/versions/`, every machine's data is sample data, and at merge each person drops their
  database and runs `alembic upgrade head` on the merged code. A shared development database across
  machines was argued against: it would take away the thing that makes the end integration cheap.
  Reproduced a forked migration chain in a throwaway copy to show him what it looks like
  (`alembic heads` printing two lines, `Multiple head revisions are present`) and that re-pointing
  `down_revision` and renumbering puts it back to one head.
- Wrote a brief for each teammate, posted in the thread for him to send: the day-one steps, the first
  feature, the folders they own, what not to touch, and the two rules that bite. **Stream B starts on
  F2.1** (evidence-backed deal stages: the opportunity object every other M2 feature hangs off, needing
  nothing from the other streams) and **Stream C on F5.1** (Account 360, on top of the accounts,
  contacts, site tree and ERP links that already exist). Both lead with proving the whole loop (clone,
  setup, branch, the five checks, a pull request, a merge) on one trivial change before any module work.
- **5415e96, the platform owner is named.** The work-split doc's rule about the ten Module 12 folders
  says "without Nischay" rather than "without the platform owner", the handover records the three-stream
  split and the ownership as decisions, and `START_HERE.md` gained what a second and third developer
  need: setting their own git name before their first commit (a shared zip carries the name it was built
  with), `npm run build` as the fifth check, one migration per pull request with `alembic heads` printing
  a single head before merging and what to do when it prints two, and a section on the three streams,
  their folders, and the habit at the four places two streams meet, where the owner writes the small
  function and the other side calls it.

**Problems found and fixed**
- **"Could not save this enquiry" on a friend's laptop.** Reproduced from nothing: the laptop had no
  `backend\.env` at all, so `EVIDENCE_KEYS` was empty, so every write that records customer details in
  the audit trail failed, while every read worked because the configuration's defaults happen to match
  the Docker database. Two fixes: the error shape above, so the next such machine says so; and the
  setup script, so a new machine has the file.
- **A teammate's `uv sync` stopped on the first wheel** with "The cloud operation cannot be performed on
  a file with incompatible hardlinks (os error 396)". uv fills a virtual environment by hardlinking from
  its cache, which OneDrive does not allow. Fixed in setup and named in the guide with both cures.
- The setup script first left the database password as `CHANGE_ME`, because `.env.example` writes
  `CHANGE_ME` inside the database URLs rather than as the whole value. A value *containing* `CHANGE_ME`
  now counts as unset. Checked on a fresh copy: setup from nothing, then the API, the fixed admin login,
  and a manual enquiry saved.

- **The copy to Nischay's PC broke its own git history**, briefly. The `.sync` bundle named its file of
  new objects `tmp.pack`, the same as the morning's bundle, and extracting with `tar --overwrite`
  replaced it, so the objects of that morning's commit were destroyed: his repository answered
  "Could not read 3a01a57 / Failed to traverse parents of commit 5415e96". Repaired by packing the range
  from the newest commit his machine could still read up to HEAD under a fresh name and indexing it;
  `git log` then showed all three commits with a clean tree. Every bundle from now on names its pack
  uniquely, since duplicate objects across packs are harmless and an overwritten pack is not.
- A teammate's clone showed that the shared zip carries Claude's git identity, so their commits would
  have been authored "Claude" until they set their own. Named in START_HERE section 2 now.

**Not done / next**
- **Freight** (K1 D) still waits on **G1-G7**. Then F3.3 pricing and the floor.
- Open with Nischay: whether the seal check gets a "Check costs" button on the quote cost view, or stays
  an API and audit check.
- Three of his five extra J3 points are on the later list in HANDOVER section 7, each with the condition
  that would make it worth building.
- Three process questions are open with him, all in HANDOVER section 7: the merge cadence that keeps a
  forked migration chain cheap, whether a GitHub Action runs the five checks on every push, and the
  folder name for M6, since `app/channels` is already the messaging platform.

## 2026-10-15 (Day 8): J1-J4 answered; a fix from his own test; fixed development logins

**Decisions (Nischay):** the **"Update the draft"** card: an enquiry line configured after its
quote has started updates the draft quote line. **J1-J4** at 13:07Z: J1 A (BOM quantities are taken as
used, no yield on top), J2 A (sub-assemblies with their own default BOM opened up, 5 levels), **J3 C**
(a line costed from an ERPNext BOM with no family cost model behind it has no factory overhead or
packing, so the cost is incomplete and goes to a review card) and J4 A (the daily reprice email at
09:00 IST). At 13:17Z he picked two of his five extra J3 points as "worth now" (the per-connection
"BOM costs already include..." checkboxes, and sealing the quotation cost snapshot) and left three for
later. He paused the build at 13:21Z.

Earlier in the day: no code change for the no-Docker options (10:39Z, "docker issue is solved"), and
`uv` stays the documented way to run the commands (10:28Z), since `pip install -e .` fails on this
repository's flat layout.

**Done**
- **c636e55, configuring an enquiry line after its quote started updates the draft quote.** Found on
  Nischay's own PC: he started a quote before configuring its line, so the quote kept the unconfigured
  line and its cost read "not known". The draft quote line copied from the enquiry line now takes the
  new configuration and its cost is worked out again; a sent revision never changes. Audited as
  `quote.line.configured`.
- **e7dc078 and 8703ced, fixed logins on development servers.** A server started with
  `ENVIRONMENT=development` (the default) always has company **forge** with
  `admin@forge.example.com`, and one login per role beside it (`tester@`, `finance@`, `rep@`), all on
  the password `Forge@2026`. Anything that needs two people, such as an approval chain, can be tried
  without seeding a throwaway company. Any other `ENVIRONMENT`, such as production, never gets them, and
  an existing login is left as it is, so a changed password stays changed.
- **a6c785c, J3 C**: a BOM cost with no cost model behind it is incomplete. The cost keeps every part the
  BOM does cover, says which parts are missing, and goes to a review card in Decisions, so nobody prices
  from a cost that quietly left out the overhead. A family whose model sets overhead to zero on purpose
  is unaffected.
- **876ad9f, an ERP whose BOM costs already include the overhead is not charged twice** (his extra J3
  point 2). Each ERP connection says, as two checkboxes on its page, whether its BOM costs already carry
  the factory overhead and the packing. What it carries is left out of Forge's own figure, "why this
  cost" says so, and it is no longer called missing, so such a line needs no review. Nothing changes for
  a company that leaves the boxes unticked.

**Problems found and fixed**
- `test_send_anyway_is_recorded_and_never_passes_a_block` delivered at a fixed 14 Oct 23:05 IST, but
  "send anyway" queues for the real now, so it failed once that time passed; it now delivers five
  minutes from now.
- The first version of the seal check trusted the `seal` key stored beside the cost, which an editor who
  left that key alone would have passed; it now always recomputes.
- A seal test tried to fake an older audit record and hit "permission denied for table audit_records":
  `audit_records` is append-only for `forge_app` (migration 0004). The test now asserts that guarantee
  instead of faking the data.

**Not done / next**
- Freight (K1 D) waits on G1-G7; the two "worth now" extras were built the next morning.

## 2026-10-14 (Day 7): M3 answers in; F3.1 configurator built; F3.2 cost engine built (freight to come)

**Decisions (Nischay):** C1-C18 at 07:10Z: C1 B (build in the guide's order, one feature at a time:
F3.1, F3.2, F3.3 ...), C2-C5 A, C6 "companies have their own, so a sample template for the
demonstration", C7 A "with person approval included", C8 A "dependent on the type of product", C9 B,
C10-C16 A, C17 B "we will modify it as per requirement", C18 A ("we don't have customers yet; a
prototype that can be modified later"). Cards: "Keep public" for the GitHub repo (07:13Z), "Sample data"
(07:27Z: a feature is done when its checks pass on sample data and Nischay has tried it on his PC;
checks that need real use are marked "measure with the first customer"), "Start F3.2 now" (07:42Z:
the cost engine is built while he tries F3.1; fixes from his try come first), "Answer each" (10:47Z) on
taking all the K recommendations. K1-K9 at 12:50Z: K1 D (his own freight design, section 3 of the
handover) and K2-K9 A; at 12:55Z he added K10-K13 (incomplete costs reviewed, BOM history kept and
changes flagged, canonical units, rates and cost models managed by authorized people with history and
audit) and chose a Decisions card for the K10 review (12:57Z). All recorded in section 3 of HANDOVER.md.

**Done**
- **63e34fb, F3.1 product configurator** (C4 A, migration 0013): each product family can have options
  and rules (needs, rules out, only when). The rules are checked before saving: conflicts, options that
  can never be chosen, optional attributes the rules always need, repeated rules and a variant-code
  pattern that leaves an attribute out are refused with reasons. Each save is a new version that can't
  be changed. "Configure" on an enquiry line offers only choices that still lead to a valid product, and
  each greyed-out choice says which rule rules it out. A valid product gets a variant code
  (FL-150-300-F316-RF). A new version marks configurations on open enquiries "configure again"; an
  ERPNext item change marks those using the item "reprice recommended". Sample family: Flanges.
- **fe7d159, F3.2 groundwork** (migration 0014):
  - Quotes (C12 A, C14 A): "Start a quote" on an enquiry makes Q/2026-27/0001 R1 with the enquiry's
    lines and configured products, valid 15 days. One quote per enquiry; changes are revisions (F3.7).
    Draft lines, validity and the delivery place can be changed; a sent revision never changes. Costs
    are shown with the `costs.view` permission (C17 B).
  - Material and machine-hour rates that apply from a date and are never edited (C3 A). A correction
    is a new row on the same date. Saving one lists the open quotes costed with the old rate, and
    ERPNext workstations win over Forge's own machine rates.
  - The formula checker for cost models (`app/cpq/formula.py`): exact decimal maths, Excel-like
    `if()`, `lookup(table, keys...)`, and errors in words; never Python's eval.
  - ERPNext workstations and submitted BOMs (items and operations) are read, off until a connection
    turns them on. Quotes follow the surviving account on a merge.
  - Screens: Quotes menu (list, one quote, Material rates, Machine-hour rates).
- **2adb0a2, F3.2 cost engine** (K2-K13 A, migration 0015), built in the evening after the notes
  above were first written:
  - **Units (K12 A)**, `app/cpq/units.py`: canonical units (kg, m, m², m³, nos, hour) and a checked
    conversion table (tonne, quintal, g, mm, ft, inch, litre, sq ft, minute, ...; not "ton", which
    differs between the US and the UK). A material rate can be typed in any of them;
    Forge keeps the rate as typed next to the canonical one (₹62,000 per tonne is kept and costs at
    ₹62/kg). An unknown unit or conversion makes the cost incomplete.
  - **Cost models (K6 A, K8 A)**, `app/cpq/costmodel.py`: per family, named values (e.g. the finished
    weight from a pasted table), materials with a quantity formula, operations in minutes on a
    machine, tooling (one-time or spread over the quantity, K3 A), the factory overhead % on material
    + conversion, packing as a % or per unit, the packed weight and volume, and a rate card (cost per
    kg or unit). Saving checks every configuration the family allows (593 for the sample flanges)
    and refuses the model with reasons if any fails; each save is a new version with an audit record
    (K13 A). Screen: Quotes → Cost models, with tables pasted from Excel and a "try it" preview.
  - **The calculation (K4, K6, K7 A)**, `app/cpq/costing.py`: material = bought quantity (net × 100 /
    yield) × rate, less the scrap credit; conversion = minutes / 60 × the machine-hour rate (ERPNext
    workstations first); then overhead, packing and tooling. The source order is the family's cost
    model, then the item's default ERPNext BOM (sub-assemblies opened up, loops caught), then the
    rate card, then "not known". Each line keeps its breakdown ("why this cost"): the parts, the
    rates with their dates, the BOM versions, the unit conversions.
  - **Review cards (K10 A, "manage it as card in decision")**, `app/cpq/agent.py`: the Cost Engine
    agent puts an incomplete or unknown cost on a Decisions card for people with `costs.manage`. They
    use the cost as worked out, counter with a unit cost, or send it back. A card names exactly the
    cost it reviewed; if the cost changes, the card closes and a new one asks again.
  - **Changes (K9 A, K11 A)**: a new rate, a cost model save, an ERPNext BOM change or the hourly
    check (for rates dated ahead) works out draft quotes again and marks sent quotes "reprice
    recommended" with the reason. Every BOM version Forge reads is kept (`erp_bom_versions`). The
    person saving a rate sees which quotes it changed; each quote owner gets one email a day at
    09:00 IST. Quotes list: a "Reprice recommended" view.
  - **Sample (C18 A)**: "Use the sample" on the Flanges cost model adds sample rates (marked "Sample
    rate") and a model: forging weight by size and class, turning, drilling, a ring groove and a ring
    gauge for RTJ faces, 12% overhead, 2% packing. 150 mm Class 300 F316 RF works out at ₹10,557.59.
- The F3.2 questions K1-K9 (`plans/Phase2_M3_F3.2_Cost_Questions.md`) and, after K1 D, the freight
  follow-up G1-G7 (`plans/Phase2_M3_F3.2_Freight_Questions.md`), both in the Claude project files.
- Tests: 37 new backend (301 in all, plus 3 that run only against a live ERPNext) and 11 new frontend
  (42), all passing.
  Browser checks as an admin on desktop and phone: the flanges template, a rule conflict caught, a
  line configured; two material rates (one dated ahead) with history, a machine rate, a quote started
  from an enquiry, lines changed and added, delivery place set.
  For the cost engine: the sample Flanges model started, the weight table shown, a bad paste reported,
  a quote line costed at ₹10,557.59, a review card for a line nothing covers, a rate typed per tonne
  shown per kg with yield and scrap.

**Problems found and fixed**
- The migration's backfill of sync flows for existing ERP connections inserted nothing, because row
  security applies to the table owner too; it now runs with the system flag on.
- New quotes referenced accounts without being registered for merges; `test_steward` caught it and
  quotes now follow a merge (rule 9).
- My reply to Nischay wrongly put the dealer check under "measure with the first customer"; it is
  checked with the Phase 5 dealer portal, and the message was corrected.
- The session hit its usage limit at 07:57Z and resumed at 10:22Z when Nischay wrote "continue".
- Cost engine: tiny quantities showed as "0 m³" (now shown to 4 significant figures); the agents
  screen test assumed the Sandbox agent came first (agents sort by name, so "Cost Engine" now does).
  The session hit its usage limit again at 13:35Z and resumed at 15:23Z.

**Not done / next**
- Nischay: after this copy, `uv run alembic upgrade head` (0013 to 0015) and restart the API, worker
  and beat. Try F3.1 (Enquiries → Setup → Product families → Flanges template, "Configure" on a line)
  and the cost engine (Quotes → Cost models → Flanges → "Use the sample", then "Work out costs" on a
  quote). Answer G1-G7 (freight) and J1-J4 (`plans/Phase2_M3_F3.2_Cost_Defaults_Questions.md`: yield
  on BOMs, sub-assemblies, overhead for items without a family, the email time).
- Claude: fixes from his tries first; then freight once G1-G7 are answered, and any J changes; then
  F3.3 pricing and the floor.

---

## 2026-10-13 (Day 6): nothing built; waiting on the M3 answers

Nothing was built or changed in the code today. Waiting on Nischay: his answers to the M3 CPQ questions
C1-C18 and whether his first customer keeps prices in ERPNext or Excel (nothing for M3 is built before
them); "push" in the GitHub upload thread (its bundle is refreshed to include this entry). His PC was offline from the evening of 12 Oct; f66dcfe, df01860 and this entry were
copied to it when it came back on 13 Oct.

---

## 2026-10-12 (Day 5): M1 Enquiry built for email; M3 questions sent

**Decisions (Nischay):** N1-N13 A and N15 A (06:01Z, 06:12Z); N14 "test only for emails and later will
add other platforms" (the web form and marketplaces go on the later list with WhatsApp). Documentation
is updated only after 17:30 IST, never before or while building (06:12Z). P1 A "Draft first", P2 A
"Review step", P3 A "Standard + unchecked" (08:21Z); "Decline only" (09:01Z). The code also goes to the
private GitHub repo Nischay8506/forge-crm for a teammate (08:25Z; changes 1A; pushed by a separate
thread from Claude's bundles). Work paused 09:01Z ("stop execution for now") and resumed 12:46Z.

**Done**
- **613bdec, the M1 email path** (N1 A): migration 0010 (stored files, mailboxes, inbound emails,
  enquiries and lines, history, product families, enquiry settings, SLA timers; account owner, users'
  reports-to and enquiry profile). Mailbox readers for Gmail (API), Microsoft 365 (Graph) and IMAP with
  OAuth sign-in (N3 A, N4 A), and a Test mailbox. Encrypted file store (N9 A). The reader (AI through
  the gateway, rules without a key; nothing dropped), Other mail with complaints told to a role (N5 A),
  duplicates (N11 A), routing with Away (N12 A), the SLA engine and four stage timers with the
  owner / manager / head ladder (N13 A), the acknowledgement with the missing questions, nudges on
  days 2 and 5 and parking, and the commitment guard (N7 A). Screens: Queue, one enquiry, manual
  enquiry, Other mail, Test mailbox, Setup, account owner.
- **c8a727f, the test set** (N6 A): 200 synthetic emails (English, Hindi, Telugu, mixed, Excel / CSV /
  PDF BOMs, other mail) and `evals/enquiries.py`. Rules alone: nothing lost, slowest email 2.9 s, the
  40-line BOM 40/40 lines with 100% of fields, 0 of 140 acknowledgements promising anything, the right
  language for 99.3%, and 73% exact labels against the 95% target (without the AI every email is kept
  as an enquiry for a person; the AI is needed, D3).
- **dc45bc9, the document reader** (F1.4, N10 A): unsure and unreadable fields kept empty and
  highlighted (migration 0011), the latest five corrections sent to the reader as examples.
- **ef660dc, "can we make this?" and scoring** (F1.6, F1.7, migration 0012): the What we make profile
  with starter templates and a sample company; per-line and per-enquiry stamps with reasons; the decline
  draft a person approves (P1 A); the engineering review with its own timer (P2 A); the automatic
  acknowledgement only for standard and not-yet-checked enquiries (P3 A); lead scoring v1 with Fit,
  Intent, Risk, a 0-100 priority and the top three reasons (N15 A); the Queue sorted by priority.
  Decision cards now show the drafted email and choice facts in words.
- **e0f86d1**: the Step 5 usability finding is fixed (the counter form says why "Approve with this
  change" is grey).
- The M3 CPQ questions, C1-C18, written to `plans/Phase2_M3_CPQ_Questions.md` in the Claude project
  files and sent to Nischay (12:52Z).
- Tests: 50 new backend (260 in all, plus 3 live-ERPNext ones) and 6 new frontend (31). Browser checks
  as an admin, desktop and phone: families from templates, the sample profile, scoring, two test
  emails read into enquiries, the engineering review, a no-fit enquiry declined through its decision card.
- **f66dcfe, the two M1 gaps found while writing this entry** (evening): a customer's reply now fills
  the enquiry's lines and details without re-typing (F1.8 check 2, `enquiries/answers.py`): only empty
  fields, a different value or an answer without a line is flagged for a person, the customer's words
  are kept with each question, and the history says what was filled in. A test now runs the quote and
  first-look timers across a night, a Sunday and a company holiday (F1.10). 264 backend tests; browser
  check on desktop and phone: a person adds the line and acknowledges (no AI key), the customer replies
  "Qty 40 MT, delivery at Patancheru, required by 15/11/2026", and all three land in the enquiry.

**Problems found and fixed**
- The rules took a size ("Pipe rack 12m - 5 Nos") or a grade ("SS 304 Sheet") as the quantity; the
  price check blocked Hindi and Telugu acknowledgements quoting "का रेट" / "ధర" in the subject; the
  rules read "Material IS 2062 E350…" as the writer's designation (they now read the signature after
  the sign-off). A null or absurd quantity from the model failed the whole reading.
- With no setup, every line said "matches none of our product families"; now the enquiry simply says
  "Not checked: set up what we make". An order below the size we like showed as raising the score.
- Decision cards showed raw values ("no_fit") and not the email itself.
- The rules turned an answer like "Qty 40 MT, delivery at Patancheru." into a new item line; a reply
  read by the rules now adds a line only when it matches a product family (f66dcfe).
- Writing this entry: the file-store comment named a `rotate-file-keys` command that does not exist;
  `rotate-evidence` already re-wraps the file keys, and the comment now says so. The M1 settings were
  missing from `backend/.env.example` and are added.

**Not done / next**
- Nischay: answer C1-C18 (and whether prices live in ERPNext or Excel); on his PC `uv sync`,
  `uv run alembic upgrade head` (0010-0012) and a try of Enquiries; for M1 to be marked done (N6 A) the
  D3 keys, about 100 real anonymised enquiry emails with a few real BOMs and drawings, a real test
  mailbox; CognitBotz's Google and Microsoft app registrations for Gmail and Outlook sign-in; still the
  Step 5 usability runs, the Step 3 Docker check and the D3 language reviews.
- Claude: M3 once the answers come (the two M1 gaps found while writing this are fixed, f66dcfe).

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
