"""Build a secret-free deployment bundle from an explicit reviewed file allowlist.

No build, upload, deployment, credential generation or service mutation occurs.
The archive records exact source hashes; image digests must be supplied at deployment.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "0031_collections"
FILES = (
    "scripts/release_package.py",
    "scripts/release_config.py",
    "infrastructure/postgres/init-app-role.sh",
    "docker-compose.production.yml",
    "docker-compose.production-embedding.yml",
    "infrastructure/deployment/runtime.env.example",
    "infrastructure/deployment/migration.env.example",
    "infrastructure/deployment/compose.env.example",
    "infrastructure/deployment/nginx.conf.template",
    "docs/deployable-release.md",
    "docs/release-gates.md",
    "docs/runtime-vulnerability-followup.md",
    "docs/deployment-preparation.md",
    "docs/notifications.md",
    "docs/semantic-search.md",
    "docs/foundation-infrastructure.md",
    "docs/storage-runtime-policy.md",
    "infrastructure/embedding/model.json",
)


def hostname(value: str) -> str:
    if (
        len(value) > 253
        or value != value.lower()
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value)
    ):
        raise ValueError("INVALID_RELEASE_HOST")
    if (
        "." not in value
        or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in value.split(".")
        )
        or value.endswith((".invalid", ".local", ".localhost"))
    ):
        raise ValueError("INVALID_RELEASE_HOST")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    raise ValueError("INVALID_RELEASE_HOST")


def package(host: str) -> Path:
    host = hostname(host)
    # Existing local env/cache/user documents are excluded by construction.
    payload = {name: (ROOT / name).read_bytes() for name in FILES}
    config = payload["infrastructure/deployment/nginx.conf.template"].decode("utf-8")
    payload["nginx.conf"] = config.replace("__KOS_HOST__", host).encode("utf-8")
    manifest = {
        "format": "knowledge-os-deployment-v1",
        "schema": SCHEMA,
        "created_at": datetime.now(UTC).isoformat(),
        "hostname": host,
        "scope": "deployable_configuration_only",
        "public_release_verified": False,
        "external_services_required": [
            "postgresql16_pgvector",
            "authenticated_redis",
            "private_https_s3",
        ],
        "files": {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for name, data in payload.items()
        },
    }
    payload["manifest.json"] = json.dumps(manifest, indent=2).encode("utf-8")
    directory = ROOT / ".cache/release-packages" / uuid4().hex
    directory.mkdir(parents=True)
    destination = directory / "knowledge-os-deployment.zip"
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in payload.items():
            archive.writestr(name, data)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(".zip.sha256").write_text(digest + "\n", encoding="ascii")
    return destination


def verify(path: Path) -> None:
    """Strict extraction-free integrity check, including exact allowlist and bounded sizes."""
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("INVALID_RELEASE_PACKAGE")
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(set(names)) != len(names) or set(names) != {*FILES, "nginx.conf", "manifest.json"}:
            raise ValueError("INVALID_RELEASE_PACKAGE")
        if sum(entry.file_size for entry in entries) > 2 * 1024 * 1024:
            raise ValueError("INVALID_RELEASE_PACKAGE")
        manifest = json.loads(archive.read("manifest.json"))
        if (
            manifest.get("format") != "knowledge-os-deployment-v1"
            or manifest.get("schema") != SCHEMA
            or manifest.get("public_release_verified") is not False
            or set(manifest.get("files", {})) != {*FILES, "nginx.conf"}
        ):
            raise ValueError("INVALID_RELEASE_PACKAGE")
        host = hostname(manifest["hostname"])
        for name, record in manifest["files"].items():
            data = archive.read(name)
            if record != {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}:
                raise ValueError("RELEASE_CHECKSUM_MISMATCH")
        template = archive.read("infrastructure/deployment/nginx.conf.template").decode("utf-8")
        if archive.read("nginx.conf") != template.replace("__KOS_HOST__", host).encode("utf-8"):
            raise ValueError("RELEASE_GATEWAY_MISMATCH")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--host")
    choice.add_argument("--verify", type=Path)
    args = parser.parse_args()
    try:
        if args.verify:
            verify(args.verify)
            print("PASS: deployment package integrity only; release gates remain")
        else:
            path = package(args.host)
            verify(path)
            print(f"PASS: secret-free deployment package: {path}")
    except Exception:
        raise SystemExit("FAIL: deployment package; no secrets printed") from None


if __name__ == "__main__":
    main()
