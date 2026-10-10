# Forge CRM

Agentic CRM for manufacturers. Backend: FastAPI + PostgreSQL (row-level security) + Celery/Redis.
Frontend: React + Vite + Tailwind.

**New to this project? Read [docs/HANDOVER.md](docs/HANDOVER.md) first.** It says where the work
stands, how to run it, the rules the code follows, and what to do next.
Day-by-day history is in [docs/BUILD_LOG.md](docs/BUILD_LOG.md).

Quick commands (details in the handover):

```bash
# backend
cd backend && uv sync && uv run alembic upgrade head && uv run uvicorn app.main:app --reload
uv run pytest            # needs Postgres; see HANDOVER section 4
# worker
uv run celery -A app.worker.celery_app worker --beat --loglevel=info
# on Windows, two windows instead: worker --loglevel=info --pool=solo, and beat --loglevel=info
# frontend
cd frontend && npm install && npm run dev
```
