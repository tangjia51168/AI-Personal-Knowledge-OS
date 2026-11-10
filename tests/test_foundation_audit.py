import json

import pytest

from scripts import foundation_audit as audit


def test_inventory_uses_lock_digests_and_does_not_claim_legal_or_cve_approval():
    result = audit.inventory(audit.ROOT)
    assert result["status"] == "inventory-complete"
    assert result["python"] and result["node"]
    assert result["legal_review"] == "not-approved"
    assert result["cve_scan_performed"] is False
    assert result["runtime_components"][3]["declared_license"] == "AGPL-3.0"
    assert all(len(value) == 64 for value in result["input_sha256"].values())


def test_python_rejects_floating_missing_hash_or_conflicting_versions(tmp_path):
    (tmp_path / "requirements").mkdir()
    for content in ("unsafe>=1", "unsafe==1.0", "unsafe==1.0 --hash=sha256:bad"):
        for lock in audit.LOCKS:
            (tmp_path / lock).write_text(content)
        with pytest.raises(RuntimeError, match="hash-bound"):
            audit.python_inventory(tmp_path)
    for lock in audit.LOCKS:
        version = "2.0" if lock.endswith("build.lock") else "1.0"
        (tmp_path / lock).write_text(f"unsafe=={version} --hash=sha256:" + "a" * 64)
    with pytest.raises(RuntimeError, match="Conflicting"):
        audit.python_inventory(tmp_path)


def test_node_rejects_package_without_integrity(tmp_path):
    path = tmp_path / "package-lock.json"
    path.write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {"node_modules/example": {"version": "1.0", "license": "MIT"}},
            }
        )
    )
    with pytest.raises(RuntimeError, match="integrity"):
        audit.node_inventory(tmp_path)


def test_bundled_dependency_requires_parent_declaration_and_archive_hash(tmp_path):
    path = tmp_path / "package-lock.json"
    payload = {
        "lockfileVersion": 3,
        "packages": {
            "node_modules/outer": {
                "version": "1.0",
                "integrity": "sha512-YQ==",
                "bundleDependencies": ["inner"],
            },
            "node_modules/outer/node_modules/inner": {"version": "2.0", "inBundle": True},
        },
    }
    path.write_text(json.dumps(payload))
    result = audit.node_inventory(tmp_path)
    assert result[1]["integrity_source"] == "node_modules/outer"
    payload["packages"]["node_modules/outer"]["bundleDependencies"] = []
    path.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match="Unbound"):
        audit.node_inventory(tmp_path)
