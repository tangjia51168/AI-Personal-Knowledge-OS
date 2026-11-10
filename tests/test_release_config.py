"""Operator-input safety checks with no production connections."""

import pytest

from scripts.release_config import check, read_env


def inputs(tmp_path):
    values = {}
    for name in ("API", "WEB", "GATEWAY", "EMBEDDING"):
        values[f"KOS_{name}_IMAGE"] = "registry.example.com/kos/image@sha256:" + "a" * 64
    for name in ("RUNTIME_ENV", "MIGRATION_ENV", "GATEWAY_CONFIG", "TLS_CERT", "TLS_KEY"):
        path = tmp_path / name
        path.write_text("placeholder", encoding="utf-8")
        values[f"KOS_{name}"] = str(path)
    (tmp_path / "RUNTIME_ENV").write_text("APP_ENV=production\nREGISTRATION_ENABLED=false\n")
    (tmp_path / "MIGRATION_ENV").write_text("MIGRATION_DATABASE_URL=private\n")
    values["KOS_EMBEDDING_MODELS"] = str(tmp_path)
    return values


def write(tmp_path, values):
    path = tmp_path / "compose.env"
    path.write_text("\n".join(f"{key}={value}" for key, value in values.items()))
    return path


def test_valid_immutable_inputs_and_optional_embedding(tmp_path):
    path = write(tmp_path, inputs(tmp_path))
    check(path)
    check(path, embedding=True)


@pytest.mark.parametrize("key", ["KOS_API_IMAGE", "KOS_RUNTIME_ENV", "MIGRATION_DATABASE_URL"])
def test_compose_shell_cannot_override_reviewed_files_or_inherit_migration_secret(
    tmp_path, monkeypatch, key
):
    path = write(tmp_path, inputs(tmp_path))
    monkeypatch.setenv(key, "private-shell-value")
    with pytest.raises(ValueError, match="^DEPLOYMENT_SHELL_OVERRIDE_FORBIDDEN$"):
        check(path)


@pytest.mark.parametrize("value", ["repo:latest", "sha256:" + "a" * 64, "repo@sha256:abc"])
def test_mutable_or_incomplete_images_rejected(tmp_path, value):
    values = inputs(tmp_path)
    values["KOS_API_IMAGE"] = value
    with pytest.raises(ValueError, match="IMMUTABLE"):
        check(write(tmp_path, values))


@pytest.mark.parametrize(
    "kind", ["credential", "same_file", "extra_migration", "missing", "relative"]
)
def test_credential_separation_and_missing_inputs(tmp_path, kind):
    values = inputs(tmp_path)
    if kind == "credential":
        with (tmp_path / "RUNTIME_ENV").open("a") as stream:
            stream.write("MIGRATION_DATABASE_URL=private\n")
    elif kind == "same_file":
        values["KOS_MIGRATION_ENV"] = values["KOS_RUNTIME_ENV"]
    elif kind == "extra_migration":
        (tmp_path / "MIGRATION_ENV").write_text("MIGRATION_DATABASE_URL=x\nSECRET=y\n")
    else:
        values["KOS_TLS_KEY"] = str(tmp_path / "absent") if kind == "missing" else "key.pem"
    with pytest.raises(ValueError):
        check(write(tmp_path, values))


@pytest.mark.parametrize("value", ["A=x\nA=y", "A=$SECRET", 'A="x"', "export A=x", "A=x "])
def test_ambiguous_dotenv_forms_rejected_without_values_in_error(tmp_path, value):
    path = tmp_path / "input.env"
    path.write_text(value)
    with pytest.raises(ValueError, match="^INVALID_LITERAL_ENV$"):
        read_env(path)
