"""Local cross-instance recovery, cold broker and bounded business baseline.

Only the fixed acceptance source is supported. New Docker instances and volumes
are generated for every run and retained stopped. No remote/cloud provisioning.
No secrets, account tokens, SQL rows or raw Docker output enter the report.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import secrets
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import boto3
import httpx
from botocore.config import Config
from redis import Redis
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import backup_recovery as backup  # noqa: E402
from scripts import operations_check, storage_iam  # noqa: E402

LABEL = "knowledge-os.foundation-recovery"
WRITERS = ("gateway", "web", "api", "worker", "beat")
REQUIRED = ("postgres", "redis", "minio", "api", "worker", "beat")
CONFIG = Config(
    region_name="us-east-1",
    connect_timeout=3,
    read_timeout=10,
    retries={"max_attempts": 0},
    proxies={},
    s3={"addressing_style": "path"},
)


def docker(*args: str, data: bytes | None = None, timeout: int = 60) -> bytes:
    result = subprocess.run(
        [backup.docker_path(), *args],
        input=data,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Docker operation failed; raw output withheld")
    return result.stdout


def inspect(container: str) -> dict:
    return json.loads(docker("inspect", container))[0]


def source_inventory() -> dict[str, dict]:
    operations_check.load_acceptance()
    result = {}
    for service in (*REQUIRED, "web", "gateway"):
        ids = (
            docker(
                "ps",
                "--quiet",
                "--filter",
                "label=com.docker.compose.project=" + backup.PROJECT,
                "--filter",
                "label=com.docker.compose.service=" + service,
            )
            .decode()
            .split()
        )
        if service == "gateway" and not ids:
            continue
        if len(ids) != 1:
            raise RuntimeError("One acceptance container per service required")
        info = inspect(ids[0])
        if (
            info["Config"]["Labels"].get("com.docker.compose.project") != backup.PROJECT
            or info["Config"]["Labels"].get("com.docker.compose.service") != service
            or not info["State"]["Running"]
        ):
            raise RuntimeError("Source must be the running acceptance project")
        result[service] = info
    # Validate storage/db container bindings before any fixture or service change.
    for service, internal, external in (
        ("postgres", "5432/tcp", "15432"),
        ("redis", "6379/tcp", "16379"),
        ("minio", "9000/tcp", "19000"),
        ("api", "8000/tcp", "18000"),
    ):
        if result[service]["NetworkSettings"]["Ports"].get(internal) != [
            {"HostIp": "127.0.0.1", "HostPort": external}
        ]:
            raise RuntimeError("Source bindings are not isolated loopback acceptance")
    return result


def environment(info: dict) -> dict[str, str]:
    return dict(item.split("=", 1) for item in info["Config"]["Env"] if "=" in item)


def published_port(container: str, internal: str) -> int:
    bindings = inspect(container)["NetworkSettings"]["Ports"][internal]
    if len(bindings) != 1 or bindings[0]["HostIp"] != "127.0.0.1":
        raise RuntimeError("Recovery binding is not loopback")
    return int(bindings[0]["HostPort"])


def wait_until(check, *, seconds: int = 100) -> Any:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("Recovery deadline exceeded; targets retained")


def require(condition: bool, label: str) -> None:
    if not condition:
        raise RuntimeError(label)


def api_data(response: httpx.Response, status: int = 200) -> Any:
    require(response.status_code == status, "Unexpected business response")
    return response.json()["data"]


def baseline(client: httpx.Client, path: str, auth: dict) -> dict:
    """Small deterministic read workload, not a capacity or public SLA claim."""

    def request(_: int) -> float:
        started = time.monotonic()
        api_data(client.get(path, headers=auth))
        return (time.monotonic() - started) * 1000

    api_data(client.get(path, headers=auth))  # warmup excluded
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as pool:
        samples = sorted(pool.map(request, range(24)))
    elapsed = time.monotonic() - started
    return {
        "requests": len(samples),
        "concurrency": 4,
        "failures": 0,
        "elapsed_seconds": round(elapsed, 3),
        "requests_per_second": round(len(samples) / elapsed, 2) if elapsed > 0 else None,
        "p50_ms": round(samples[11], 2),
        "p95_ms": round(samples[22], 2),
        "max_ms": round(max(samples), 2),
        "workload": "authenticated-asset-list",
        "capacity_verified": False,
    }


class Target:
    def __init__(self, run_id: str, images: dict[str, dict]) -> None:
        self.name = "kos-recovery-" + run_id
        self.images = images
        self.containers: list[str] = []
        self.volumes: list[str] = []
        docker("network", "create", "--internal", "--label", LABEL + "=" + run_id, self.name)
        # Docker Desktop cannot publish host ports from an internal-only bridge.
        # Data services get an additional bridge for bounded loopback operator I/O;
        # Worker/Beat have only the internal network and cannot reach providers.
        # API has loopback operator I/O; model/collector/email switches are disabled.
        docker("network", "create", "--label", LABEL + "=" + run_id, self.name + "-operator")

    def start(
        self,
        service: str,
        command: list[str],
        env: dict[str, str],
        *,
        volume: str | None = None,
        port: str | None = None,
    ) -> str:
        name = self.name + "-" + service
        arguments = [
            "run",
            "-d",
            "--pull=never",
            "--name",
            name,
            "--label",
            LABEL + "=" + self.name,
            "--network",
            self.name if service in ("worker", "beat") else self.name + "-operator",
            "--network-alias",
            service,
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--memory=768m",
            "--cpus=1",
            "--pids-limit=256",
            "--restart=no",
        ]
        if volume:
            volume_name = name + "-data"
            docker("volume", "create", "--label", LABEL + "=" + self.name, volume_name)
            self.volumes.append(volume_name)
            arguments.extend(["--mount", f"type=volume,source={volume_name},target={volume}"])
        if service == "postgres":
            # Postgres entrypoint needs to initialise/chown the fresh owned volume.
            arguments.extend(
                [
                    "--cap-add=CHOWN",
                    "--cap-add=FOWNER",
                    "--cap-add=DAC_OVERRIDE",
                    "--cap-add=SETUID",
                    "--cap-add=SETGID",
                ]
            )
        else:
            arguments.extend(
                ["--user", self.images[service]["Config"].get("User") or "10001:10001"]
            )
            # Redis official uid owns a newly-created root /data only after chown.
            if service == "redis":
                arguments = arguments[:-2]
                arguments.extend(["--cap-add=CHOWN", "--cap-add=SETUID", "--cap-add=SETGID"])
        if service in ("api", "worker", "beat"):
            arguments.extend(["--read-only", "--tmpfs", "/tmp:rw,nosuid,size=67108864,mode=1777"])
        if port:
            arguments.extend(["--publish", "127.0.0.1::" + port])
        # Pass env via the subprocess environment, never command arguments/console.
        for key in env:
            arguments.extend(["--env", key])
        self.containers.append(name)
        result = subprocess.run(
            [backup.docker_path(), *arguments, self.images[service]["Image"], *command],
            env={**os.environ, **env},
            capture_output=True,
            timeout=60,
            check=False,
        )
        require(result.returncode == 0, "Recovery container start failed")
        if service in ("postgres", "redis", "minio", "api"):
            docker("network", "connect", "--alias", service, self.name, name)
        return name

    def stop(self) -> None:
        failed = False
        for name in reversed(self.containers):
            try:
                # Unique generated identity + label: never stop an unrelated container.
                require(
                    inspect(name)["Config"]["Labels"].get(LABEL) == self.name,
                    "Recovery ownership differs",
                )
                docker("stop", "--time", "10", name)
            except Exception:
                failed = True
        require(not failed, "Recovery target stop failed; inspect retained targets")


def restore_objects(storage, bucket: str, directory: Path, key: bytes, manifest: dict) -> None:
    storage.create_bucket(Bucket=bucket)
    for item in manifest["objects"]:
        content = io.BytesIO()
        backup.decrypt_stream(directory / item["file"], key, content)
        storage.put_object(
            Bucket=bucket,
            Key=item["key"],
            Body=content.getvalue(),
            ContentType=item["content_type"],
        )
        response = storage.get_object(Bucket=bucket, Key=item["key"])
        try:
            digest, size = hashlib.sha256(), 0
            while block := response["Body"].read(backup.CHUNK):
                digest.update(block)
                size += len(block)
        finally:
            response["Body"].close()
        require(
            digest.hexdigest() == item["sha256"] and size == item["size"], "Restored object differs"
        )


def seed_product_records(client: httpx.Client, path: str, auth: dict, run_id: str) -> dict:
    """Actual import Worker + human note + no-provider research + paused schedule.

    The fresh protocol tenant has no provider configuration. Its retained failed
    research record exercises real citations without making any inference call.
    """
    batch = api_data(
        client.post(
            path + "/sources",
            headers=auth,
            json={
                "text": "https://example.com/recovery-protocol-" + run_id,
                "idempotency_key": uuid4().hex,
            },
        ),
        201,
    )
    source = batch["sources"][0]["id"]
    task = api_data(
        client.post(
            path + f"/sources/{source}/imports",
            headers=auth,
            json={
                "idempotency_key": uuid4().hex,
                "consent": True,
                "title": "人工恢复协议资料",
                "text": "恢复协议专用原文：先准备三个材料，再验证输出。",
                "format": "text",
                "content_kind": "article",
            },
        ),
        202,
    )

    def imported():
        result = api_data(client.get(path + "/jobs/" + task["id"], headers=auth))
        require(result["status"] not in ("failed", "cancelled"), "Protocol import failed")
        return result if result["status"] == "succeeded" else None

    wait_until(imported)
    snapshot = api_data(client.get(path + "/jobs/" + task["id"] + "/result", headers=auth))[
        "snapshot_id"
    ]
    note = api_data(
        client.post(
            path + f"/snapshots/{snapshot}/notes",
            headers=auth,
            json={
                "idempotency_key": uuid4().hex,
                "consent": True,
                "title": "恢复协议人工笔记",
                "overview": {"text": "准备三个材料，再验证输出。", "evidence_keys": ["E001"]},
                "sections": [
                    {
                        "kind": "core_claims",
                        "items": [{"text": "先准备再验证。", "evidence_keys": ["E001"]}],
                    }
                ],
                "personal_notes": "协议夹具，仅验收恢复。",
            },
        )
    )
    research = api_data(
        client.post(
            path + "/knowledge/answers",
            headers=auth,
            json={
                "idempotency_key": uuid4().hex,
                "consent": True,
                "question": "恢复协议要求准备什么？",
                "query": "恢复协议",
            },
        )
    )
    require(
        research["status"] == "failed" and bool(research["citations"]),
        "Fresh no-provider research fixture differs",
    )
    require(
        research["citations"][0]["revision_id"] == note["revision_id"], "Fixture citation differs"
    )
    schedule = api_data(
        client.post(
            path + "/sources/schedules",
            headers=auth,
            json={
                "source_id": source,
                "interval_minutes": 1440,
                "consent": True,
            },
        ),
        201,
    )
    paused = api_data(
        client.patch(
            path + "/sources/schedules/" + schedule["id"],
            headers=auth,
            json={
                "active": False,
                "expected_version": 1,
                "consent": False,
            },
        )
    )
    collection = api_data(
        client.post(
            path + "/collections",
            headers=auth,
            json={"kind": "collection", "name": "恢复协议素材合集"},
        )
    )
    collection = api_data(
        client.put(
            path + f"/collections/{collection['id']}/items/{source}",
            headers=auth,
            json={"expected_version": 1, "active": True, "position": 7},
        )
    )
    collection_page = api_data(
        client.get(path + f"/collections/{collection['id']}/items", headers=auth)
    )
    return {"note": note, "research": research, "schedule": paused, "collection": collection_page}


def verify_product_records(client: httpx.Client, path: str, auth: dict, records: dict) -> None:
    collection = records["collection"]
    restored = api_data(
        client.get(path + f"/collections/{collection['collection']['id']}/items", headers=auth)
    )
    require(restored == collection, "Restored source collection and order differ")
    note = records["note"]
    exported = client.get(
        path + f"/notes/{note['note_id']}/revisions/{note['revision_id']}/export",
        headers=auth,
        params={"format": "json"},
    )
    require(exported.status_code == 200, "Restored manual note unavailable")
    value = exported.json()
    require(
        value["revision"]["origin"] == "user_edit" and value["revision"]["version"] == 1,
        "Restored manual provenance differs",
    )
    require(
        value["evidence"][0]["quote"] == "恢复协议专用原文：先准备三个材料，再验证输出。",
        "Restored evidence differs",
    )
    research = api_data(
        client.get(path + "/knowledge/answers/" + records["research"]["id"], headers=auth)
    )
    require(research == records["research"], "Restored fixed research citations differ")
    schedules = api_data(client.get(path + "/sources/schedules", headers=auth))
    require(schedules["schedules"] == [records["schedule"]], "Restored paused schedule differs")


def checkpoint_fingerprint(url: str) -> list[tuple]:
    """Compare durable stage content/usage linkage without outputting note bodies."""
    from sqlalchemy import create_engine

    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            connection.execute(text("SET LOCAL statement_timeout='5s'"))
            return [
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT tenant_id,run_id,position,usage_id,provider_id,model,prompt_sha256,"
                        "protocol,status,md5(COALESCE(output::text,'')),error_code "
                        "FROM summary_checkpoints ORDER BY tenant_id,run_id,position"
                    )
                )
            ]
    finally:
        engine.dispose()


def semantic_fingerprint(url: str) -> list[tuple]:
    """Verify exact persisted vectors/text bindings without exposing user content."""
    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            connection.execute(text("SET LOCAL statement_timeout='5s'"))
            return [
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT tenant_id,note_id,snapshot_id,revision_id,position,model,"
                        "md5(quote),md5(embedding::text),created_by FROM semantic_chunks "
                        "ORDER BY tenant_id,revision_id,model,position"
                    )
                )
            ]
    finally:
        engine.dispose()


def notification_fingerprint(url: str) -> list[tuple]:
    """Exact subscription/receipt linkage, including uncertainty and read timestamps."""
    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            connection.execute(text("SET LOCAL statement_timeout='5s'"))
            return [
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT p.tenant_id,p.user_id,p.id,p.enabled,p.email,p.version,"
                        "d.id,d.preference_version,d.occurrence,d.kind,d.note_id,d.reminder_at,"
                        "d.email_state,d.read_at,d.dispatched_at,d.finished_at "
                        "FROM notification_preferences p LEFT JOIN notification_deliveries d "
                        "ON d.tenant_id=p.tenant_id AND d.preference_id=p.id "
                        "AND d.user_id=p.user_id "
                        "ORDER BY p.tenant_id,p.user_id,d.id"
                    )
                )
            ]
    finally:
        engine.dispose()


def collection_fingerprint(url: str) -> list[tuple]:
    """Compare all groups and retained memberships, without exposing names or URLs."""
    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            connection.execute(text("SET LOCAL statement_timeout='5s'"))
            return [
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT c.tenant_id,c.id,c.kind,md5(c.name),md5(c.name_key),"
                        "c.active,c.version,c.created_by,c.updated_by,c.created_at,c.updated_at,"
                        "i.source_id,i.active,i.position,i.created_by,i.updated_by,"
                        "i.created_at,i.updated_at FROM collections c "
                        "LEFT JOIN collection_items i ON i.tenant_id=c.tenant_id "
                        "AND i.collection_id=c.id ORDER BY c.tenant_id,c.id,i.source_id"
                    )
                )
            ]
    finally:
        engine.dispose()


def run() -> Path:
    source = source_inventory()
    values = operations_check.load_acceptance()
    source_checkpoints = checkpoint_fingerprint(values["MIGRATION_DATABASE_URL"])
    run_id = uuid4().hex
    directory = ROOT / ".cache/foundation-recovery" / run_id
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    report: dict[str, Any] = {
        "status": "failed",
        "scope": "local-cross-instance",
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "checks": {},
        "offsite_verified": False,
        "production_recovery_verified": False,
        "redis_source_copied": False,
        "source_data_retained": True,
    }
    target = None
    account = None
    stopped: list[str] = []
    outage_started = time.monotonic()
    try:
        with httpx.Client(base_url="http://127.0.0.1:18000", trust_env=False, timeout=20) as client:
            account = api_data(
                client.post(
                    "/api/v1/auth/register",
                    json={
                        "email": f"foundation-{run_id}@example.invalid",
                        "password": secrets.token_urlsafe(32),
                    },
                ),
                201,
            )
            auth = {"Authorization": "Bearer " + account["access_token"]}
            path = f"/api/v1/spaces/{account['tenant_id']}"
            product_records = seed_product_records(client, path, auth, run_id)
            api_data(
                client.put(
                    path + "/learning/notifications/preference",
                    headers=auth,
                    json={
                        "expected_version": 0,
                        "enabled": True,
                        "email": False,
                        "consent": True,
                    },
                )
            )
            receipt = api_data(
                client.post(
                    path + "/learning/notifications/test",
                    headers=auth,
                    json={"idempotency_key": str(uuid4()), "consent": True},
                )
            )
            api_data(
                client.post(
                    path + f"/learning/notifications/{receipt['id']}/read", headers=auth, json={}
                )
            )
            # Freeze this fixture before capture; restored Worker must not send mail.
            api_data(
                client.put(
                    path + "/learning/notifications/preference",
                    headers=auth,
                    json={
                        "expected_version": 1,
                        "enabled": False,
                        "email": False,
                        "consent": False,
                    },
                )
            )
            report["checks"]["personal_notification_fixture"] = True
            # Only when the explicit local service is enabled; never fake vectors.
            if environment(source["api"]).get("LOCAL_EMBEDDING_ENDPOINT"):
                indexed = api_data(
                    client.post(
                        path + "/knowledge/semantic/index",
                        headers=auth,
                        json={
                            "note_id": product_records["note"]["note_id"],
                            "revision_id": product_records["note"]["revision_id"],
                            "consent": True,
                        },
                    )
                )
                require(indexed["chunks"] > 0, "Recovery semantic fixture missing")
                report["checks"]["real_local_semantic_fixture"] = True
            # Hold dispatch before creating the recovery outbox jobs.
            docker("stop", source["beat"]["Id"])
            stopped.append("beat")
            body = ("cross-instance acceptance " + run_id).encode()
            asset = api_data(
                client.post(
                    path + "/assets", content=body, headers={**auth, "Content-Type": "text/plain"}
                ),
                201,
            )
            job = api_data(
                client.post(path + "/jobs", headers=auth, json={"idempotency_key": run_id}), 202
            )
            require(job["status"] == "queued", "Source fixture must be queued")
            fenced_job = api_data(
                client.post(path + "/jobs", headers=auth, json={"idempotency_key": uuid4().hex}),
                202,
            )
            require(fenced_job["status"] == "queued", "Fenced fixture must be queued")
        for service in WRITERS:
            if service in source and service not in stopped:
                docker("stop", source[service]["Id"])
                stopped.append(service)
        source_semantics = semantic_fingerprint(values["MIGRATION_DATABASE_URL"])
        source_notifications = notification_fingerprint(values["MIGRATION_DATABASE_URL"])
        source_collections = collection_fingerprint(values["MIGRATION_DATABASE_URL"])
        snapshot_started = time.monotonic()
        archive = backup.drill(archive_only=True)
        report["archive"] = archive.name
        report["capture_seconds"] = round(time.monotonic() - snapshot_started, 3)
        snapshot_finished = time.monotonic()
        report["stage"] = "authenticate"
        key = backup.load_key(archive / "recovery-key.json")
        manifest_buffer = io.BytesIO()
        backup.decrypt_stream(archive / "manifest.json.enc", key, manifest_buffer)
        manifest = json.loads(manifest_buffer.getvalue())
        # The archive is just captured in-process, not an untrusted imported path.
        require(manifest["project"] == backup.PROJECT, "Archive project differs")
        report["checks"]["authenticated_archive"] = True
        target = Target(run_id, source)
        report["stage"] = "start-data-services"
        admin_password, app_password = secrets.token_hex(32), secrets.token_hex(32)
        root_user, root_password = "recovery" + run_id[:20], secrets.token_hex(32)
        database, username, bucket = (
            values.get("POSTGRES_DB", "knowledge_os"),
            values.get("POSTGRES_USER", "knowledge_admin"),
            values["S3_BUCKET"],
        )
        pg = target.start(
            "postgres",
            [],
            {
                "POSTGRES_USER": username,
                "POSTGRES_PASSWORD": admin_password,
                "POSTGRES_DB": database,
            },
            volume="/var/lib/postgresql/data",
            port="5432",
        )
        red = target.start(
            "redis",
            [
                "redis-server",
                "--appendonly",
                "yes",
                "--maxmemory",
                "192mb",
                "--maxmemory-policy",
                "noeviction",
            ],
            {},
            volume="/data",
            port="6379",
        )
        target.start(
            "minio",
            ["server", "/data"],
            {"MINIO_ROOT_USER": root_user, "MINIO_ROOT_PASSWORD": root_password},
            volume="/data",
            port="9000",
        )
        pg_port = published_port(pg, "5432/tcp")
        db_url = make_url(values["MIGRATION_DATABASE_URL"]).set(
            host="127.0.0.1", port=pg_port, password=admin_password, database=database
        )
        admin = create_engine(db_url, connect_args={"connect_timeout": 3}, hide_parameters=True)

        def postgres_ready():
            with admin.connect() as connection:
                return connection.execute(text("SELECT 1")).scalar_one() == 1

        wait_until(postgres_ready)
        report["stage"] = "restore-database"
        with admin.begin() as connection:
            # Only generated credentials; parameter-safe quoting via psycopg literal.
            from psycopg import sql

            statement = sql.SQL(
                "CREATE ROLE knowledge_app LOGIN PASSWORD {} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            ).format(sql.Literal(app_password))
            connection.exec_driver_sql(statement.as_string())
            connection.execute(text("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))
            connection.execute(text("GRANT USAGE ON SCHEMA public TO knowledge_app"))
        backup.restore_database(pg, username, database, archive / "postgres.dump.enc", key)
        with admin.begin() as connection:
            require(
                backup.database_fingerprint(connection) == manifest["database_fingerprint"],
                "Restored database fingerprint differs",
            )
            # Isolated recovery must never execute a user's other recovered
            # tasks. Fingerprint first, then fence all except this explicit
            # random protocol job before starting Beat/Worker.
            connection.execute(
                text("UPDATE job_dispatches SET done=true WHERE job_id<>:job"),
                {"job": job["id"]},
            )
            report["checks"]["unrelated_recovered_tasks_fenced"] = True
        admin.dispose()
        report["checks"]["data_constraints_rls_acl_triggers"] = True
        # db_url is the new independent target, never the source.
        restored_checkpoints = checkpoint_fingerprint(db_url.render_as_string(hide_password=False))
        require(restored_checkpoints == source_checkpoints, "Restored durable stages differ")
        report["checks"]["durable_summary_stages_and_usage_links"] = True
        report["summary_checkpoint_count"] = len(restored_checkpoints)
        restored_semantics = semantic_fingerprint(db_url.render_as_string(hide_password=False))
        require(restored_semantics == source_semantics, "Restored semantic vectors differ")
        report["checks"]["exact_semantic_vectors_and_revision_bindings"] = True
        report["semantic_chunk_count"] = len(restored_semantics)
        restored_notifications = notification_fingerprint(
            db_url.render_as_string(hide_password=False)
        )
        require(restored_notifications == source_notifications, "Restored notifications differ")
        report["checks"]["exact_personal_notification_receipts"] = True
        report["notification_fingerprint_rows"] = len(restored_notifications)
        restored_collections = collection_fingerprint(db_url.render_as_string(hide_password=False))
        require(restored_collections == source_collections, "Restored collections differ")
        report["checks"]["exact_collections_and_retained_memberships"] = True
        report["collection_fingerprint_rows"] = len(restored_collections)
        report["database_tables"] = len(manifest["database_fingerprint"])
        report["stage"] = "restore-storage"
        endpoint = f"http://127.0.0.1:{published_port(target.name + '-minio', '9000/tcp')}"
        storage = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=root_user,
            aws_secret_access_key=root_password,
            config=CONFIG,
        )
        wait_until(lambda: storage.list_buckets() is not None)
        restore_objects(storage, bucket, archive, key, manifest)
        report["objects"] = len(manifest["objects"])
        report["checks"]["objects_sha256"] = True
        require(
            httpx.get(
                endpoint + "/" + bucket + "/" + manifest["marker_key"], trust_env=False, timeout=10
            ).status_code
            == 403,
            "Anonymous restored bucket access allowed",
        )
        # Policy-scoped STS credential, not root, is used by every restored runtime.
        sts = boto3.client(
            "sts",
            endpoint_url=endpoint,
            aws_access_key_id=root_user,
            aws_secret_access_key=root_password,
            config=CONFIG,
        )
        credentials = sts.assume_role(
            RoleArn="arn:aws:iam::123456789012:role/knowledge-os-runtime",
            RoleSessionName="foundation-recovery",
            DurationSeconds=3600,
            Policy=json.dumps(storage_iam.runtime_policy(bucket)),
        )["Credentials"]
        runtime_storage = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
            config=CONFIG,
        )
        storage_iam.denied(lambda: runtime_storage.get_bucket_policy(Bucket=bucket))
        storage_iam.denied(
            lambda: runtime_storage.get_object(Bucket=bucket, Key=manifest["marker_key"])
        )
        runtime_storage.close()
        sts.close()
        storage.close()
        report["checks"]["private_scoped_storage"] = True
        with Redis(
            host="127.0.0.1", port=published_port(red, "6379/tcp"), socket_timeout=3
        ) as redis:
            require(redis.dbsize() == 0, "Recovery broker must start empty")
        report["checks"]["cold_redis"] = True
        report["stage"] = "start-runtime"
        env = environment(source["api"])
        # Retain in-memory source signing/encryption configuration. No email,
        # platform or model egress is permitted in the independent recovery network.
        env.update(
            {
                "APP_ENV": "test",
                "DATABASE_URL": db_url.set(
                    host="postgres", port=5432, username="knowledge_app", password=app_password
                ).render_as_string(hide_password=False),
                "REDIS_URL": "redis://redis:6379/0",
                "S3_ENDPOINT_URL": "http://minio:9000",
                "S3_BUCKET": bucket,
                "S3_ACCESS_KEY": credentials["AccessKeyId"],
                "S3_SECRET_KEY": credentials["SecretAccessKey"],
                "S3_SESSION_TOKEN": credentials["SessionToken"],
                "OPENAI_ENABLED": "false",
                "DEEPSEEK_ENABLED": "false",
                "COLLECTOR_ENABLED": "false",
                "SMTP_HOST": "",
                "SMTP_USERNAME": "",
                "SMTP_PASSWORD": "",
                "OLLAMA_ENDPOINTS": "{}",
                "LOCAL_EMBEDDING_ENDPOINT": "",
            }
        )
        env.pop("MIGRATION_DATABASE_URL", None)
        api = target.start("api", source["api"]["Config"]["Cmd"], env, port="8000")
        base_url = f"http://127.0.0.1:{published_port(api, '8000/tcp')}"
        with httpx.Client(base_url=base_url, trust_env=False, timeout=15) as client:
            wait_until(lambda: client.get("/health/ready").status_code == 200)
            restored = api_data(client.get(path + "/assets", headers=auth))
            require(any(item["id"] == asset["id"] for item in restored), "Restored asset absent")
            ticket = api_data(
                client.post(path + f"/assets/{asset['id']}/download-ticket", headers=auth)
            )
            download = client.get(
                path + f"/assets/{asset['id']}/download", params=ticket, headers=auth
            )
            require(
                download.status_code == 200 and download.content == body,
                "Restored download differs",
            )
            require(client.get(path + "/assets").status_code == 401, "Unauthenticated read allowed")
            other = api_data(
                client.post(
                    "/api/v1/auth/register",
                    json={
                        "email": f"other-{run_id}@example.invalid",
                        "password": secrets.token_urlsafe(32),
                    },
                ),
                201,
            )
            require(
                client.get(
                    path + "/assets", headers={"Authorization": "Bearer " + other["access_token"]}
                ).status_code
                == 404,
                "Cross-tenant read allowed",
            )
            report["checks"]["restored_session_asset_and_isolation"] = True
            verify_product_records(client, path, auth, product_records)
            report["checks"]["restored_manual_note_research_and_paused_schedule"] = True
            notifications = api_data(client.get(path + "/learning/notifications", headers=auth))
            require(
                notifications["preference"]
                == {
                    "version": 2,
                    "enabled": False,
                    "email": False,
                },
                "Restored notification preference differs",
            )
            require(
                any(
                    row["id"] == receipt["id"]
                    and row["read_at"] is not None
                    and row["email_state"] == "not_requested"
                    for row in notifications["data"]
                ),
                "Restored read receipt missing",
            )
            require(
                client.get(
                    path + "/learning/notifications",
                    headers={"Authorization": "Bearer " + other["access_token"]},
                ).status_code
                == 404,
                "Restored notification isolation failed",
            )
            report["checks"]["restored_notification_api_and_isolation"] = True
            api_data(client.get(path + "/jobs/" + job["id"], headers=auth))
            report["business_ready_seconds"] = round(time.monotonic() - snapshot_finished, 3)
            # Independent Beat republishes the restored DB outbox to the empty broker.
            target.start("worker", source["worker"]["Config"]["Cmd"], env)
            target.start("beat", source["beat"]["Config"]["Cmd"], env)

            def completed_job():
                record = api_data(client.get(path + "/jobs/" + job["id"], headers=auth))
                require(record["status"] not in ("failed", "cancelled"), "Recovered job failed")
                return record if record["status"] == "succeeded" else None

            completed = wait_until(completed_job, seconds=70)
            require(completed["attempt_count"] == 1, "Recovered job replayed unexpectedly")
            result = api_data(client.get(path + "/jobs/" + job["id"] + "/result", headers=auth))
            require(
                result["space"]["id"] == account["tenant_id"]
                and any(item["id"] == asset["id"] for item in result["assets"]),
                "Worker result differs",
            )
            report["checks"]["outbox_independent_worker"] = True
            fenced_record = api_data(client.get(path + "/jobs/" + fenced_job["id"], headers=auth))
            require(
                fenced_record["status"] == "queued" and fenced_record["attempt_count"] == 0,
                "Recovery executed an unrelated task",
            )
            report["checks"]["fenced_protocol_job_not_executed"] = True
            report["outbox_ready_seconds"] = round(time.monotonic() - snapshot_finished, 3)
            report["baseline"] = baseline(client, path + "/assets", auth)
            report["checks"]["bounded_business_baseline"] = True
            metrics = api_data(
                client.get(
                    "/api/v1/system/metrics",
                    headers={"Authorization": "Bearer " + env["SYSTEM_API_TOKEN"]},
                )
            )
            require(
                metrics["scope"] == "api-process-since-start"
                and sum(item["requests"] for item in metrics["series"]) >= 25,
                "Restored API metrics unavailable",
            )
            report["checks"]["restored_api_metrics_consumer"] = True
        report["status"] = "passed"
    except Exception as error:
        report["error_class"] = type(error).__name__
    finally:
        cleanup_errors = []
        if target:
            report["target"] = target.name
            report["volumes_retained"] = target.volumes
            try:
                target.stop()
                report["checks"]["target_stopped_retained"] = True
            except Exception as error:
                cleanup_errors.append(type(error).__name__)
        # Restore precisely the previously running source containers in dependency order.
        for service in ("api", "worker", "beat", "web", "gateway"):
            if service in stopped:
                try:
                    require(
                        inspect(source[service]["Id"])["Config"]["Labels"].get(
                            "com.docker.compose.project"
                        )
                        == backup.PROJECT,
                        "Source ownership differs",
                    )
                    docker("start", source[service]["Id"])
                except Exception as error:
                    cleanup_errors.append(type(error).__name__)
        try:
            with httpx.Client(
                base_url="http://127.0.0.1:18000", trust_env=False, timeout=15
            ) as client:
                wait_until(lambda: client.get("/health/ready").status_code == 200)
                if account:
                    original = api_data(client.get(path + "/assets", headers=auth))
                    require(
                        any(item["id"] == asset["id"] for item in original), "Source data absent"
                    )
                report["checks"]["source_api_resumed"] = True
                report["source_pause_and_drill_seconds"] = round(
                    time.monotonic() - outage_started, 3
                )

            def source_healthy():
                for service in source:
                    state = inspect(source[service]["Id"])["State"]
                    if (
                        not state["Running"]
                        or state.get("Health", {}).get("Status", "healthy") != "healthy"
                    ):
                        return False
                return True

            wait_until(source_healthy)
            report["checks"]["all_source_services_resumed"] = True
        except Exception as error:
            cleanup_errors.append(type(error).__name__)
        try:
            if account:
                # Reuse the established fixture-only cleanup with exact random IDs.
                from app.core.config import get_settings
                from app.integrations.storage import get_s3_client

                from scripts.account_e2e import cleanup_fixture

                with storage_iam.application_storage(
                    values,
                    {
                        "AccessKeyId": values["S3_ACCESS_KEY"],
                        "SecretAccessKey": values["S3_SECRET_KEY"],
                        "SessionToken": "",
                    },
                ):
                    engine = create_engine(values["MIGRATION_DATABASE_URL"], hide_parameters=True)
                    try:
                        with engine.begin() as connection:
                            cleanup_fixture(connection, account)
                    finally:
                        engine.dispose()
                        get_s3_client.cache_clear()
                        get_settings.cache_clear()
                report["checks"]["source_fixture_cleaned"] = True
        except Exception as error:
            cleanup_errors.append(type(error).__name__)
        if cleanup_errors:
            report["status"] = "failed"
            report["cleanup_errors"] = cleanup_errors
        report["completed_at"] = datetime.now(UTC).isoformat()
        with backup.private_create(directory / "summary.json") as output:
            output.write(json.dumps(report, indent=2).encode())
    require(
        report["status"] == "passed", "Cross-instance recovery failed; sanitized report retained"
    )
    return directory / "summary.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acceptance", action="store_true", required=True)
    parser.parse_args()
    try:
        destination = run()
    except Exception as error:
        raise SystemExit(
            f"FAIL: foundation recovery ({type(error).__name__}); retained report"
        ) from None
    print(f"PASS: local cross-instance recovery; report: {destination}")


if __name__ == "__main__":
    main()
