"""Celery worker + beat schedule. Run with:
    celery -A app.worker.celery_app worker --beat --loglevel=info
All background work (outbox delivery now; pollers, SLA timers and cadences later)
lives here, never inside the web process."""

from celery import Celery
from celery.schedules import crontab

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
        # F12.4: seal every tenant's audit chain hourly (R3); retention runs nightly at
        # 03:00 IST (R5).
        "audit-seal": {"task": "forge.audit_seal", "schedule": crontab(minute=0)},
        "audit-retention": {"task": "forge.audit_retention",
                            "schedule": crontab(hour=21, minute=30)},
        # F12.3: approval reminders, deadlines and escalation every minute; bottleneck
        # alerts every 15 minutes (H4, H9).
        "inbox-tick": {"task": "forge.inbox_tick", "schedule": crontab()},
        # F12.5: send due messages (queued, or held until now) every 10 seconds.
        "channels-tick": {"task": "forge.channels_tick", "schedule": 10.0},
        # M1: read waiting emails and run enquiry timers every 10 seconds, check the
        # sales mailboxes every 15 seconds (each one at most once a minute unless a push
        # asked sooner), nudge and park every hour (N3 A, N13 A, F1.8).
        "enquiry-tick": {"task": "forge.enquiry_tick", "schedule": 10.0},
        "mail-tick": {"task": "forge.mail_tick", "schedule": 15.0},
        "enquiry-followups": {"task": "forge.enquiry_followups",
                              "schedule": crontab(minute=7)},
        # F3.2: open quotes against today's rates and BOMs every hour (K9, K11 A); each
        # owner's "cost changed" email once a day at 09:00 IST.
        "costs-refresh": {"task": "forge.costs_refresh", "schedule": crontab(minute=20)},
        "cost-alert-emails": {"task": "forge.cost_alert_emails",
                              "schedule": crontab(hour=3, minute=30)},
    },
)
celery_app.autodiscover_tasks(["app.worker"], related_name="tasks")
