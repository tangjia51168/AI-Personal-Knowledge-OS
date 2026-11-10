from functools import lru_cache
from typing import Any

from celery import Celery

from app.core.config import get_settings


@lru_cache
def get_task_queue() -> Any:
    url = get_settings().redis_url.get_secret_value()
    queue = Celery(
        "knowledge_os",
        broker=url,
        backend=url,
        loader="app.integrations.queue_loader:RuntimeLoader",
    )
    queue.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        result_expires=3600,
        broker_connection_timeout=3,
        broker_connection_retry_on_startup=True,
        broker_transport_options={"socket_connect_timeout": 3, "socket_timeout": 3},
        redis_socket_connect_timeout=3,
        redis_socket_timeout=3,
        task_publish_retry=False,
        task_track_started=True,
        timezone="UTC",
        enable_utc=True,
    )
    return queue
