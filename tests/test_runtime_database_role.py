"""Startup guards must fail closed without leaking a driver's error text."""

from unittest.mock import MagicMock

import pytest
from app.core.config import Settings
from app.db import security
from app.integrations.queue import get_task_queue


def test_database_connection_error_is_sanitized():
    engine = MagicMock()
    engine.connect.side_effect = RuntimeError("postgresql://private-user:private-password@host/db")
    with pytest.raises(RuntimeError, match="^Production database role check failed$") as error:
        security.check_database_role(engine)
    assert "private" not in str(error.value)
    assert error.value.__suppress_context__


def test_development_runtime_does_not_connect(monkeypatch):
    monkeypatch.setattr(security, "get_settings", lambda: Settings(_env_file=None))
    engine = MagicMock(side_effect=AssertionError("unexpected database connection"))
    monkeypatch.setattr(security, "get_engine", engine)
    security.validate_runtime_database()
    engine.assert_not_called()


def test_invalid_engine_configuration_is_sanitized(monkeypatch):
    monkeypatch.setattr(
        security, "get_settings", lambda: Settings(_env_file=None, app_env="production")
    )
    monkeypatch.setattr(
        security,
        "get_engine",
        MagicMock(side_effect=ValueError("postgresql://private-password@host:not-a-port/db")),
    )
    with pytest.raises(RuntimeError, match="^Production database role check failed$") as error:
        security.validate_runtime_database()
    assert "private" not in str(error.value)


@pytest.mark.parametrize("rejected", [False, True])
def test_startup_discards_probe_connections_before_celery_prefork(monkeypatch, rejected):
    monkeypatch.setattr(
        security, "get_settings", lambda: Settings(_env_file=None, app_env="production")
    )
    engine = MagicMock()
    monkeypatch.setattr(security, "get_engine", lambda: engine)
    check = MagicMock(side_effect=RuntimeError("private failure") if rejected else None)
    monkeypatch.setattr(security, "check_database_role", check)
    if rejected:
        with pytest.raises(RuntimeError, match="^Production database role check failed$"):
            security.validate_runtime_database()
    else:
        security.validate_runtime_database()
    check.assert_called_once_with(engine)
    engine.dispose.assert_called_once_with()


def test_api_lifespan_propagates_database_rejection(monkeypatch):
    from app import main
    from fastapi.testclient import TestClient

    def rejected():
        raise RuntimeError(security.ROLE_CHECK_FAILED)

    monkeypatch.setattr(main, "validate_runtime_database", rejected)
    with pytest.raises(RuntimeError, match=security.ROLE_CHECK_FAILED), TestClient(main.app):
        pytest.fail("The API must not finish startup with a rejected database role")


def test_celery_factory_selects_guarded_loader_without_connecting():
    queue = get_task_queue()
    assert queue.loader_cls == "app.integrations.queue_loader:RuntimeLoader"


def test_loader_propagates_database_rejection_before_work(monkeypatch):
    from app.integrations import queue_loader

    def rejected():
        raise RuntimeError(security.ROLE_CHECK_FAILED)

    monkeypatch.setattr(queue_loader, "validate_runtime_database", rejected)
    with pytest.raises(RuntimeError, match=security.ROLE_CHECK_FAILED):
        queue_loader.RuntimeLoader(app=MagicMock()).on_worker_init()
