import json
from pathlib import Path

import pytest

from scripts import operations_check as check


def test_run_check_sanitizes_exception_text():
    result = check.run_check(
        "secret",
        lambda: (_ for _ in ()).throw(
            RuntimeError("redis://user:password@example.invalid/private")
        ),
    )
    assert result == {"status": "failed", "error": "RuntimeError"}
    assert "password" not in json.dumps(result)


def test_container_check_rejects_missing_and_unhealthy(monkeypatch):
    monkeypatch.setattr(check, "run_docker", lambda *args, **kwargs: b"")
    result = check.run_check("containers", lambda: check.check_containers({}))
    assert result["status"] == "failed"


def test_load_acceptance_rejects_runtime_database_outside_isolated_stack(monkeypatch):
    values = {
        "DATABASE_URL": "postgresql+psycopg://knowledge_app:secret@remote.invalid:5432/db",
        "MIGRATION_DATABASE_URL": "postgresql+psycopg://knowledge_admin:secret@127.0.0.1:15432/db",
        "REDIS_URL": "redis://127.0.0.1:16379/0",
        "S3_ENDPOINT_URL": "http://127.0.0.1:19000",
        "S3_ACCESS_KEY": "access",
        "S3_SECRET_KEY": "secret",
        "S3_BUCKET": "bucket",
    }
    monkeypatch.setattr(check, "dotenv_values", lambda *args, **kwargs: values)
    with pytest.raises(RuntimeError, match="isolated loopback"):
        check.load_acceptance()


def test_load_runtime_storage_fails_closed_and_accepts_only_ready_scoped_identity(monkeypatch):
    values = {
        "S3_ENDPOINT_URL": "http://127.0.0.1:19000",
        "S3_ACCESS_KEY": "operator",
        "S3_SECRET_KEY": "operator-secret",
        "S3_BUCKET": "bucket",
    }
    runtime = {
        "S3_RUNTIME_ACCESS_KEY": "kosruntime" + "a" * 32,
        "S3_RUNTIME_SECRET_KEY": "b" * 64,
        "S3_RUNTIME_POLICY": "kos-runtime-" + "c" * 32,
        "S3_RUNTIME_STATE": "ready",
    }
    monkeypatch.setattr(check, "dotenv_values", lambda path, **kwargs: runtime)
    loaded = check.load_runtime_storage(values)
    assert loaded["S3_ACCESS_KEY"] == runtime["S3_RUNTIME_ACCESS_KEY"]
    assert loaded["S3_SECRET_KEY"] == runtime["S3_RUNTIME_SECRET_KEY"]
    for invalid in (
        {},
        {**runtime, "S3_RUNTIME_STATE": "pending"},
        {**runtime, "S3_RUNTIME_ACCESS_KEY": values["S3_ACCESS_KEY"]},
        {**runtime, "S3_RUNTIME_SECRET_KEY": values["S3_SECRET_KEY"]},
        {**runtime, "S3_RUNTIME_ACCESS_KEY": "kosruntime" + "z" * 32},
        {**runtime, "S3_RUNTIME_POLICY": "*"},
        {**runtime, "S3_RUNTIME_SECRET_KEY": "short"},
    ):
        monkeypatch.setattr(check, "dotenv_values", lambda path, invalid=invalid, **kwargs: invalid)
        with pytest.raises(RuntimeError, match="scoped runtime storage"):
            check.load_runtime_storage(values)


def test_check_storage_uses_scoped_credentials_and_closes_client(monkeypatch):
    calls = {}
    runtime = {
        "S3_ENDPOINT_URL": "http://127.0.0.1:19000",
        "S3_ACCESS_KEY": "runtime-user",
        "S3_SECRET_KEY": "runtime-secret",
        "S3_BUCKET": "bucket",
        "S3_REGION": "us-east-1",
    }
    monkeypatch.setattr(check, "load_runtime_storage", lambda values: runtime)

    class Storage:
        def head_bucket(self, **kwargs):
            calls["head"] = kwargs

        def close(self):
            calls["closed"] = True

    def client(name, **kwargs):
        calls["client"] = kwargs
        return Storage()

    monkeypatch.setattr(check.boto3, "client", client)
    assert check.check_storage({}) == {"bucket_reachable": True, "scoped_runtime": True}
    assert calls["client"]["aws_access_key_id"] == "runtime-user"
    assert calls["client"]["aws_secret_access_key"] == "runtime-secret"
    assert calls["head"] == {"Bucket": "bucket"}
    assert calls["closed"] is True


def test_resource_snapshot_contains_only_known_service_metrics(monkeypatch):
    metrics = "\n".join(
        f"{check.PROJECT}-{service}-1|10.0%|0.2%" for service in check.REQUIRED_SERVICES
    ).encode()
    monkeypatch.setattr(
        check, "run_docker", lambda *args, **kwargs: metrics if args[0] == "stats" else b"id"
    )
    result = check.check_stats({})
    assert result["containers_sampled"] == 7
    assert result["services"]["worker"] == {"memory_percent": 10.0, "cpu_percent": 0.2}


def test_backlog_threshold_allows_normal_dispatch_delay_and_rejects_stalled_queue():
    check.check_backlog(1, 5)
    check.check_backlog(100, 300)
    for backlog, age in ((101, 0), (1, 301)):
        with pytest.raises(RuntimeError, match="operations threshold"):
            check.check_backlog(backlog, age)


def test_report_contains_only_sanitized_check_data(tmp_path, monkeypatch):
    monkeypatch.setattr(check, "REPORT_ROOT", tmp_path)
    monkeypatch.setattr(check, "load_acceptance", lambda: {})
    monkeypatch.setattr(check, "run_check", lambda name, fn: {"status": "passed"})
    report = check.run_acceptance()
    payload = json.loads(Path(report).read_text(encoding="utf-8"))
    assert payload["status"] == "passed"
    assert payload["project"] == check.PROJECT
    assert set(payload["checks"]) == {
        "api",
        "worker",
        "database",
        "redis",
        "storage",
        "containers",
        "resources",
        "metrics",
    }
