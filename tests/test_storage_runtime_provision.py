import pytest

from scripts import provision_storage_runtime as provision


def test_runtime_file_names_are_generated_and_state_is_explicit(tmp_path, monkeypatch):
    target = tmp_path / ".env.storage-runtime.acceptance"
    monkeypatch.setattr(provision, "RUNTIME_FILE", target)
    values = provision.save_credentials()
    assert values["S3_RUNTIME_ACCESS_KEY"].startswith("kosruntime")
    assert values["S3_RUNTIME_POLICY"].startswith("kos-runtime-")
    assert values["S3_RUNTIME_STATE"] == "pending"
    assert target.read_text(encoding="utf-8").count("S3_RUNTIME_SECRET_KEY=") == 1
    original = target.read_bytes()
    assert provision.save_credentials() == values
    assert target.read_bytes() == original
    with pytest.raises(RuntimeError, match="incomplete"):
        target.write_text("S3_RUNTIME_ACCESS_KEY=partial\n", encoding="utf-8")
        provision.save_credentials()


def test_mark_ready_replaces_only_pending_state(tmp_path, monkeypatch):
    target = tmp_path / ".env.storage-runtime.acceptance"
    target.write_text("S3_RUNTIME_STATE=pending\n", encoding="utf-8")
    monkeypatch.setattr(provision, "RUNTIME_FILE", target)
    provision.mark_ready()
    assert target.read_text(encoding="utf-8") == "S3_RUNTIME_STATE=ready\n"
    with pytest.raises(RuntimeError, match="Unexpected runtime file state"):
        provision.mark_ready()


def test_ready_identity_is_verified_without_overwriting_account_or_file(tmp_path, monkeypatch):
    target = tmp_path / ".env.storage-runtime.acceptance"
    monkeypatch.setattr(provision, "RUNTIME_FILE", target)
    provision.save_credentials()
    provision.mark_ready()
    original = target.read_bytes()
    values = provision.save_credentials()
    monkeypatch.setattr(provision, "verify_mc", lambda: None)
    verified = []
    monkeypatch.setattr(provision, "check_credentials", lambda *args: verified.append(True))
    monkeypatch.setattr(
        provision.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not modify account")
    )
    provision.provision({}, values)
    assert verified == [True]
    assert target.read_bytes() == original


def test_pending_complete_identity_recovers_without_administrator_mutations(tmp_path, monkeypatch):
    target = tmp_path / ".env.storage-runtime.acceptance"
    monkeypatch.setattr(provision, "RUNTIME_FILE", target)
    values = provision.save_credentials()
    monkeypatch.setattr(provision, "verify_mc", lambda: None)
    monkeypatch.setattr(provision, "check_credentials", lambda *args: None)
    monkeypatch.setattr(
        provision.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not modify account")
    )
    provision.provision({}, values)
    loaded = provision.save_credentials()
    assert loaded == {**values, "S3_RUNTIME_STATE": "ready"}
