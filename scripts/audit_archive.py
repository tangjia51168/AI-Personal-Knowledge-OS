"""Encrypted, read-only audit export for the dedicated local acceptance stack."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import secrets
import sys
from datetime import UTC, datetime
from getpass import getpass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.modules.audit.export import MAX_EXPORT_ROWS, audit_snapshot
from app.modules.audit.models import AuditAction
from app.modules.identity.security_events import SecurityAction
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import dotenv_values
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from scripts.backup_recovery import (
    MAGIC,
    compose_ids,
    encrypt_stream,
    load_key,
    private_create,
    run_docker,
    save_key,
)

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = ROOT / ".cache" / "audit-archives"
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
ARCHIVE_FILE = "audit.json.enc"
KEY_FILE = "archive-key.json"
HEADER_FIELDS = {
    "format",
    "scope",
    "consistency",
    "snapshot_at",
    "tenant_id",
    "user_id",
    "business_events",
    "account_events",
}


def _uuid(value: Any) -> None:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Invalid audit archive UUID")


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Invalid audit archive timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(None):
        raise ValueError("Audit archive timestamps require UTC")
    return parsed


def validate_payload(payload: Any) -> dict[str, int]:
    if (
        not isinstance(payload, dict)
        or set(payload) != HEADER_FIELDS
        or type(payload["format"]) is not int
        or payload["format"] != 1
        or payload["scope"] != "space-business-and-own-account"
        or payload["consistency"] != "postgresql-repeatable-read"
    ):
        raise ValueError("Invalid audit archive schema")
    _uuid(payload["tenant_id"])
    _uuid(payload["user_id"])
    _timestamp(payload["snapshot_at"])
    count = 0
    counts = {}
    for name, actions, fields in (
        (
            "business_events",
            {a.value for a in AuditAction},
            {"id", "actor_id", "action", "resource_id", "created_at"},
        ),
        ("account_events", {a.value for a in SecurityAction}, {"id", "action", "created_at"}),
    ):
        rows = payload[name]
        if not isinstance(rows, list):
            raise ValueError("Invalid audit archive events")
        count += len(rows)
        if count > MAX_EXPORT_ROWS:
            raise ValueError("Audit archive row limit exceeded")
        seen = set()
        previous = None
        for row in rows:
            if not isinstance(row, dict) or set(row) != fields or row["action"] not in actions:
                raise ValueError("Invalid audit archive event")
            for field in fields - {"action", "created_at"}:
                _uuid(row[field])
            ordering = (_timestamp(row["created_at"]), UUID(row["id"]))
            if row["id"] in seen or previous is not None and ordering <= previous:
                raise ValueError("Duplicate or unordered audit archive event")
            previous = ordering
            seen.add(row["id"])
        counts[name] = len(rows)
    return counts


def _unique_json(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate audit archive field")
        result[key] = value
    return result


def write_archive(payload: dict[str, Any], directory: Path) -> dict[str, Any]:
    counts = validate_payload(payload)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError("Audit archive byte limit exceeded")
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    key = secrets.token_bytes(32)
    save_key(directory / KEY_FILE, key)
    digest = encrypt_stream(io.BytesIO(raw), directory / ARCHIVE_FILE, key, MAX_ARCHIVE_BYTES)
    verified = verify_archive(directory)
    if any(verified[name] != count for name, count in counts.items()):
        raise RuntimeError("Audit archive verification mismatch")
    summary = {
        "status": "passed",
        "completed_at": datetime.now(UTC).isoformat(),
        **counts,
        "plaintext_bytes": digest["size"],
        "sha256": digest["sha256"],
        "encryption_verified": True,
        "source_records_deleted": False,
        "scope": "space-business-and-own-account",
        "consistency": "postgresql-repeatable-read",
        "independent_archive_verified": False,
        "production_verified": False,
    }
    with private_create(directory / "summary.json") as target:
        target.write(json.dumps(summary, indent=2).encode())
    return summary


def verify_archive(directory: Path) -> dict[str, Any]:
    """Authenticate the complete ciphertext before consuming any decrypted JSON."""
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Audit archive directory required")
    encrypted, key_file = directory / ARCHIVE_FILE, directory / KEY_FILE
    if (
        encrypted.is_symlink()
        or key_file.is_symlink()
        or not encrypted.is_file()
        or not key_file.is_file()
        or encrypted.stat().st_size > MAX_ARCHIVE_BYTES + len(MAGIC) + 28
        or key_file.stat().st_size > 16_384
    ):
        raise ValueError("Invalid or oversized audit archive")
    key = load_key(key_file)
    # Bound the actual read as well as stat, then authenticate all bytes before
    # exposing any plaintext to the JSON parser. No plaintext file is created.
    ceiling = MAX_ARCHIVE_BYTES + len(MAGIC) + 28
    with encrypted.open("rb") as source:
        sealed = source.read(ceiling + 1)
    if len(sealed) > ceiling or len(sealed) < len(MAGIC) + 28 or not sealed.startswith(MAGIC):
        raise ValueError("Invalid or oversized audit archive")
    start = len(MAGIC)
    raw = AESGCM(key).decrypt(sealed[start : start + 12], sealed[start + 12 :], MAGIC)
    payload = json.loads(raw, object_pairs_hook=_unique_json)
    counts = validate_payload(payload)
    return {
        "status": "passed",
        **counts,
        "plaintext_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "encryption_verified": True,
        "independent_archive_verified": False,
    }


def acceptance_database() -> str:
    values = dotenv_values(ROOT / ".env.acceptance", interpolate=False)
    database = values.get("DATABASE_URL")
    if not database:
        raise RuntimeError("Acceptance application database is missing")
    url = make_url(database)
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in ("localhost", "127.0.0.1")
        or url.port != 15432
        or url.username != "knowledge_app"
        or not url.password
        or url.query
        or url.database != values.get("POSTGRES_DB", "knowledge_os")
    ):
        raise RuntimeError("Audit export requires the isolated application database")
    containers = compose_ids("postgres")
    if len(containers) != 1:
        raise RuntimeError("One isolated acceptance database is required")
    info = json.loads(run_docker("inspect", containers[0]))[0]
    labels = info["Config"]["Labels"]
    if (
        labels.get("com.docker.compose.project") != "knowledge-os-acceptance"
        or labels.get("com.docker.compose.service") != "postgres"
        or not info["State"]["Running"]
        or info["NetworkSettings"]["Ports"].get("5432/tcp")
        != [{"HostIp": "127.0.0.1", "HostPort": "15432"}]
    ):
        raise RuntimeError("Database is not the isolated acceptance service")
    # Match the running API's verification policy, without reading its secret config.
    apis = compose_ids("api")
    if len(apis) != 1:
        raise RuntimeError("One isolated acceptance API is required")
    api = json.loads(run_docker("inspect", apis[0]))[0]
    environment = api["Config"]["Env"]
    if (
        not api["State"]["Running"]
        or api["Config"]["Labels"].get("com.docker.compose.project") != "knowledge-os-acceptance"
        or api["Config"]["Labels"].get("com.docker.compose.service") != "api"
        or "EMAIL_VERIFICATION_REQUIRED=false" not in environment
    ):
        raise RuntimeError("Audit CLI requires the unchanged local verification policy")
    return database


def export_acceptance(tenant_id: UUID, token: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise ValueError("A valid session token is required")
    database = acceptance_database()
    engine = create_engine(database, hide_parameters=True, connect_args={"connect_timeout": 3})
    try:
        with Session(engine) as session, session.begin():
            payload = audit_snapshot(session, token, tenant_id)
    finally:
        engine.dispose()
    directory = ARCHIVE_ROOT / uuid4().hex
    write_archive(payload, directory)
    return directory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--acceptance", action="store_true", required=True)
    export.add_argument("--tenant-id", type=UUID, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "export":
            token = (
                getpass("Session token (hidden): ")
                if sys.stdin.isatty()
                else sys.stdin.readline(256).strip()
            )
            directory = export_acceptance(args.tenant_id, token)
            print(f"PASS: encrypted audit snapshot; local directory: {directory}")
        else:
            result = verify_archive(args.directory)
            print(json.dumps(result, indent=2))
    except Exception as error:
        raise SystemExit(
            f"Audit archive failed ({type(error).__name__}); partial ciphertext retained"
        ) from None


if __name__ == "__main__":
    main()
