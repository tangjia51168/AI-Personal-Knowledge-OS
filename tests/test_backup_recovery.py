import importlib.util
import io
import json

import pytest
from cryptography.exceptions import InvalidTag


def load_module():
    spec = importlib.util.spec_from_file_location("backup_recovery", "scripts/backup_recovery.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_encrypted_archive_authenticates_content_and_rejects_tamper(tmp_path):
    backup = load_module()
    path = tmp_path / "archive.enc"
    key = b"a" * 32
    original = b"private acceptance backup" * 100
    expected = backup.encrypt_stream(io.BytesIO(original), path, key, 10000)
    assert original not in path.read_bytes()
    target = io.BytesIO()
    assert backup.decrypt_stream(path, key, target) == expected
    assert target.getvalue() == original
    with pytest.raises(InvalidTag):
        backup.decrypt_stream(path, b"b" * 32)
    altered = bytearray(path.read_bytes())
    altered[len(backup.MAGIC) + 20] ^= 1
    path.write_bytes(altered)
    with pytest.raises(InvalidTag):
        backup.decrypt_stream(path, key)


def test_backup_is_exclusive_and_resource_bounded(tmp_path):
    backup = load_module()
    path = tmp_path / "bounded.enc"
    with pytest.raises(RuntimeError, match="resource limit"):
        backup.encrypt_stream(io.BytesIO(b"too large"), path, b"a" * 32, 3)
    with pytest.raises(FileExistsError):
        backup.encrypt_stream(io.BytesIO(b"safe"), path, b"a" * 32, 10)


def test_key_is_protected_and_reusable(tmp_path):
    backup = load_module()
    path = tmp_path / "key.json"
    key = b"private-32-byte-test-key".ljust(32, b"_")
    backup.save_key(path, key)
    assert backup.load_key(path) == key
    with pytest.raises(FileExistsError):
        backup.save_key(path, key)


@pytest.mark.parametrize(
    ("database_url", "storage_url"),
    [
        ("postgresql+psycopg://knowledge_admin@remote.example:15432/knowledge_os", None),
        ("postgresql+psycopg://knowledge_admin@localhost:5432/knowledge_os", None),
        ("postgresql+psycopg://knowledge_admin@localhost:15432/production", None),
        ("postgresql+psycopg://other@localhost:15432/knowledge_os", None),
        ("postgresql+psycopg://knowledge_admin@localhost:15432/knowledge_os?host=remote", None),
        (None, "http://remote.example:19000"),
        (None, "http://localhost:9000"),
        (None, "http://localhost:19000/other"),
        (None, "http://user:secret@localhost:19000"),
        (None, "http://localhost:19000?redirect=remote"),
    ],
)
def test_configuration_rejects_targets_outside_acceptance(monkeypatch, database_url, storage_url):
    backup = load_module()
    values = {
        "MIGRATION_DATABASE_URL": database_url
        or "postgresql+psycopg://knowledge_admin@localhost:15432/knowledge_os",
        "S3_ENDPOINT_URL": storage_url or "http://localhost:19000",
    }
    monkeypatch.setattr(backup, "dotenv_values", lambda *args, **kwargs: values)
    with pytest.raises(RuntimeError, match="isolated loopback acceptance"):
        backup.configuration()


@pytest.mark.parametrize("violation", ["project", "port", "running-writer"])
def test_stack_rejects_wrong_container_or_active_writers(monkeypatch, violation):
    backup = load_module()

    def ids(service):
        return [service] if service in ("postgres", "minio", "api") else []

    def inspect(_command, service):
        port, host_port = ("5432/tcp", "15432") if service == "postgres" else ("9000/tcp", "19000")
        info = {
            "State": {"Running": service != "api" or violation == "running-writer"},
            "Config": {
                "Labels": {
                    "com.docker.compose.project": backup.PROJECT,
                    "com.docker.compose.service": service,
                }
            },
            "NetworkSettings": {"Ports": {port: [{"HostIp": "127.0.0.1", "HostPort": host_port}]}},
        }
        if service == "minio" and violation == "project":
            info["Config"]["Labels"]["com.docker.compose.project"] = "production"
        if service == "minio" and violation == "port":
            info["NetworkSettings"]["Ports"][port][0]["HostIp"] = "0.0.0.0"
        return json.dumps([info]).encode()

    monkeypatch.setattr(backup, "compose_ids", ids)
    monkeypatch.setattr(backup, "run_docker", inspect)
    with pytest.raises(RuntimeError):
        backup.check_stack()
