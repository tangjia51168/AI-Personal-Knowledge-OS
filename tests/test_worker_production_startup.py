"""Exercise actual Celery entrypoints without contacting external services."""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def runtime_environment():
    # Override every settings field so local .env secrets cannot affect the test.
    return {
        **os.environ,
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql+psycopg://knowledge_app:private@postgres.internal:5432/unreachable",
        "MIGRATION_DATABASE_URL": "",
        "REDIS_URL": "redis://default:never-print-this-password@127.0.0.1:1/0",
        "S3_ENDPOINT_URL": "https://objects.internal.example",
        "S3_ACCESS_KEY": "fixture-only",
        "S3_SECRET_KEY": "storage-" + "c" * 40,
        "SYSTEM_API_TOKEN": "operator-" + "a" * 40,
        "DOWNLOAD_SIGNING_KEY": "download-" + "b" * 40,
        "CREDENTIAL_KEYS": json.dumps({"v1": base64.b64encode(bytes(range(32))).decode()}),
        "CREDENTIAL_ACTIVE_KEY": "v1",
        "REGISTRATION_ENABLED": "false",
        "CORS_ORIGINS": '["https://knowledge.example.com"]',
        "PUBLIC_APP_URL": "https://knowledge.example.com",
        "TRUSTED_HOSTS": '["knowledge.example.com","api"]',
        "OLLAMA_ENDPOINTS": "{}",
        "OPENAI_ENABLED": "false",
        "PYTHONPATH": os.pathsep.join(
            str(Path(path).resolve()) for path in ("apps/api", "apps/worker")
        ),
    }


@pytest.mark.parametrize("command", ["worker", "beat"])
def test_invalid_production_worker_and_beat_exit_before_broker_connection(tmp_path, command):
    environment = runtime_environment()
    environment["SYSTEM_API_TOKEN"] = "CHANGE_ME"
    environment.pop("MIGRATION_DATABASE_URL")
    result = subprocess.run(
        [sys.executable, "-m", "celery", "-A", "worker.celery_app:celery_app", command],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Production preflight failed" in output
    assert "never-print-this-password" not in output
    assert "Connected to" not in output


def test_valid_production_celery_import_does_not_connect(tmp_path):
    environment = runtime_environment()
    environment.pop("MIGRATION_DATABASE_URL")
    result = subprocess.run(
        [sys.executable, "-c", "from worker.celery_app import celery_app; print('loaded')"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "loaded"
    assert "never-print-this-password" not in result.stderr


@pytest.mark.parametrize("command", ["worker", "beat"])
def test_production_database_failure_exits_before_broker_connection(tmp_path, command):
    environment = runtime_environment()
    environment.pop("MIGRATION_DATABASE_URL")
    # Loopback port 1 refuses immediately. No DNS lookup or external service.
    environment["DATABASE_URL"] = (
        "postgresql+psycopg://knowledge_app:never-print-db-password@127.0.0.1:1/unreachable"
    )
    result = subprocess.run(
        [sys.executable, "-m", "celery", "-A", "worker.celery_app:celery_app", command],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Production database role check failed" in output
    assert "never-print-db-password" not in output
    assert "never-print-this-password" not in output
    assert "Connected to" not in output
