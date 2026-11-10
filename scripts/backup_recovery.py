"""Encrypted PostgreSQL/S3 recovery drill for the isolated acceptance stack only.

The caller stops api/worker/beat/web first. Source data and named volumes are never
deleted. Restore always creates a new database and private bucket and retains them.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import quote, urlsplit
from uuid import uuid4

import boto3
import httpx
from botocore.config import Config
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from dotenv import dotenv_values
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "knowledge-os-acceptance"
MAGIC = b"KOSBACKUP\x01"
CHUNK = 1024 * 1024
MAX_DUMP = 512 * CHUNK
MAX_OBJECT_TOTAL = 256 * CHUNK
MAX_OBJECTS = 10000


def private_create(path: Path) -> BinaryIO:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(descriptor, "wb")


def encrypt_stream(source: BinaryIO, destination: Path, key: bytes, limit: int) -> dict[str, Any]:
    nonce = secrets.token_bytes(12)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(MAGIC)
    digest, size = hashlib.sha256(), 0
    with private_create(destination) as target:
        target.write(MAGIC + nonce)
        while block := source.read(CHUNK):
            size += len(block)
            if size > limit:
                raise RuntimeError("Backup resource limit exceeded; partial ciphertext retained")
            digest.update(block)
            target.write(encryptor.update(block))
        target.write(encryptor.finalize())
        target.write(encryptor.tag)
    return {"size": size, "sha256": digest.hexdigest(), "file": destination.name}


def decrypt_stream(source: Path, key: bytes, target: BinaryIO | None = None) -> dict[str, Any]:
    length = source.stat().st_size
    header_size = len(MAGIC) + 12
    if length < header_size + 16 or length > MAX_DUMP + header_size + 16:
        raise RuntimeError("Invalid encrypted backup length")
    digest, size = hashlib.sha256(), 0
    with source.open("rb") as encrypted:
        if encrypted.read(len(MAGIC)) != MAGIC:
            raise RuntimeError("Invalid encrypted backup format")
        nonce = encrypted.read(12)
        encrypted.seek(-16, os.SEEK_END)
        tag = encrypted.read(16)
        encrypted.seek(header_size)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(MAGIC)
        remaining = length - header_size - 16
        while remaining:
            block = encrypted.read(min(CHUNK, remaining))
            if not block:
                raise RuntimeError("Truncated encrypted backup")
            remaining -= len(block)
            plaintext = decryptor.update(block)
            size += len(plaintext)
            digest.update(plaintext)
            if target is not None:
                target.write(plaintext)
        tail = decryptor.finalize()
        digest.update(tail)
        size += len(tail)
        if target is not None:
            target.write(tail)
    return {"size": size, "sha256": digest.hexdigest(), "file": source.name}


class DataBlob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def protect_windows_key(value: bytes, decrypt: bool = False) -> bytes:
    buffer = ctypes.create_string_buffer(value)
    source = DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = DataBlob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    operation = library.CryptUnprotectData if decrypt else library.CryptProtectData
    operation.argtypes = [
        ctypes.POINTER(DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(DataBlob),
    ]
    operation.restype = ctypes.c_int
    free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN: never open an interactive/system prompt.
    ok = operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    if not ok:
        raise RuntimeError("Windows key protection failed")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        free(output.data)


def save_key(path: Path, key: bytes) -> None:
    if os.name == "nt":
        payload = {
            "method": "windows-dpapi-current-user",
            "value": base64.b64encode(protect_windows_key(key)).decode(),
        }
    else:
        payload = {"method": "owner-only-0600", "value": base64.b64encode(key).decode()}
    with private_create(path) as target:
        target.write(json.dumps(payload).encode())


def load_key(path: Path) -> bytes:
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise RuntimeError("Backup key file must be owner-only")
    payload = json.loads(path.read_bytes())
    raw = base64.b64decode(payload["value"], validate=True)
    if payload["method"] == "windows-dpapi-current-user" and os.name == "nt":
        raw = protect_windows_key(raw, decrypt=True)
    elif payload["method"] != "owner-only-0600" or os.name == "nt":
        raise RuntimeError("Unsupported backup key protection on this host")
    if len(raw) != 32:
        raise RuntimeError("Invalid backup encryption key")
    return raw


def docker_path() -> str:
    command = shutil.which("docker") or "C:/Program Files/Docker/Docker/resources/bin/docker.exe"
    if not Path(command).is_file():
        raise RuntimeError("Docker CLI unavailable")
    return command


def run_docker(*args: str) -> bytes:
    result = subprocess.run([docker_path(), *args], capture_output=True, check=False, timeout=30)
    if result.returncode:
        raise RuntimeError("Docker command failed; command output withheld")
    return result.stdout


def compose_ids(service: str) -> list[str]:
    output = run_docker(
        "compose",
        "--env-file",
        str(ROOT / ".env.acceptance"),
        "-p",
        PROJECT,
        "-f",
        str(ROOT / "docker-compose.yml"),
        "-f",
        str(ROOT / "docker-compose.acceptance.yml"),
        "ps",
        "--all",
        "--quiet",
        service,
    )
    return output.decode().split()


def check_stack() -> str:
    for service in ("api", "worker", "beat", "web"):
        for container in compose_ids(service):
            info = json.loads(run_docker("inspect", container))[0]
            if info["State"]["Running"]:
                raise RuntimeError("Stop acceptance api/worker/beat/web before the recovery drill")
    postgres_container = ""
    for service, container_port, host_port in (
        ("postgres", "5432/tcp", "15432"),
        ("minio", "9000/tcp", "19000"),
    ):
        containers = compose_ids(service)
        if len(containers) != 1:
            raise RuntimeError("One isolated acceptance database/storage container is required")
        info = json.loads(run_docker("inspect", containers[0]))[0]
        labels = info["Config"]["Labels"]
        bindings = info["NetworkSettings"]["Ports"].get(container_port)
        if (
            labels.get("com.docker.compose.project") != PROJECT
            or labels.get("com.docker.compose.service") != service
            or not info["State"]["Running"]
            or bindings != [{"HostIp": "127.0.0.1", "HostPort": host_port}]
        ):
            raise RuntimeError("Container is not the running isolated acceptance service")
        if service == "postgres":
            postgres_container = containers[0]
    return postgres_container


def configuration() -> tuple[dict[str, str], Any, Any]:
    values = {
        name: value
        for name, value in dotenv_values(ROOT / ".env.acceptance", interpolate=False).items()
        if value is not None
    }
    url = make_url(values["MIGRATION_DATABASE_URL"])
    storage_url = urlsplit(values["S3_ENDPOINT_URL"])
    if (
        url.drivername != "postgresql+psycopg"
        or url.query
        or url.host not in ("localhost", "127.0.0.1")
        or url.port != 15432
        or url.username != values.get("POSTGRES_USER", "knowledge_admin")
        or url.database != values.get("POSTGRES_DB", "knowledge_os")
        or storage_url.scheme != "http"
        or storage_url.hostname not in ("localhost", "127.0.0.1")
        or storage_url.port != 19000
        or storage_url.username is not None
        or storage_url.password is not None
        or storage_url.path not in ("", "/")
        or storage_url.query
        or storage_url.fragment
    ):
        raise RuntimeError("Recovery drill requires isolated loopback acceptance endpoints")
    if not url.database or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", url.database):
        raise RuntimeError("Invalid acceptance database name")
    engine = create_engine(url, connect_args={"connect_timeout": 3})
    storage = boto3.client(
        "s3",
        endpoint_url=values["S3_ENDPOINT_URL"],
        aws_access_key_id=values["S3_ACCESS_KEY"],
        aws_secret_access_key=values["S3_SECRET_KEY"],
        region_name=values.get("S3_REGION", "us-east-1"),
        config=Config(
            connect_timeout=3,
            read_timeout=10,
            retries={"max_attempts": 0},
            s3={"addressing_style": "path"},
        ),
    )
    return values, engine, storage


def quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def canonical_check(connection: Connection, relation: str, expression: str) -> str:
    # pg_dump can distribute an array cast across its elements. Reparse the
    # catalog expression with PostgreSQL rather than weakening a textual diff.
    view = "recovery_check_" + uuid4().hex
    connection.execute(
        text(
            f"CREATE TEMP VIEW {quoted(view)} AS SELECT ({expression}) "
            f"AS check_value FROM {relation}"
        )
    )
    try:
        return connection.execute(
            text("SELECT pg_get_viewdef(to_regclass(:view),false)"), {"view": "pg_temp." + view}
        ).scalar_one()
    finally:
        connection.execute(text(f"DROP VIEW pg_temp.{quoted(view)}"))


def trigger_fingerprint(connection: Connection, schema: str, table: str) -> list[list[Any]]:
    """Include guard behavior and execution privileges, never database-local OIDs."""
    return [
        list(row)
        for row in connection.execute(
            text(
                "SELECT t.tgname,t.tgenabled,pg_get_triggerdef(t.oid,false),"
                "pg_get_functiondef(p.oid),pg_get_userbyid(p.proowner),"
                "p.proacl::text,p.prosecdef,p.proconfig::text "
                "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "JOIN pg_proc p ON p.oid=t.tgfoid "
                "WHERE n.nspname=:schema AND c.relname=:table AND NOT t.tgisinternal "
                "ORDER BY t.tgname"
            ),
            {"schema": schema, "table": table},
        ).all()
    ]


def database_fingerprint(connection: Connection) -> dict[str, Any]:
    connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    connection.execute(text("SET LOCAL statement_timeout = '30s'"))
    connection.execute(text("SET LOCAL temp_file_limit = '512MB'"))
    tables = connection.execute(
        text(
            "SELECT schemaname,tablename FROM pg_tables "
            "WHERE schemaname IN ('public','acceptance_checks') "
            "ORDER BY schemaname,tablename"
        )
    ).all()
    result = {}
    for schema, table in tables:
        relation = quoted(schema) + "." + quoted(table)
        rows = connection.execute(
            text(f"SELECT row_to_json(t)::text FROM {relation} t ORDER BY row_to_json(t)::text"),
            execution_options={"stream_results": True},
        )
        digest, count = hashlib.sha256(), 0
        try:
            for row in rows:
                raw = row[0].encode()
                digest.update(len(raw).to_bytes(8, "big") + raw)
                count += 1
        finally:
            rows.close()
        security = connection.execute(
            text(
                "SELECT c.relrowsecurity,c.relforcerowsecurity,"
                "pg_get_userbyid(c.relowner),c.relacl::text "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=:schema AND c.relname=:table"
            ),
            {"schema": schema, "table": table},
        ).one()
        # Column grants are independent of relacl. In particular, note_runs
        # deliberately grants UPDATE only on status/error_code; widening that
        # permission must fail recovery verification even when table ACLs match.
        column_privileges = connection.execute(
            text(
                "SELECT a.attname,pg_get_userbyid(x.grantor),"
                "CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END,"
                "x.privilege_type,x.is_grantable "
                "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "CROSS JOIN LATERAL aclexplode(a.attacl) x "
                "WHERE n.nspname=:schema AND c.relname=:table "
                "AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,2,3,4,5"
            ),
            {"schema": schema, "table": table},
        ).all()
        policies = list(
            connection.execute(
                text(
                    "SELECT policyname,permissive,roles::text,cmd,qual,with_check "
                    "FROM pg_policies WHERE schemaname=:schema AND tablename=:table "
                    "ORDER BY policyname"
                ),
                {"schema": schema, "table": table},
            ).all()
        )
        constraints = list(
            connection.execute(
                text(
                    "SELECT c.conname,c.contype,c.convalidated,c.condeferrable,c.condeferred,"
                    "pg_get_constraintdef(c.oid,true),pg_get_expr(c.conbin,c.conrelid,false) "
                    "FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
                    "JOIN pg_namespace n ON n.oid=t.relnamespace "
                    "WHERE n.nspname=:schema AND t.relname=:table ORDER BY c.conname"
                ),
                {"schema": schema, "table": table},
            ).all()
        )
        result[f"{schema}.{table}"] = {
            "rows": count,
            "sha256": digest.hexdigest(),
            "rls": list(security),
            "column_privileges": [list(row) for row in column_privileges],
            "triggers": trigger_fingerprint(connection, schema, table),
            "policies": [
                [
                    *row[:4],
                    *[
                        canonical_check(connection, relation, expression)
                        if expression is not None
                        else None
                        for expression in row[4:]
                    ],
                ]
                for row in policies
            ],
            "constraints": [
                [
                    *row[:5],
                    canonical_check(connection, relation, row[6]) if row[1] == "c" else row[5],
                ]
                for row in constraints
            ],
        }
    return result


@contextmanager
def postgres_dump(
    container: str, username: str, database: str, snapshot: str
) -> Iterator[BinaryIO]:
    process = subprocess.Popen(
        [
            docker_path(),
            "exec",
            container,
            "pg_dump",
            "--format=custom",
            "--no-password",
            "--lock-wait-timeout=10s",
            "--username",
            username,
            "--dbname",
            database,
            "--snapshot",
            snapshot,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert process.stdout is not None
        yield process.stdout
        process.stdout.close()
        if process.wait(timeout=30):
            raise RuntimeError("PostgreSQL dump failed; partial encrypted archive retained")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def restore_database(
    container: str, username: str, database: str, archive: Path, key: bytes
) -> None:
    # Authenticate the COMPLETE archive before running any pg_restore SQL.
    decrypt_stream(archive, key)
    process = subprocess.Popen(
        [
            docker_path(),
            "exec",
            "-i",
            container,
            "pg_restore",
            "--exit-on-error",
            "--no-password",
            "--username",
            username,
            "--dbname",
            database,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert process.stdin is not None
        decrypt_stream(archive, key, process.stdin)
        process.stdin.close()
        if process.wait(timeout=60):
            raise RuntimeError("PostgreSQL restore failed; new recovery database retained")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def verify_blob(path: Path, key: bytes, expected: dict[str, Any]) -> None:
    if decrypt_stream(path, key) != {name: expected[name] for name in ("size", "sha256", "file")}:
        raise RuntimeError("Backup content checksum mismatch")


def drill(*, archive_only: bool = False) -> Path:
    container = check_stack()
    values, engine, storage = configuration()
    source_bucket = values.get("S3_BUCKET", "knowledge-os")
    run_id = uuid4().hex
    directory = ROOT / ".cache" / "recovery-drills" / run_id
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    key = secrets.token_bytes(32)
    save_key(directory / "recovery-key.json", key)
    source_database = engine.url.database
    username = values.get("POSTGRES_USER", "knowledge_admin")
    target_database = "knowledge_os_recovery_" + run_id[:12]
    target_bucket = "knowledge-os-recovery-" + run_id[:20]
    marker_key = "acceptance/backup-recovery/" + run_id
    marker = secrets.token_hex(24)
    manifest: dict[str, Any] = {
        "format": 1,
        "project": PROJECT,
        "created_at": datetime.now(UTC).isoformat(),
        "source_database": source_database,
        "source_bucket": source_bucket,
        "target_database": target_database,
        "target_bucket": target_bucket,
        "marker_id": run_id,
        "marker_key": marker_key,
        "objects": [],
    }
    database_marker_created = False
    object_marker_attempted = False
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS acceptance_checks"))
            connection.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS acceptance_checks.recovery_markers "
                    "(id text PRIMARY KEY,value text NOT NULL)"
                )
            )
            connection.execute(
                text("INSERT INTO acceptance_checks.recovery_markers VALUES (:id,:value)"),
                {"id": run_id, "value": marker},
            )
        database_marker_created = True
        object_marker_attempted = True
        storage.put_object(
            Bucket=source_bucket, Key=marker_key, Body=marker.encode(), ContentType="text/plain"
        )
        with (
            engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection,
            connection.begin(),
        ):
            snapshot = connection.execute(text("SELECT pg_export_snapshot()")).scalar_one()
            manifest["database_fingerprint"] = database_fingerprint(connection)
            with postgres_dump(container, username, source_database, snapshot) as stream:
                manifest["database_archive"] = encrypt_stream(
                    stream, directory / "postgres.dump.enc", key, MAX_DUMP
                )
            total = 0
            for page in storage.get_paginator("list_objects_v2").paginate(Bucket=source_bucket):
                for item in page.get("Contents", []):
                    total += item["Size"]
                    if total > MAX_OBJECT_TOTAL or len(manifest["objects"]) >= MAX_OBJECTS:
                        raise RuntimeError("Object backup resource limit exceeded")
                    response = storage.get_object(
                        Bucket=source_bucket, Key=item["Key"], IfMatch=item["ETag"]
                    )
                    try:
                        record = encrypt_stream(
                            response["Body"],
                            directory / f"object-{len(manifest['objects']):05}.enc",
                            key,
                            MAX_OBJECT_TOTAL,
                        )
                        if record["size"] != item["Size"]:
                            raise RuntimeError("Source object changed during backup")
                        record.update(
                            key=item["Key"],
                            content_type=response.get("ContentType", "application/octet-stream"),
                        )
                        manifest["objects"].append(record)
                    finally:
                        response["Body"].close()
            if not any(item["key"] == marker_key for item in manifest["objects"]):
                raise RuntimeError("Recovery marker missing from object archive")
        manifest_archive = encrypt_stream(
            io.BytesIO(json.dumps(manifest, sort_keys=True).encode()),
            directory / "manifest.json.enc",
            key,
            CHUNK * 8,
        )
        # Verify ciphertext authentication/checksums before creating recovery targets.
        key = load_key(directory / "recovery-key.json")
        verify_blob(directory / "manifest.json.enc", key, manifest_archive)
        verify_blob(
            directory / manifest["database_archive"]["file"], key, manifest["database_archive"]
        )
        for item in manifest["objects"]:
            verify_blob(directory / item["file"], key, item)
        if archive_only:
            # A cross-instance consumer restores the authenticated archive itself.
            # Do not create unnecessary same-instance targets or report recovery.
            with private_create(directory / "capture.json") as target:
                target.write(
                    json.dumps({"status": "captured", "recovery_verified": False}).encode()
                )
            return directory
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text(f"CREATE DATABASE {quoted(target_database)}"))
        restore_database(container, username, target_database, directory / "postgres.dump.enc", key)
        target_engine = create_engine(
            engine.url.set(database=target_database), connect_args={"connect_timeout": 3}
        )
        try:
            with target_engine.begin() as connection:
                if database_fingerprint(connection) != manifest["database_fingerprint"]:
                    raise RuntimeError("Restored database data or RLS policies differ")
        finally:
            target_engine.dispose()
        # A new bucket has no public policy; object bytes are authenticated before upload.
        storage.create_bucket(Bucket=target_bucket)
        for item in manifest["objects"]:
            buffer = io.BytesIO()
            decrypt_stream(directory / item["file"], key, buffer)
            buffer.seek(0)
            storage.put_object(
                Bucket=target_bucket,
                Key=item["key"],
                Body=buffer,
                ContentLength=item["size"],
                ContentType=item["content_type"],
            )
            response = storage.get_object(Bucket=target_bucket, Key=item["key"])
            digest, count = hashlib.sha256(), 0
            try:
                while block := response["Body"].read(CHUNK):
                    digest.update(block)
                    count += len(block)
            finally:
                response["Body"].close()
            if count != item["size"] or digest.hexdigest() != item["sha256"]:
                raise RuntimeError("Restored S3 object checksum mismatch")
        anonymous = httpx.get(
            values["S3_ENDPOINT_URL"].rstrip("/")
            + "/"
            + target_bucket
            + "/"
            + quote(marker_key, safe="/"),
            timeout=10,
            trust_env=False,
        )
        if anonymous.status_code != 403:
            raise RuntimeError("Recovery bucket did not reject anonymous object access")
        summary = {
            "format": 1,
            "completed_at": datetime.now(UTC).isoformat(),
            "status": "passed",
            "database_tables_verified": len(manifest["database_fingerprint"]),
            "objects_verified": len(manifest["objects"]),
            "database_checksum_verified": True,
            "encrypted_manifest_verified": True,
            "rls_policy_restore_verified": True,
            "table_constraints_restore_verified": True,
            "trigger_functions_restore_verified": True,
            "object_checksum_verified": True,
            "anonymous_access_denied": True,
            "target_database": target_database,
            "target_bucket": target_bucket,
            "original_data_retained": True,
            "production_recovery_verified": False,
        }
    finally:
        # Remove ONLY this run's dedicated source markers. Retain archives, failed
        # restore targets and all existing source data; never DROP/TRUNCATE or delete volumes.
        primary_failure = sys.exc_info()[0] is not None
        cleanup_failure = False
        if database_marker_created:
            try:
                with engine.begin() as connection:
                    connection.execute(
                        text("DELETE FROM acceptance_checks.recovery_markers WHERE id=:id"),
                        {"id": run_id},
                    )
            except Exception:
                cleanup_failure = True
        if object_marker_attempted:
            try:
                storage.delete_object(Bucket=source_bucket, Key=marker_key)
            except Exception:
                cleanup_failure = True
        engine.dispose()
        if cleanup_failure:
            if primary_failure:
                print("Source marker cleanup also failed; retained for review", file=sys.stderr)
            else:
                raise RuntimeError("Source marker cleanup failed; artifacts/targets retained")
    with private_create(directory / "summary.json") as target:
        target.write(json.dumps(summary, indent=2).encode())
    return directory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--acceptance", action="store_true", required=True, help="Confirm isolated acceptance drill"
    )
    parser.parse_args()
    try:
        destination = drill()
    except Exception as exc:
        # No URLs, passwords, SQL values, object names or raw subprocess output.
        raise SystemExit(
            f"Recovery drill failed ({type(exc).__name__}); artifacts/targets retained"
        ) from None
    print(f"PASS: encrypted acceptance recovery verified; artifacts: {destination}")


if __name__ == "__main__":
    main()
