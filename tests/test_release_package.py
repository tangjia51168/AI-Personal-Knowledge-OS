"""Deployment contracts and extraction-free bundle integrity; no production deployment."""

import hashlib
import json
import zipfile

import pytest
import yaml

from scripts import release_package as release


class ComposeLoader(yaml.SafeLoader):
    pass


ComposeLoader.add_constructor("!reset", lambda loader, node: loader.construct_sequence(node))


def test_production_compose_has_separate_secrets_no_data_services_or_public_app_ports():
    config = yaml.load(
        (release.ROOT / "docker-compose.production.yml").read_text("utf-8"), Loader=ComposeLoader
    )
    assert config["name"] == "knowledge-os-production"
    services = config["services"]
    assert set(services) == {
        "api",
        "worker",
        "beat",
        "web",
        "gateway",
        "migrate",
        "preflight",
        "provision",
    }
    for name, service in services.items():
        assert "build" not in service and service["pull_policy"] == "never"
        assert service["read_only"] and service["cap_drop"] == ["ALL"]
        if name != "gateway":
            assert not service.get("ports")
        if name in {"api", "worker", "beat", "preflight", "provision"}:
            assert service["environment"]["APP_ENV"] == "production"
            assert service["environment"]["MIGRATION_DATABASE_URL"] is None
            assert service["environment"]["REGISTRATION_ENABLED"] == "false"
            assert "KOS_RUNTIME_ENV" in service["env_file"][0]
        if name in {"migrate", "preflight", "provision"}:
            assert service["profiles"] == ["maintenance"]
    assert services["migrate"]["command"] == ["alembic", "upgrade", release.SCHEMA]
    assert "KOS_MIGRATION_ENV" in services["migrate"]["env_file"][0]
    assert services["preflight"]["network_mode"] == "none"
    assert services["gateway"]["ports"] == ["${KOS_BIND_ADDRESS:-127.0.0.1}:443:8443"]
    assert all(
        mount["read_only"] and not mount["bind"]["create_host_path"]
        for mount in services["gateway"]["volumes"]
    )


def test_production_embedding_is_private_optional_and_artifact_pinned():
    config = yaml.safe_load(
        (release.ROOT / "docker-compose.production-embedding.yml").read_text("utf-8")
    )
    service = config["services"]["embedding"]
    assert service["networks"] == ["embedding"] and not service.get("ports")
    assert config["networks"]["embedding"]["internal"] is True
    assert "KOS_EMBEDDING_IMAGE" in service["image"] and service["pull_policy"] == "never"
    assert service["volumes"][0]["read_only"]
    assert (
        config["services"]["api"]["environment"]["LOCAL_EMBEDDING_ENDPOINT"]
        == "http://embedding:8080"
    )


@pytest.mark.parametrize(
    "value",
    [
        "localhost",
        "127.0.0.1",
        "localhost.local",
        "a.invalid",
        "a..com",
        "a.com;include",
        "a.com\n",
        "A.com",
        "*.a.com",
        "https://a.com",
        "a.com:443",
        "-a.com",
        "a-.com",
        "a" * 64 + ".com",
        "a.com/$x",
    ],
)
def test_release_hostname_cannot_inject_gateway_or_use_loopback(value):
    with pytest.raises(ValueError, match="INVALID_RELEASE_HOST"):
        release.hostname(value)


def fixture_root(tmp_path):
    for name in release.FILES:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((release.ROOT / name).read_bytes())
    (tmp_path / ".env").write_text("SECRET=must-not-escape")
    (tmp_path / "user-document.md").write_text("private user content")
    return tmp_path


def test_bundle_contains_exact_allowlist_hashes_and_no_local_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "ROOT", fixture_root(tmp_path))
    path = release.package("knowledge.example.com")
    release.verify(path)
    with zipfile.ZipFile(path) as archive:
        assert set(archive.namelist()) == {*release.FILES, "nginx.conf", "manifest.json"}
        assert not any(b"must-not-escape" in archive.read(name) for name in archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["schema"] == release.SCHEMA
        assert manifest["public_release_verified"] is False
        assert "__KOS_HOST__" not in archive.read("nginx.conf").decode()
    assert (
        path.with_suffix(".zip.sha256").read_text().strip()
        == hashlib.sha256(path.read_bytes()).hexdigest()
    )


@pytest.mark.parametrize("change", ["content", "extra", "duplicate", "manifest"])
def test_bundle_rejects_tamper_unlisted_entries_duplicates_and_false_release(
    tmp_path, monkeypatch, change
):
    monkeypatch.setattr(release, "ROOT", fixture_root(tmp_path))
    original = release.package("knowledge.example.com")
    with zipfile.ZipFile(original) as archive:
        records = {name: archive.read(name) for name in archive.namelist()}
    if change == "content":
        records["nginx.conf"] += b"\nmodified"
    elif change == "extra":
        records["../../.env"] = b"private"
    elif change == "manifest":
        manifest = json.loads(records["manifest.json"])
        manifest["public_release_verified"] = True
        records["manifest.json"] = json.dumps(manifest).encode()
    target = tmp_path / "tampered.zip"
    with zipfile.ZipFile(target, "w") as archive:
        for name, data in records.items():
            archive.writestr(name, data)
        if change == "duplicate":
            with pytest.warns(UserWarning):
                archive.writestr("nginx.conf", records["nginx.conf"])
    with pytest.raises(ValueError):
        release.verify(target)
