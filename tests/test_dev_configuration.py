import importlib.util
from pathlib import Path

import pytest
from dotenv import dotenv_values


def load_configure_module():
    spec = importlib.util.spec_from_file_location("configure_dev", "scripts/configure_dev.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_acceptance_config_is_private_and_non_overwriting(tmp_path, monkeypatch, capsys):
    configure = load_configure_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["configure_dev", "--acceptance"])
    configure.main()
    target = Path(".env.acceptance")
    original = target.read_bytes()
    values = dotenv_values(target)
    assert ":15432/" in values["DATABASE_URL"]
    assert ":16379/" in values["REDIS_URL"]
    assert values["POSTGRES_PASSWORD"] != values["APP_DB_PASSWORD"]
    assert len(values["SYSTEM_API_TOKEN"]) == 64
    assert len(values["DOWNLOAD_SIGNING_KEY"]) == 64
    assert values["DOWNLOAD_SIGNING_KEY"] != values["SYSTEM_API_TOKEN"]
    output = capsys.readouterr().out
    for name in ("POSTGRES_PASSWORD", "APP_DB_PASSWORD", "S3_SECRET_KEY", "SYSTEM_API_TOKEN"):
        assert values[name] not in output
    with pytest.raises(SystemExit, match="already exists"):
        configure.main()
    assert target.read_bytes() == original


def test_generated_default_config(tmp_path, monkeypatch):
    configure = load_configure_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["configure_dev"])
    configure.main()
    values = dotenv_values(".env")
    assert ":5432/" in values["DATABASE_URL"]
    assert values["APP_DB_PASSWORD"] in values["DATABASE_URL"]
