from typing import Any

from app.core.config import get_settings
from app.core.production import validate_production
from app.integrations.queue import get_task_queue
from app.modules.jobs.execution import dispatch_pending, execute_dispatch

# This module is loaded by Worker, Beat and inspect. Fail synchronously before
# Celery can connect to a broker; signal receivers may swallow startup errors.
validate_production(get_settings())
celery_app = get_task_queue()


celery_app.conf.beat_schedule = {
    "durable-job-dispatch": {"task": "knowledge_os.dispatch", "schedule": 5.0},
    "source-schedule-dispatch": {"task": "knowledge_os.schedule_dispatch", "schedule": 30.0},
    "notification-dispatch": {"task": "knowledge_os.notification_dispatch", "schedule": 30.0},
}


@celery_app.task(
    name="knowledge_os.notification_dispatch", ignore_result=True, soft_time_limit=15, time_limit=20
)
def dispatch_notifications() -> int:
    from app.modules.learning.notifications import dispatch_due

    return dispatch_due()


@celery_app.task(
    name="knowledge_os.notifications",
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=150,
    time_limit=160,
)
def execute_notifications(preference_id: str) -> None:
    from app.modules.learning.notifications import execute_due

    execute_due(preference_id)


@celery_app.task(
    name="knowledge_os.schedule_dispatch", ignore_result=True, soft_time_limit=15, time_limit=20
)
def dispatch_source_schedules() -> int:
    from app.modules.sources.schedules import dispatch_due

    return dispatch_due()


@celery_app.task(
    name="knowledge_os.source_schedule",
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=25,
    time_limit=30,
)
def execute_source_schedule(schedule_id: str) -> None:
    from app.modules.sources.schedules import execute_due

    execute_due(schedule_id)


@celery_app.task(
    name="knowledge_os.dispatch", ignore_result=True, soft_time_limit=15, time_limit=20
)
def dispatch_jobs() -> int:
    return dispatch_pending()


@celery_app.task(
    name="knowledge_os.execute",
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=50,
    time_limit=60,
)
def execute_job(dispatch_id: str) -> None:
    execute_dispatch(dispatch_id)


@celery_app.task(name="knowledge_os.smoke", soft_time_limit=10, time_limit=15)
def smoke() -> dict[str, Any]:
    """Real queued ping; never operates on user or tenant resources."""
    return {"status": "ok", "worker": "knowledge-os", "schema_version": 1}
