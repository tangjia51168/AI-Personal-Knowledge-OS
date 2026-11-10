"""Run a bounded, read-only operations check against the local acceptance stack.

The command is deliberately opt-in and fixed to the isolated acceptance project.
It never prints configuration values, broker URLs, credentials, or request bodies.
It does not send notifications or change service state.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import boto3
import httpx
from botocore.config import Config
from dotenv import dotenv_values
from redis import Redis
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "knowledge-os-acceptance"
REPORT_ROOT = ROOT / ".cache" / "operations"
RUNTIME_STORAGE_FILE = ROOT / ".env.storage-runtime.acceptance"
REQUIRED_SERVICES = ("postgres", "redis", "minio", "api", "worker", "beat", "web")
MAX_BACKLOG = 100
MAX_OLDEST_SECONDS = 300


def docker_path() -> str:
    command = shutil.which("docker")
    if command:
        return command
    candidate = "C:/Program Files/Docker/Docker/resources/bin/docker.exe"
    if Path(candidate).exists():
        return candidate
    raise RuntimeError("Docker CLI unavailable")


def run_docker(*args: str, timeout: int = 20) -> bytes:
    result = subprocess.run(
        [docker_path(), *args], capture_output=True, check=False, timeout=timeout
    )
    if result.returncode:
        raise RuntimeError("docker command failed")
    return result.stdout


def compose_args(values: dict[str, str]) -> list[str]:
    # All paths and the project name are fixed; no user-supplied compose target is accepted.
    return [
        "compose",
        "--env-file",
        str(ROOT / ".env.acceptance"),
        "-p",
        PROJECT,
        "-f",
        str(ROOT / "docker-compose.yml"),
        "-f",
        str(ROOT / "docker-compose.acceptance.yml"),
    ]


def load_acceptance() -> dict[str, str]:
    values = {
        name: value
        for name, value in dotenv_values(ROOT / ".env.acceptance", interpolate=False).items()
        if value is not None
    }
    required = (
        "DATABASE_URL",
        "MIGRATION_DATABASE_URL",
        "REDIS_URL",
        "S3_ENDPOINT_URL",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
        "S3_BUCKET",
    )
    if any(name not in values for name in required):
        raise RuntimeError("acceptance configuration is incomplete")
    runtime_database = urlsplit(values["DATABASE_URL"])
    migration_database = urlsplit(values["MIGRATION_DATABASE_URL"])
    redis = urlsplit(values["REDIS_URL"])
    storage = urlsplit(values["S3_ENDPOINT_URL"])
    if (
        runtime_database.scheme != "postgresql+psycopg"
        or runtime_database.hostname not in ("localhost", "127.0.0.1")
        or runtime_database.port != 15432
        or runtime_database.query
        or runtime_database.fragment
        or runtime_database.path != "/" + values.get("POSTGRES_DB", "knowledge_os")
        or runtime_database.username != "knowledge_app"
        or not runtime_database.password
        or migration_database.scheme != "postgresql+psycopg"
        or migration_database.hostname not in ("localhost", "127.0.0.1")
        or migration_database.port != 15432
        or migration_database.query
        or migration_database.fragment
        or migration_database.path != runtime_database.path
        or migration_database.username != values.get("POSTGRES_USER", "knowledge_admin")
        or not migration_database.password
        or redis.scheme != "redis"
        or redis.hostname not in ("localhost", "127.0.0.1")
        or redis.port != 16379
        or redis.query
        or redis.fragment
        or redis.path != "/0"
        or storage.scheme != "http"
        or storage.hostname not in ("localhost", "127.0.0.1")
        or storage.port != 19000
        or storage.username is not None
        or storage.password is not None
        or storage.query
        or storage.fragment
        or storage.path not in ("", "/")
    ):
        raise RuntimeError("acceptance endpoints must be isolated loopback services")
    return values


def load_runtime_storage(values: dict[str, str]) -> dict[str, str]:
    """Load the ready, scoped identity used by API/Worker/Beat.

    The operator credentials in ``.env.acceptance`` are intentionally not a
    fallback: an operations check must fail closed if the runtime identity is
    absent, malformed, or still pending.
    """
    runtime = {
        name: value
        for name, value in dotenv_values(RUNTIME_STORAGE_FILE, interpolate=False).items()
        if value is not None
    }
    access = runtime.get("S3_RUNTIME_ACCESS_KEY", "")
    secret = runtime.get("S3_RUNTIME_SECRET_KEY", "")
    policy = runtime.get("S3_RUNTIME_POLICY", "")
    if (
        runtime.get("S3_RUNTIME_STATE") != "ready"
        or not re.fullmatch(r"kosruntime[a-f0-9]{32}", access)
        or not re.fullmatch(r"[0-9a-f]{64}", secret)
        or not re.fullmatch(r"kos-runtime-[a-f0-9]{32}", policy)
        or access == values["S3_ACCESS_KEY"]
        or secret == values["S3_SECRET_KEY"]
    ):
        raise RuntimeError("scoped runtime storage configuration is missing or invalid")
    return {
        "S3_ENDPOINT_URL": values["S3_ENDPOINT_URL"],
        "S3_ACCESS_KEY": access,
        "S3_SECRET_KEY": secret,
        "S3_BUCKET": values["S3_BUCKET"],
        "S3_REGION": values.get("S3_REGION", "us-east-1"),
    }


def check_api() -> dict[str, Any]:
    with httpx.Client(timeout=8, trust_env=False, follow_redirects=False) as client:
        response = client.get(
            "http://127.0.0.1:18000/health/ready",
            headers={"Host": "api"},
        )
    if response.status_code != 200:
        raise RuntimeError("readiness endpoint is not ready")
    payload = response.json()
    data = payload.get("data", {})
    if data.get("status") != "ready":
        raise RuntimeError("readiness reported degraded")
    return {"status_code": response.status_code}


def check_worker(values: dict[str, str]) -> dict[str, Any]:
    from celery import Celery

    queue = Celery("operations", broker=values["REDIS_URL"])
    queue.conf.update(
        broker_connection_timeout=3,
        broker_connection_retry_on_startup=False,
        broker_transport_options={"socket_connect_timeout": 3, "socket_timeout": 3},
    )
    try:
        responses = queue.control.inspect(timeout=3).ping() or {}
    finally:
        queue.close()
    if not responses:
        raise RuntimeError("no independent worker responded")
    return {"workers": len(responses)}


def check_database(values: dict[str, str]) -> dict[str, Any]:
    engine = create_engine(
        values["DATABASE_URL"], hide_parameters=True, connect_args={"connect_timeout": 3}
    )
    try:
        with engine.connect() as connection:
            connection.execute(text("SET LOCAL statement_timeout='5s'"))
            role = connection.execute(
                text(
                    "SELECT r.rolsuper, r.rolbypassrls FROM pg_roles r WHERE r.rolname=current_user"
                )
            ).one()
            if role.rolsuper or role.rolbypassrls:
                raise RuntimeError("runtime database role bypasses RLS")
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            if revision != "0031_collections":
                raise RuntimeError("database migration is not current")
            row = connection.execute(
                text(
                    "SELECT count(*) FILTER (WHERE NOT done AND due_at <= now()) AS overdue, "
                    "count(*) FILTER (WHERE NOT done) AS pending, "
                    "extract(epoch FROM COALESCE("
                    "now()-min(due_at) FILTER (WHERE NOT done AND due_at <= now()), "
                    "interval '0')) AS oldest_seconds "
                    "FROM job_dispatches"
                )
            ).one()
            overdue = int(row.overdue or 0)
            pending = int(row.pending or 0)
            oldest = max(0, int(row.oldest_seconds or 0))
            check_backlog(overdue, oldest)
            return {
                "pending_dispatches": pending,
                "overdue_dispatches": overdue,
                "oldest_overdue_seconds": oldest,
            }
    finally:
        engine.dispose()


def check_backlog(overdue: int, oldest: int) -> None:
    if overdue > MAX_BACKLOG or oldest > MAX_OLDEST_SECONDS:
        raise RuntimeError("outbox backlog exceeds operations threshold")


def check_redis(values: dict[str, str]) -> dict[str, Any]:
    with Redis.from_url(values["REDIS_URL"], socket_connect_timeout=3, socket_timeout=3) as client:
        if not client.ping():
            raise RuntimeError("redis ping failed")
        info = client.info("memory")
        return {
            "used_memory_bytes": int(info.get("used_memory", 0)),
            "maxmemory_bytes": int(info.get("maxmemory", 0)),
            "maxmemory_policy": str(info.get("maxmemory_policy", "unknown")),
        }


def check_storage(values: dict[str, str]) -> dict[str, Any]:
    runtime = load_runtime_storage(values)
    client = boto3.client(
        "s3",
        endpoint_url=runtime["S3_ENDPOINT_URL"],
        aws_access_key_id=runtime["S3_ACCESS_KEY"],
        aws_secret_access_key=runtime["S3_SECRET_KEY"],
        region_name=runtime["S3_REGION"],
        config=Config(
            connect_timeout=3,
            read_timeout=8,
            retries={"max_attempts": 0},
            s3={"addressing_style": "path"},
        ),
    )
    try:
        client.head_bucket(Bucket=runtime["S3_BUCKET"])
    finally:
        client.close()
    return {"bucket_reachable": True, "scoped_runtime": True}


def check_containers(values: dict[str, str]) -> dict[str, Any]:
    raw = run_docker(*compose_args(values), "ps", "--all", "--format", "json").decode()
    services: dict[str, dict[str, Any]] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        service = item.get("Service")
        if service in REQUIRED_SERVICES:
            services[service] = {
                "state": item.get("State", "unknown"),
                "health": item.get("Health", "unknown"),
            }
    missing = [service for service in REQUIRED_SERVICES if service not in services]
    unhealthy = [
        service
        for service, item in services.items()
        if item["state"] != "running" or (item["health"] not in ("", "healthy", "unknown"))
    ]
    if missing or unhealthy:
        raise RuntimeError("acceptance containers are missing or unhealthy")
    return {"services": len(services)}


def check_stats(values: dict[str, str]) -> dict[str, Any]:
    raw = (
        run_docker(
            *compose_args(values),
            "ps",
            "--all",
            "--quiet",
        )
        .decode()
        .split()
    )
    if not raw:
        raise RuntimeError("acceptance containers unavailable")
    stats = run_docker(
        "stats",
        "--no-stream",
        "--format",
        "{{.Name}}|{{.MemPerc}}|{{.CPUPerc}}",
        *raw,
    ).decode()
    snapshots = {}
    for service in REQUIRED_SERVICES:
        for line in stats.splitlines():
            parts = line.split("|")
            if len(parts) != 3 or parts[0] != f"{PROJECT}-{service}-1":
                continue
            if not all(re.fullmatch(r"\d+(?:\.\d+)?%", value) for value in parts[1:]):
                raise RuntimeError("invalid resource snapshot")
            snapshots[service] = {
                "memory_percent": float(parts[1][:-1]),
                "cpu_percent": float(parts[2][:-1]),
            }
    if set(snapshots) != set(REQUIRED_SERVICES):
        raise RuntimeError("container resource snapshot unavailable")
    return {"containers_sampled": len(snapshots), "services": snapshots}


def run_check(name: str, check: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return {"status": "passed", **check()}
    except Exception as error:
        # Type names are safe diagnostics; exception text can contain URLs or credentials.
        return {"status": "failed", "error": type(error).__name__}


def check_metrics(values: dict[str, str]) -> dict[str, Any]:
    with httpx.Client(trust_env=False, timeout=10) as client:
        response = client.get(
            "http://127.0.0.1:18000/api/v1/system/metrics",
            headers={"Authorization": "Bearer " + values["SYSTEM_API_TOKEN"]},
        )
    if response.status_code != 200:
        raise RuntimeError("local operator metrics unavailable")
    data = response.json()["data"]
    if data["scope"] != "api-process-since-start" or data["overflow_requests"] != 0:
        raise RuntimeError("metrics scope or cardinality gate failed")
    requests = sum(item["requests"] for item in data["series"])
    errors = sum(item["errors_5xx"] for item in data["series"])
    # Counts since process start are evidence, not an alert window or an SLA.
    return {
        "scope": data["scope"],
        "requests": requests,
        "errors_5xx": errors,
        "series": len(data["series"]),
        "snapshot": data,
    }


def run_acceptance() -> Path:
    values = load_acceptance()
    checks = {
        "api": run_check("api", check_api),
        "worker": run_check("worker", lambda: check_worker(values)),
        "database": run_check("database", lambda: check_database(values)),
        "redis": run_check("redis", lambda: check_redis(values)),
        "storage": run_check("storage", lambda: check_storage(values)),
        "containers": run_check("containers", lambda: check_containers(values)),
        "resources": run_check("resources", lambda: check_stats(values)),
        "metrics": run_check("metrics", lambda: check_metrics(values)),
    }
    passed = all(item["status"] == "passed" for item in checks.values())
    report_dir = REPORT_ROOT / uuid4().hex
    report_dir.mkdir(parents=True, exist_ok=False)
    report = {
        "status": "passed" if passed else "failed",
        "completed_at": datetime.now(UTC).isoformat(),
        "project": PROJECT,
        "checks": checks,
    }
    report_path = report_dir / "summary.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not passed:
        raise RuntimeError(f"Operations check failed; report: {report_path}")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acceptance", action="store_true", required=True)
    parser.parse_args()
    try:
        report = run_acceptance()
    except Exception as error:
        raise SystemExit(
            f"Operations check failed ({type(error).__name__}); see sanitized report"
        ) from None
    print(f"PASS: acceptance operations check; report: {report}")


if __name__ == "__main__":
    main()
