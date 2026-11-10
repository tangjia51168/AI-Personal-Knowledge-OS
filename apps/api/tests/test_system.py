import asyncio
from uuid import uuid4

import pytest
from app.core.config import Settings, get_settings
from app.db.models import Base
from app.integrations.queue import get_task_queue
from app.integrations.storage import tenant_object_key
from app.main import app
from app.modules.system import health
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    monkeypatch.setenv("SYSTEM_API_TOKEN", "test-operator-token-not-for-production-123456")
    get_settings.cache_clear()
    yield
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_liveness_has_no_external_dependency():
    with TestClient(app) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"


def test_readiness_success(monkeypatch):
    for name in ("check_database", "check_redis", "check_storage"):
        monkeypatch.setattr(health, name, lambda: None)
    response = TestClient(app).get("/health/ready")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ready"


def test_dependency_failure_is_503_without_credentials(monkeypatch):
    def fail():
        raise RuntimeError("postgres://private:secret@internal")

    monkeypatch.setattr(health, "check_database", fail)
    monkeypatch.setattr(health, "check_redis", lambda: None)
    monkeypatch.setattr(health, "check_storage", lambda: None)
    response = TestClient(app).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["data"]["services"]["postgresql"]["status"] == "down"
    assert "secret" not in response.text


def test_probe_reports_down():
    def fail():
        raise OSError("offline")

    assert asyncio.run(health.probe("redis", fail)) == ("redis", {"status": "down"})


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v1/system/smoke-jobs"),
        ("GET", f"/api/v1/system/smoke-jobs/smoke-{uuid4()}"),
        ("GET", "/api/v1/system/metrics"),
    ],
)
def test_operator_auth_required(method, path):
    response = TestClient(app).request(method, path)
    assert response.status_code == 401


def test_unconfigured_auth_fails_closed(monkeypatch):
    monkeypatch.setenv("SYSTEM_API_TOKEN", "CHANGE_ME")
    get_settings.cache_clear()
    assert TestClient(app).post("/api/v1/system/smoke-jobs").status_code == 503


def test_smoke_submission_uses_fixed_task_name():
    class Queue:
        def send_task(self, name, task_id):
            assert name == "knowledge_os.smoke"
            assert task_id.startswith("smoke-")

    app.dependency_overrides[get_task_queue] = lambda: Queue()
    response = TestClient(app).post(
        "/api/v1/system/smoke-jobs",
        headers={"Authorization": "Bearer test-operator-token-not-for-production-123456"},
    )
    assert response.status_code == 202
    assert response.json()["data"]["status"] == "submitted"


def test_queue_failure_is_sanitized():
    class Queue:
        def send_task(self, *args, **kwargs):
            raise RuntimeError("redis://password")

    app.dependency_overrides[get_task_queue] = lambda: Queue()
    response = TestClient(app).post(
        "/api/v1/system/smoke-jobs",
        headers={"Authorization": "Bearer test-operator-token-not-for-production-123456"},
    )
    assert response.status_code == 503
    assert "password" not in response.text


def test_invalid_task_id_rejected():
    app.dependency_overrides[get_task_queue] = lambda: object()
    response = TestClient(app).get(
        "/api/v1/system/smoke-jobs/not-a-smoke-job",
        headers={"Authorization": "Bearer test-operator-token-not-for-production-123456"},
    )
    assert response.status_code == 422


def test_tenant_keys_are_distinct_and_not_user_paths():
    asset, tenant_a, tenant_b = uuid4(), uuid4(), uuid4()
    assert tenant_object_key(tenant_a, asset) != tenant_object_key(tenant_b, asset)
    assert tenant_object_key(tenant_a, asset) == f"tenants/{tenant_a}/assets/{asset}"
    with pytest.raises(ValueError):
        tenant_object_key("../../other", asset)


def test_secrets_not_in_config_repr():
    settings = Settings(_env_file=None, system_api_token="super-secret-key")
    assert "super-secret-key" not in repr(settings)


def test_tenant_owned_models_have_tenant_column():
    for name in ("processing_jobs", "media_assets", "tenant_members"):
        assert "tenant_id" in Base.metadata.tables[name].columns
    constraints = Base.metadata.tables["processing_jobs"].foreign_key_constraints
    assert any(
        {column.name for column in constraint.columns} == {"tenant_id", "requested_by"}
        for constraint in constraints
    )


def test_no_unprotected_business_routes():
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    assert not any(path.startswith("/api/v1/sources") for path in paths)
    assert paths["/api/v1/system/smoke-jobs"]["post"]["security"]
    assert paths["/api/v1/spaces/{tenant_id}/sources/inspect"]["post"]["security"]
