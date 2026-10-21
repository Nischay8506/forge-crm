# Start here: running Forge CRM on your own machine

Forge CRM is an agentic CRM for manufacturers: enquiries come in, agents read them, people
approve what matters, and quotations come out. This page gets it running on your PC in
about twenty minutes, most of which is downloads. Written for Windows, because that is
what the team uses; the same steps work on macOS and Linux with the commands noted at the
end.

You do not need to understand the code to finish this page. When something looks wrong,
jump to **If something looks wrong**: the three things that actually go wrong are there.

## 1. Install four things

| What | Where | Check it worked |
|---|---|---|
| Docker Desktop | https://www.docker.com/products/docker-desktop/ | Open it and wait until it says "Engine running" |
| Git | https://git-scm.com/download/win | `git --version` |
| Node.js 22 LTS | https://nodejs.org/ | `node --version` |
| uv (installs Python itself) | https://docs.astral.sh/uv/ (`winget install astral-sh.uv`) | `uv --version` |

You do not need to install Python. uv downloads the right version (3.12) on its own.

Open a **new** terminal after installing, or the commands above will not be found yet.
Anywhere below, "terminal" means Command Prompt or PowerShell.

## 2. Get the code

```
git clone https://github.com/Nischay8506/forge-crm.git
cd forge-crm
```

If Nischay sent you a zip instead, unzip it and `cd` into the folder. It is a full git
repository, so `git pull` works from it as well. **Set your own name on it before your
first commit**, or your commits will be signed with the name the zip was built with:

```
git config user.name "Your Name"
git config user.email "you@example.com"
```

Put it somewhere **outside OneDrive, Dropbox or Google Drive**, for example `C:\dev`. A
synced folder tries to upload the thousands of files that the Python and Node packages
add, which is slow and sometimes locks a file while you are building.

## 3. Run setup once

Docker Desktop has to be running first. Then, in the `forge-crm` folder:

```
scripts\setup.cmd
```

It takes a few minutes and tells you each thing it does: starts PostgreSQL and Redis in
Docker, writes `backend\.env` with working settings and three freshly generated keys,
installs the backend and the frontend, and creates the database tables. Safe to run again
at any time: it never changes a setting you have already filled in.

The keys it generates are yours alone and stay on your machine. `backend\.env` is ignored
by git on purpose, so never commit it and never copy someone else's.

## 4. Start it: three terminals

Each one stays open while you work. From the `forge-crm` folder:

```
1   cd backend    uv run uvicorn app.main:app --reload --port 8000
2   cd backend    uv run celery -A app.worker.celery_app worker --loglevel=info --pool=solo
3   cd frontend   npm run dev
```

- **1, the API.** Everything the screens do goes through it. `--reload` restarts it when
  you save a Python file. Its own documentation is at http://localhost:8000/docs.
- **2, the worker.** Background work: timers, due emails, hourly cost checks, audit seals.
  The screens work without it, but nothing scheduled happens. On Windows the scheduler
  cannot run inside the worker, so if you need scheduled jobs (daily emails, the hourly
  seal), open a fourth terminal with `uv run celery -A app.worker.celery_app beat
  --loglevel=info`.
- **3, the website.** Open the address it prints, normally http://localhost:5173.

## 5. Sign in

Company `forge`, password `Forge@2026`, and one of:

| Email | Who they are |
|---|---|
| admin@forge.example.com | Admin: every screen |
| tester@forge.example.com | Sales Manager: decides approval cards |
| finance@forge.example.com | Finance: second approver on large amounts |
| rep@forge.example.com | Sales: asks for approvals |

These four exist on every development machine, created when the API starts. They are for
development only and never appear on a production server. Approval chains need two people,
which is why there are four.

A good first lap: **Enquiries → + Manual enquiry**, save it, open it, then **Quotes** and
start a quote from that enquiry.

## 6. What works without extra keys, and what does not

Everything in the screens works with what setup gave you, except the parts that need an
outside service:

- **AI steps** (reading an email enquiry, deciding whether we can make it, translation,
  "Draft it for me") need `GROQ_API_KEY`, `GEMINI_API_KEY` or `ANTHROPIC_API_KEY` in
  `backend\.env`. Without them Forge skips the model and the rules still run: you add
  enquiry lines by hand, and the buttons that need a model say so. Ask Nischay for keys
  if your part needs them.
- **ERPNext** (customers, BOMs, workstations) needs a connection on the Integrations page.
  `infra/erpnext/` has a compose file for a local ERPNext if you need one.
- **Real email** stays off until an admin switches it on. Everything Forge sends lands in
  **Messages → Test inbox**, where you can act as the customer.
- **Browser alerts** on the Decisions page need two more keys (`new-push-keys`). Optional.

## 7. Before you push code

```
cd backend    uv run pytest          (about five minutes, needs Docker running)
cd backend    uv run ruff check app tests evals
cd frontend   npx tsc --noEmit -p .
cd frontend   npx vitest run
cd frontend   npm run build
```

All five have to pass. Keep each commit one change, and say in the message what a reader
would see differently. `docs/HANDOVER.md` section 5 is the code map and the rules the code
follows: read the part you are touching before you add to it.

**Where to push** (decided 2026-10-21). Push your feature to the project branch, not to
`main`. Nischay pulls it, checks it against the rest of the project and the database, and
merges. That is the one way work reaches `main`.

**Database migrations: Nischay writes every one of them.** Do not add a file to
`backend/alembic/versions` yourself. If your feature needs a new table or column, push the
feature and tell him what the schema change is; he writes the migration, and your code can
then rely on it. The reason is that two people numbering migrations from the same starting
point forks the chain, and nobody's `uv run alembic upgrade head` works afterwards —
`uv run pytest` then fails before a single test runs. `uv run alembic heads` must always
print exactly **one** line.

## 8. Getting new code later

```
git pull
cd backend    uv sync                       (new packages)
cd backend    uv run alembic upgrade head    (new database tables)
cd frontend   npm install                    (new packages)
```

Running `scripts\setup.cmd` again does all four and nothing else.

## If something looks wrong

**A screen saves nothing and the browser console shows 500.** Your `backend\.env` has no
`EVIDENCE_KEYS`, so Forge cannot write the audit trail that every save goes through. Run
`scripts\setup.cmd` again, which fills it in, and restart the API. (Forge now says this on
screen instead of a bare 500, so if the message names a setting, that is the one to fix.)

**Nothing loads at all, or every request is blocked.** Look at what address terminal 3
printed. If port 5173 was busy, Vite moves to 5174 and the API then refuses the browser,
because `CORS_ORIGINS` in `backend\.env` lists the address it trusts. Either free 5173 or
add the new address to that list and restart the API.

**`uv sync` stops with "failed to hardlink" or "os error 396".** The project is in
OneDrive, which cannot do hardlinks. Setup now tells uv to copy instead, so run
`scripts\setup.cmd` again and it will get past it. If you want it off your cloud drive,
move the whole folder to `C:\dev\forge-crm`, delete `backend\.venv`, and run setup once
more.

**The API will not start, or says it cannot connect.** Docker Desktop is not running, or
the containers are not up: `docker compose -f infra/docker-compose.yml ps` should show
`postgres` and `redis` as running. `scripts\setup.cmd` starts them.

Also worth knowing: `backend\.env` is only read when the API starts from inside the
`backend` folder, and `uv run pytest` needs Docker running because it uses a real
PostgreSQL.

## 9. Working alongside the other two streams

The work is split three ways, by stretch of the sales flow rather than by layer, so each
person owns whole features end to end:

| Stream | Modules | Owns these folders |
|---|---|---|
| A. Quote to cash | M3 CPQ, M4 order and ERP handoff, M6 dealers | `app/cpq`, `app/orders`, `app/partner`, `features/quotes`, `features/orders` |
| B. Demand and insight | M1 leftovers, M2 pipeline, M10 forecasting, M11 voice of customer | `app/enquiries`, `app/pipeline`, `app/analytics`, `features/enquiries`, `features/pipeline` |
| C. Customer and after-sales | M5 accounts, M7 field, M8 service and warranty, M9 installed base | `app/accounts`, `app/field`, `app/service`, `features/accounts`, `features/service` |

Stay inside your own folders, and **ask Nischay before changing anything in the shared
platform**: `app/core`, `app/db.py`, `app/rls.py`, `app/models`, `app/agents`,
`app/audit`, `app/approvals`, `app/channels`, `app/integrations`, `app/llm`. Those are
Module 12, every stream runs on them, and Nischay owns them. A new permission code, a new
audit skeleton key or a new router line is a small change there, so just say what you
need.

Where two streams meet (an enquiry becoming an opportunity, a quote becoming an order, an
order becoming an installed asset), the stream that **owns** the record writes the small
function and the other stream calls it, rather than reading the other's tables directly.
`app/cpq/quotes.follow_configuration` is the pattern: twenty-odd lines in the owner's
folder, called from the other side.

## Where to read next

- `docs/HANDOVER.md` — where the work stands, every decision taken so far, the code map,
  and what is next. The one document to read before building anything.
- `docs/BUILD_LOG.md` — what was built each day.
- `docs/USABILITY_TEST.md` — how the approval inbox was tested with real users.

## macOS and Linux

The same steps, with `python3 scripts/setup.py` instead of `scripts\setup.cmd`, forward
slashes in paths, and the worker as one command: `uv run celery -A app.worker.celery_app
worker --beat --loglevel=info`.
