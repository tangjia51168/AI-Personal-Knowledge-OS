"""Real archive crypto and offline input boundaries; no service or credentials needed."""

import copy
import io
import json
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidTag

from scripts import audit_archive as archive


def payload():
    actor = str(uuid4())
    return {
        "format": 1,
        "scope": "space-business-and-own-account",
        "consistency": "postgresql-repeatable-read",
        "snapshot_at": "2026-11-07T00:00:00+00:00",
        "tenant_id": str(uuid4()),
        "user_id": actor,
        "business_events": [
            {
                "id": str(uuid4()),
                "actor_id": actor,
                "action": "space.created",
                "resource_id": str(uuid4()),
                "created_at": "2026-11-06T22:00:00+00:00",
            },
            {
                "id": str(uuid4()),
                "actor_id": actor,
                "action": "settings.updated",
                "resource_id": str(uuid4()),
                "created_at": "2026-11-06T23:00:00+00:00",
            },
        ],
        "account_events": [
            {
                "id": str(uuid4()),
                "action": "account.registered",
                "created_at": "2026-11-06T22:00:00+00:00",
            }
        ],
    }


def encrypted_bytes(directory, raw):
    directory.mkdir(mode=0o700)
    key = b"archive-unit-test-key".ljust(32, b"_")
    archive.save_key(directory / archive.KEY_FILE, key)
    archive.encrypt_stream(
        io.BytesIO(raw), directory / archive.ARCHIVE_FILE, key, archive.MAX_ARCHIVE_BYTES
    )
    return key


def test_archive_roundtrip_is_encrypted_and_exclusive(tmp_path):
    source = payload()
    directory = tmp_path / "snapshot"
    summary = archive.write_archive(source, directory)
    verified = archive.verify_archive(directory)
    assert summary["business_events"] == verified["business_events"] == 2
    assert summary["account_events"] == verified["account_events"] == 1
    assert summary["sha256"] == verified["sha256"]
    assert summary["source_records_deleted"] is False
    assert summary["production_verified"] is False
    assert verified["independent_archive_verified"] is False
    ciphertext = (directory / archive.ARCHIVE_FILE).read_bytes()
    assert source["tenant_id"].encode() not in ciphertext
    assert b"settings.updated" not in ciphertext
    assert b"business_events" not in ciphertext
    assert source["tenant_id"] not in json.dumps(summary)
    assert {path.name for path in directory.iterdir()} == {
        archive.ARCHIVE_FILE,
        archive.KEY_FILE,
        "summary.json",
    }
    with pytest.raises(FileExistsError):
        archive.write_archive(source, directory)


@pytest.mark.parametrize("damage", ["ciphertext", "truncated", "wrong-key"])
def test_archive_rejects_damage_before_parsing_any_plaintext(tmp_path, monkeypatch, damage):
    directory = tmp_path / "damaged"
    encrypted_bytes(directory, json.dumps(payload()).encode())
    path = directory / archive.ARCHIVE_FILE
    original = path.read_bytes()
    if damage == "ciphertext":
        damaged = bytearray(original)
        damaged[len(archive.MAGIC) + 20] ^= 1
        path.write_bytes(damaged)
    elif damage == "truncated":
        path.write_bytes(original[:-4])
    else:
        alternative = tmp_path / "alternative-key.json"
        archive.save_key(alternative, b"different-archive-key".ljust(32, b"!"))
        (directory / archive.KEY_FILE).write_bytes(alternative.read_bytes())

    def forbidden_parser(_pairs):
        pytest.fail("Unauthenticated plaintext reached the JSON parser")

    monkeypatch.setattr(archive, "_unique_json", forbidden_parser)
    with pytest.raises((InvalidTag, RuntimeError)):
        archive.verify_archive(directory)


def test_authenticated_archive_still_rejects_sensitive_or_duplicate_fields(tmp_path):
    source = payload()
    source["business_events"][0]["api_key"] = "must-not-be-exported"
    directory = tmp_path / "extra-field"
    encrypted_bytes(directory, json.dumps(source).encode())
    with pytest.raises(ValueError, match="event"):
        archive.verify_archive(directory)
    duplicate = tmp_path / "duplicate-field"
    raw = json.dumps(payload()).replace('"format": 1', '"format": 1, "format": 1', 1)
    encrypted_bytes(duplicate, raw.encode())
    with pytest.raises(ValueError, match="Duplicate audit archive field"):
        archive.verify_archive(duplicate)


def test_archive_schema_rejects_invalid_actions_identity_and_order():
    cases = []
    extra_header = payload()
    extra_header["password"] = "private"
    cases.append(extra_header)
    action = payload()
    action["account_events"][0]["action"] = "arbitrary-secret"
    cases.append(action)
    identity = payload()
    identity["tenant_id"] = "untrusted-tenant"
    cases.append(identity)
    naive_time = payload()
    naive_time["snapshot_at"] = "2026-11-07T00:00:00"
    cases.append(naive_time)
    non_utc = payload()
    non_utc["account_events"][0]["created_at"] = "2026-11-06T22:00:00+08:00"
    cases.append(non_utc)
    duplicate = payload()
    duplicate["business_events"].append(copy.deepcopy(duplicate["business_events"][0]))
    cases.append(duplicate)
    unordered = payload()
    unordered["business_events"].reverse()
    cases.append(unordered)
    boolean_version = payload()
    boolean_version["format"] = True
    cases.append(boolean_version)
    for invalid in cases:
        with pytest.raises(ValueError):
            archive.validate_payload(invalid)


def test_archive_resource_limits_fail_without_creating_output(tmp_path, monkeypatch):
    source = payload()
    monkeypatch.setattr(archive, "MAX_EXPORT_ROWS", 2)
    with pytest.raises(ValueError, match="row limit"):
        archive.write_archive(source, tmp_path / "too-many")
    assert not (tmp_path / "too-many").exists()
    monkeypatch.setattr(archive, "MAX_EXPORT_ROWS", 10_000)
    monkeypatch.setattr(archive, "MAX_ARCHIVE_BYTES", 8)
    with pytest.raises(ValueError, match="byte limit"):
        archive.write_archive(source, tmp_path / "too-large")
    assert not (tmp_path / "too-large").exists()


def test_oversized_ciphertext_or_key_is_rejected_before_loading_key(tmp_path, monkeypatch):
    directory = tmp_path / "oversized"
    directory.mkdir()
    encrypted = directory / archive.ARCHIVE_FILE
    key_file = directory / archive.KEY_FILE
    encrypted.write_bytes(b"x" * 200)
    key_file.write_bytes(b"key-placeholder")
    monkeypatch.setattr(archive, "MAX_ARCHIVE_BYTES", 32)

    def forbidden_key(_path):
        pytest.fail("Oversized input reached key loading")

    monkeypatch.setattr(archive, "load_key", forbidden_key)
    with pytest.raises(ValueError, match="oversized"):
        archive.verify_archive(directory)
    encrypted.write_bytes(b"x")
    key_file.write_bytes(b"x" * 16_385)
    with pytest.raises(ValueError, match="oversized"):
        archive.verify_archive(directory)


def test_export_rejects_nonacceptance_database_before_docker_or_network(monkeypatch):
    invalid = [
        "postgresql+psycopg://knowledge_app:private@remote.invalid:15432/knowledge_os",
        "postgresql+psycopg://knowledge_app:private@localhost:5432/knowledge_os",
        "postgresql+psycopg://knowledge_app:private@localhost:15432/production",
        "postgresql+psycopg://knowledge_admin:private@localhost:15432/knowledge_os",
        "postgresql+psycopg://knowledge_app@localhost:15432/knowledge_os",
        "postgresql+psycopg://knowledge_app:private@localhost:15432/knowledge_os?host=remote",
    ]

    def forbidden_docker(_service):
        pytest.fail("Rejected database reached Docker or the network")

    monkeypatch.setattr(archive, "compose_ids", forbidden_docker)
    for database in invalid:
        monkeypatch.setattr(
            archive,
            "dotenv_values",
            lambda *args, database=database, **kwargs: {"DATABASE_URL": database},
        )
        with pytest.raises(RuntimeError, match="isolated application database"):
            archive.acceptance_database()


def test_export_rejects_wrong_container_binding_or_verification_policy(monkeypatch):
    database = "postgresql+psycopg://knowledge_app:local-fixture@localhost:15432/knowledge_os"
    monkeypatch.setattr(
        archive, "dotenv_values", lambda *args, **kwargs: {"DATABASE_URL": database}
    )
    monkeypatch.setattr(archive, "compose_ids", lambda service: [service])
    postgres = {
        "State": {"Running": True},
        "Config": {
            "Labels": {
                "com.docker.compose.project": "knowledge-os-acceptance",
                "com.docker.compose.service": "postgres",
            }
        },
        "NetworkSettings": {"Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "15432"}]}},
    }
    api = {
        "State": {"Running": True},
        "Config": {
            "Labels": {
                "com.docker.compose.project": "knowledge-os-acceptance",
                "com.docker.compose.service": "api",
            },
            "Env": ["EMAIL_VERIFICATION_REQUIRED=false"],
        },
    }
    active = {"postgres": postgres, "api": api}
    monkeypatch.setattr(
        archive, "run_docker", lambda _command, service: json.dumps([active[service]]).encode()
    )
    assert archive.acceptance_database() == database
    for violation in ("project", "binding", "stopped", "verification"):
        active = {"postgres": copy.deepcopy(postgres), "api": copy.deepcopy(api)}
        if violation == "project":
            active["postgres"]["Config"]["Labels"]["com.docker.compose.project"] = "production"
        elif violation == "binding":
            active["postgres"]["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostIp"] = "0.0.0.0"
        elif violation == "stopped":
            active["postgres"]["State"]["Running"] = False
        else:
            active["api"]["Config"]["Env"] = ["EMAIL_VERIFICATION_REQUIRED=true"]
        with pytest.raises(RuntimeError):
            archive.acceptance_database()


def test_cli_failure_hides_dependency_exception_and_does_not_print_secret(monkeypatch, capsys):
    monkeypatch.setattr(archive.sys, "argv", ["audit_archive", "verify", "--directory", "unused"])

    def rejected(_directory):
        raise RuntimeError("postgresql://private-user:private-key@private-host/private-db")

    monkeypatch.setattr(archive, "verify_archive", rejected)
    with pytest.raises(SystemExit) as error:
        archive.main()
    assert str(error.value) == "Audit archive failed (RuntimeError); partial ciphertext retained"
    assert "private" not in str(error.value)
    assert capsys.readouterr().out == ""
