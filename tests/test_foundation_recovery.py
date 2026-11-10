"""Recovery orchestration guardrails; real restore evidence is a separate CLI run."""

import io
import json
from unittest.mock import Mock

import pytest

from scripts import foundation_recovery as recovery


def test_loopback_port_requires_one_explicit_binding(monkeypatch):
    def configure(bindings):
        monkeypatch.setattr(
            recovery, "inspect", lambda _: {"NetworkSettings": {"Ports": {"5432/tcp": bindings}}}
        )

    for bindings in (
        [],
        None,
        [{"HostIp": "0.0.0.0", "HostPort": "1234"}],
        [{"HostIp": "127.0.0.1", "HostPort": "1234"}] * 2,
    ):
        configure(bindings)
        with pytest.raises((RuntimeError, TypeError)):
            recovery.published_port("generated", "5432/tcp")
    configure([{"HostIp": "127.0.0.1", "HostPort": "1234"}])
    assert recovery.published_port("generated", "5432/tcp") == 1234


def test_target_stop_never_stops_container_with_wrong_ownership(monkeypatch):
    target = object.__new__(recovery.Target)
    target.name = "kos-recovery-" + "a" * 32
    target.containers = [target.name + "-api"]
    monkeypatch.setattr(recovery, "inspect", lambda _: {"Config": {"Labels": {}}})
    command = Mock()
    monkeypatch.setattr(recovery, "docker", command)
    with pytest.raises(RuntimeError, match="stop failed"):
        target.stop()
    command.assert_not_called()


def test_target_uses_immutable_image_ids_separate_networks_and_scoped_env(monkeypatch):
    commands, processes = [], []
    monkeypatch.setattr(recovery, "docker", lambda *args, **kw: commands.append(args) or b"")
    monkeypatch.setattr(
        recovery.subprocess,
        "run",
        lambda args, **kw: processes.append((args, kw)) or Mock(returncode=0),
    )
    images = {
        name: {"Image": "sha256:immutable", "Config": {"User": "knowledge"}}
        for name in ("api", "worker", "postgres")
    }
    target = recovery.Target("a" * 32, images)
    target.start("api", ["uvicorn"], {"S3_SECRET_KEY": "private-sentinel"}, port="8000")
    target.start("worker", ["celery"], {"S3_SECRET_KEY": "private-sentinel"})
    for args, kwargs in processes:
        assert "private-sentinel" not in " ".join(args)
        assert kwargs["env"]["S3_SECRET_KEY"] == "private-sentinel"
        assert "--read-only" in args and "--pull=never" in args
        assert "sha256:immutable" in args
    api_args, worker_args = processes[0][0], processes[1][0]
    assert api_args[api_args.index("--network") + 1] == target.name + "-operator"
    assert worker_args[worker_args.index("--network") + 1] == target.name
    assert "--publish" not in worker_args
    assert any(item[:2] == ("network", "connect") for item in commands)


def test_restored_objects_require_exact_digest_and_close_body(monkeypatch, tmp_path):
    body = io.BytesIO(b"changed")
    storage = Mock()
    storage.get_object.return_value = {"Body": body}
    monkeypatch.setattr(
        recovery.backup, "decrypt_stream", lambda path, key, target: target.write(b"original")
    )
    manifest = {
        "objects": [
            {
                "file": "object.enc",
                "key": "test",
                "content_type": "text/plain",
                "sha256": "wrong",
                "size": 7,
            }
        ]
    }
    with pytest.raises(RuntimeError, match="differs"):
        recovery.restore_objects(storage, "isolated-bucket", tmp_path, b"a" * 32, manifest)
    assert body.closed


def test_baseline_does_not_count_failed_business_responses():
    client = Mock()
    client.get.return_value = Mock(status_code=503)
    with pytest.raises(RuntimeError, match="business response"):
        recovery.baseline(client, "/assets", {})


def test_baseline_is_real_bounded_and_serializable():
    client = Mock()
    client.get.return_value = Mock(status_code=200)
    client.get.return_value.json.return_value = {"data": []}
    result = recovery.baseline(client, "/assets", {})
    assert client.get.call_count == 25
    assert result["requests"] == 24 and result["concurrency"] == 4
    assert result["capacity_verified"] is False
    assert result["p50_ms"] <= result["p95_ms"] <= result["max_ms"]
    json.dumps(result, allow_nan=False)
