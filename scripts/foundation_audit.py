"""Offline reproducibility and license inventory. Never reads dotenv secrets.

An inventory is not legal approval or a CVE scan. Reports include exact lockfile
digests and unresolved package metadata rather than assuming license compliance.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
LOCKS = ("requirements/runtime.lock", "requirements/dev.lock", "requirements/build.lock")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def python_inventory(root: Path) -> list[dict]:
    packages: dict[str, dict] = {}
    for lock in LOCKS:
        # Hash continuation lines belong to their preceding exact distribution pin.
        content = (root / lock).read_text(encoding="utf-8").replace("\\\n", " ")
        for line in content.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if line == "-r runtime.lock" and lock == "requirements/dev.lock":
                continue
            match = re.match(
                r"^([A-Za-z0-9_.-]+)==([A-Za-z0-9.+!-]+)(?:;[^\\]*?)?\s+(--hash=.+)$", line
            )
            if not match or not re.search(r"--hash=sha256:[0-9a-f]{64}(?:\s|$)", match[3]):
                raise RuntimeError("Python lock is not exact and hash-bound")
            name, version = match[1].lower().replace("_", "-"), match[2]
            if name in packages and packages[name]["version"] != version:
                raise RuntimeError("Conflicting versions across Python locks")
            entry = packages.setdefault(name, {"name": name, "version": version, "locks": []})
            entry["locks"].append(lock)
    for name, entry in packages.items():
        try:
            distribution = importlib.metadata.distribution(name)
            metadata = distribution.metadata
            classifiers = [
                value.removeprefix("License :: ")
                for value in metadata.get_all("Classifier", [])
                if value.startswith("License :: ")
            ]
            # Metadata only: no arbitrary LICENSE contents, authors, URLs or code.
            expression = metadata.get("License-Expression")
            legacy = metadata.get("License", "")
            entry["license"] = (
                expression
                or classifiers
                or (legacy if len(legacy) <= 160 and "\n" not in legacy else None)
            )
            entry["metadata_source"] = "local-installed-distribution"
            entry["installed_version"] = distribution.version
            if distribution.version != entry["version"]:
                entry["license"] = None
                entry["metadata_source"] = "installed-version-differs-from-lock"
        except importlib.metadata.PackageNotFoundError:
            entry["license"] = None
            entry["metadata_source"] = "distribution-not-installed-on-this-platform"
    return sorted(packages.values(), key=lambda value: value["name"])


def node_inventory(root: Path) -> list[dict]:
    payload = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))
    if payload.get("lockfileVersion") != 3:
        raise RuntimeError("Unsupported Node lock schema")
    result = []
    for path, package in payload["packages"].items():
        if not path or package.get("link") or not path.startswith("node_modules/"):
            continue
        integrity = package.get("integrity", "")
        integrity_source = path
        if package.get("inBundle"):
            # npm embeds these files in the ancestor tarball. Require that exact
            # bundle declaration and the containing archive's integrity instead
            # of inventing a missing per-file digest or accepting any missing hash.
            parent, _, name = path.rpartition("/node_modules/")
            ancestor = payload["packages"].get(parent, {})
            if name not in ancestor.get("bundleDependencies", []):
                raise RuntimeError("Unbound bundled dependency")
            integrity = ancestor.get("integrity", "")
            integrity_source = parent
        if not package.get("version") or not re.fullmatch(r"sha512-[A-Za-z0-9+/=]+", integrity):
            raise RuntimeError("Node dependency lacks version/integrity")
        result.append(
            {
                "path": path,
                "version": package["version"],
                "license": package.get("license"),
                "dev": package.get("dev", False),
                "integrity_source": integrity_source,
            }
        )
    return result


def inventory(root: Path) -> dict:
    python = python_inventory(root)
    node = node_inventory(root)
    missing = {
        "python": [item["name"] for item in python if not item["license"]],
        "node": [item["path"] for item in node if not item["license"]],
    }
    return {
        "format": 1,
        "status": "inventory-complete",
        "completed_at": datetime.now(UTC).isoformat(),
        "input_sha256": {name: sha256(root / name) for name in (*LOCKS, "package-lock.json")},
        "python": python,
        "node": node,
        "missing_license_metadata": missing,
        "runtime_components": [
            {"name": "PostgreSQL", "declared_license": "PostgreSQL"},
            {"name": "pgvector", "declared_license": "PostgreSQL"},
            {
                "name": "Redis 7 (exact version needs image review)",
                "declared_license": "version-dependent; inspect image SBOM",
            },
            {"name": "MinIO RELEASE.2025-04-22T22-12-26Z", "declared_license": "AGPL-3.0"},
            {"name": "Nginx", "declared_license": "BSD-2-Clause"},
        ],
        "legal_review": "not-approved",
        "image_sbom_review": "required",
        "cve_scan_performed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", action="store_true", required=True)
    parser.parse_args()
    try:
        report = inventory(ROOT)
        directory = ROOT / ".cache/foundation-audit" / uuid4().hex
        directory.mkdir(parents=True, exist_ok=False)
        path = directory / "summary.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except Exception as error:
        raise SystemExit(f"FAIL: offline inventory ({type(error).__name__})") from None
    print(f"PASS: exact-lock inventory; legal/image review remains required: {path}")


if __name__ == "__main__":
    main()
