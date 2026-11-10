"""Synchronous startup checks shared by Celery Worker and Beat."""

from celery.loaders.app import AppLoader

from app.db.security import validate_runtime_database


class RuntimeLoader(AppLoader):
    def on_worker_init(self) -> None:
        # Both Worker and Beat call this before opening a broker connection.
        # Ordinary app imports and inspect clients do not initialize a worker.
        validate_runtime_database()
        super().on_worker_init()
