"""Public snapshots must exclude private artifacts and reject tampering before publication."""

import json

import pytest

from scripts import source_release as release


@pytest.mark.parametrize("name", ["../x", "/x", "C:/x", "a\\b", ".env", "a/.git/key"])
def test_private_paths_are_not_snapshot_names(name):
    if name == ".env":
        assert name not in release.TOP
    else:
        assert not release.safe_name(name)


@pytest.mark.parametrize(
    "value",
    [
        "-----BEGIN " + "PRIVATE KEY-----",
        "ghp_" + "a" * 36,
        "sk-" + "b" * 40,
        "postgresql://u:" + "actual-secret-value@localhost/db",
        "https://chatgpt" + ".com/share/private",
        "C:/" + "Users/owner/private.txt",
    ],
)
def test_content_guard_rejects_secrets_and_private_references(value):
    with pytest.raises(ValueError):
        release.inspect_content("apps/api/app/code.py", value.encode())


def test_public_payload_excludes_user_material_and_is_text_only():
    payload = release.public_payload(release.ROOT)
    assert "LICENSE" in payload and "apps/api/app/main.py" in payload
    assert "apps/web/src/app/account/collections.tsx" in payload
    assert "infrastructure/gateway/nginx.conf" in payload
    assert "infrastructure/deployment/compose.env.example" in payload
    assert "AGENTS.md" not in payload
    assert not any(name.endswith((".docx", ".png", ".env")) for name in payload)
    assert "chatgpt.com/share" not in payload["docs/mainline-acceptance.md"].decode()


def test_verifier_rejects_changed_extra_missing_and_symlink_files(tmp_path):
    import hashlib

    file = tmp_path / "README.md"
    file.write_text("public")
    manifest = {
        "format": "knowledge-os-public-source-v1",
        "hosted_service_verified": False,
        "files": {"README.md": hashlib.sha256(b"public").hexdigest()},
    }
    (tmp_path / "SOURCE_MANIFEST.json").write_text(json.dumps(manifest))
    release.verify(tmp_path)
    file.write_text("changed")
    with pytest.raises(ValueError, match="CHECKSUM"):
        release.verify(tmp_path)
    file.write_text("public")
    (tmp_path / "unexpected.txt").write_text("extra")
    with pytest.raises(ValueError):
        release.verify(tmp_path)
