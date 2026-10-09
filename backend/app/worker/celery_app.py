"""Celery worker + beat schedule. Run with:
    celery -A app.worker.celery_app worker --beat --loglevel=info
All background work (outbox delivery now; pollers, SLA timers and cadences later)
lives here, never inside the web process."""

from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery("forge", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    timezone="UTC",
    beat_schedule={
        "dispatch-outbox": {"task": "forge.dispatch_outbox", "schedule": 2.0},
        # F12.6: connection checks every minute, due syncs, the nightly reconciliation.
        "integrations-tick": {"task": "forge.integrations_tick", "schedule": 60.0},
    },
)
celery_app.autodiscover_tasks(["app.worker"], related_name="tasks")
