import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts import storage_runtime_check as check


def runtime_item():
    return {
        "Config": {
            "Labels": {
                "com.docker.compose.project": check.operations_check.PROJECT,
                "com.docker.compose.service": "api",
            },
            "Env": [
                "S3_ENDPOINT_URL=http://minio:9000",
                "S3_BUCKET=knowledge-os",
                "S3_ACCESS_KEY=scoped-user",
                "S3_SECRET_KEY=scoped-secret",
                "S3_SESSION_TOKEN=",
            ],
        },
        "State": {"Running": True},
    }


def test_inspection_rejects_operator_credentials_in_any_environment_field():
    values = {
        "S3_ACCESS_KEY": "operator-user",
        "S3_SECRET_KEY": "operator-secret",
        "S3_BUCKET": "knowledge-os",
    }
    item = runtime_item()
    check.verify_environment(item, "api", values)
    for value in (
        "S3_ACCESS_KEY=operator-user",
        "OTHER=prefix-operator-secret-suffix",
        "MIGRATION_DATABASE_URL=",
        "MINIO_ROOT_USER=",
        "S3_SESSION_TOKEN=temporary",
    ):
        invalid = deepcopy(item)
        invalid["Config"]["Env"].append(value)
        with pytest.raises(RuntimeError):
            check.verify_environment(invalid, "api", values)
    item["Config"]["Labels"]["com.docker.compose.project"] = "other-project"
    with pytest.raises(RuntimeError):
        check.verify_environment(item, "api", values)


def test_runtime_probe_does_not_accept_missing_denials_or_leak_subprocess_errors(monkeypatch):
    monkeypatch.setattr(check.operations_check, "docker_path", lambda: "docker")
    result = SimpleNamespace(returncode=1, stdout=b"secret-output", stderr=b"secret-error")
    monkeypatch.setattr(check.subprocess, "run", lambda *args, **kwargs: result)
    with pytest.raises(RuntimeError, match="^Runtime storage probe failed$") as error:
        check.check_process("api", "acceptance-runtime/test", "outside-test")
    assert "secret" not in str(error.value)
    result.returncode = 0
    result.stdout = json.dumps({"application_head_put_get_delete": True}).encode()
    with pytest.raises(RuntimeError, match="permissions failed"):
        check.check_process("api", "acceptance-runtime/test", "outside-test")


def test_runtime_probe_source_is_valid_python_and_uses_actual_adapter():
    source = check.probe_source("acceptance-runtime/test", "outside-test")
    compile(source, "runtime-storage-probe", "exec")
    assert "get_s3_client()" in source
    assert "aws_access_key_id" not in source
