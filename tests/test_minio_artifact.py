"""Local verification regressions; these do not substitute for live MinIO tests."""

import hashlib
import importlib.util
from pathlib import Path


def test_minio_verification_rejects_missing_truncated_and_corrupted_files(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("prepare_minio", "scripts/prepare_minio.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    expected = b"official-fixture"
    monkeypatch.setattr(module, "SIZE", len(expected))
    monkeypatch.setattr(module, "SHA256", hashlib.sha256(expected).hexdigest())
    artifact = tmp_path / "minio"
    assert not module.verified(artifact)
    artifact.write_bytes(expected[:-1])
    assert not module.verified(artifact)
    artifact.write_bytes(b"x" * len(expected))
    assert not module.verified(artifact)
    artifact.write_bytes(expected)
    assert module.verified(artifact)
    # The stream must be closed so Windows can rename a verified download.
    artifact.rename(tmp_path / "verified")


def test_minio_build_rechecks_digest_and_runs_nonroot():
    source = Path("infrastructure/docker/minio.Dockerfile").read_text()
    assert "sha256sum -c -" in source
    assert "USER minio" in source
    assert "FROM registry.hub.docker.com/library/python:3.12-slim@sha256:" in source
