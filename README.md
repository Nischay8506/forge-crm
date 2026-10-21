# Forge CRM

Agentic CRM for manufacturers. Backend: FastAPI + PostgreSQL (row-level security) + Celery/Redis.
Frontend: React + Vite + Tailwind.

**Getting it running on your machine? Read [START_HERE.md](START_HERE.md).** Install four tools, run
`scripts\setup.cmd`, and you have a working Forge with logins.

**Picking the project up? Read [docs/HANDOVER.md](docs/HANDOVER.md).** It says where the work stands,
how to run it, the rules the code follows, and what to do next.
Day-by-day history is in [docs/BUILD_LOG.md](docs/BUILD_LOG.md). All 69 features and the state of
every Done-When check are on the Build Map: https://claude.ai/artifact/Auf4tP4LxsDZZobJgn36Lt
`docs/USABILITY_TEST.md` is the 15-minute approval-inbox test that F12.3 still needs people for.

Quick commands (details in the handover):

```bash
# backend
cd backend && uv sync && uv run alembic upgrade head && uv run uvicorn app.main:app --reload
uv run pytest            # needs Postgres; see HANDOVER section 4
# worker
uv run celery -A app.worker.celery_app worker --beat --loglevel=info
# on Windows, two windows instead: worker --loglevel=info --pool=solo, and beat --loglevel=info
# the worker also sends email every 10 s; email starts in Messages > Test inbox (HANDOVER section 4)
# frontend
cd frontend && npm install && npm run dev
```
