"""Cache a pinned official MinIO mc binary for the local provisioning container.

This downloads an artifact inside the workspace; it does not install a system
tool or execute the binary. Existing valid files are reused without a request.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

VERSION = "RELEASE.2025-04-16T18-13-26Z"
NAME = f"mc.linux-amd64.{VERSION}"
SHA256 = "ac90da87a35641be5a0ac75d49de5161ddb47d629b5ba01261b0ae9e00aea15f"
SIZE = 30179512
ASSET_URL = "https://api.github.com/repos/minio/mc/releases/assets/401741877"
METADATA_URL = f"https://api.github.com/repos/minio/mc/releases/tags/{VERSION}"
CONTEXT = Path(__file__).resolve().parents[1] / ".cache/minio-mc"
MAX_METADATA_BYTES = 1048576


def verified(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size != SIZE:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == SHA256


def validate_metadata(metadata: dict) -> None:
    matches = [item for item in metadata.get("assets", []) if item.get("name") == NAME]
    if len(matches) != 1:
        raise RuntimeError("Pinned official mc asset missing or ambiguous")
    asset = matches[0]
    if (
        asset.get("size") != SIZE
        or asset.get("digest") != f"sha256:{SHA256}"
        or asset.get("url") != ASSET_URL
    ):
        raise RuntimeError("Official mc metadata differs from pinned artifact")


def validate_redirect(location: str) -> str:
    target = urlsplit(location)
    if (
        target.scheme != "https"
        or target.hostname != "release-assets.githubusercontent.com"
        or target.port not in (None, 443)
        or target.username is not None
        or target.password is not None
        or not target.path.startswith("/")
        or target.fragment
    ):
        raise RuntimeError("Expected official GitHub release redirect")
    return location


def prepare(proxy: str | None = None) -> Path:
    CONTEXT.mkdir(parents=True, exist_ok=True)
    target = CONTEXT / "mc"
    if verified(target):
        return target
    deadline = time.monotonic() + 600
    partial = CONTEXT / ("mc.partial-" + uuid4().hex)
    # Explicit proxy only; no machine proxy settings or certificate bypass.
    with httpx.Client(
        proxy=proxy,
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(30, connect=15),
        headers={"User-Agent": "knowledge-os-artifact-preparation"},
    ) as client:
        with client.stream("GET", METADATA_URL) as response:
            if response.status_code != 200:
                raise RuntimeError("Official mc release metadata request failed")
            metadata_bytes = bytearray()
            for chunk in response.iter_bytes():
                if len(metadata_bytes) + len(chunk) > MAX_METADATA_BYTES:
                    raise RuntimeError("Official release metadata exceeds limit")
                metadata_bytes.extend(chunk)
        validate_metadata(json.loads(metadata_bytes))
        response = client.head(ASSET_URL, headers={"Accept": "application/octet-stream"})
        if response.status_code not in (302, 307):
            raise RuntimeError("Official mc asset did not return a release redirect")
        location = validate_redirect(response.headers.get("location", ""))
        try:
            with client.stream("GET", location) as response:
                if response.status_code != 200:
                    raise RuntimeError("Official mc artifact download failed")
                total = 0
                with partial.open("xb") as stream:
                    for chunk in response.iter_bytes(1024 * 1024):
                        total += len(chunk)
                        if total > SIZE or time.monotonic() > deadline:
                            raise RuntimeError("Official mc download exceeds size or time limit")
                        stream.write(chunk)
            if not verified(partial):
                raise RuntimeError("Official mc size/SHA-256 verification failed; do not execute")
            partial.replace(target)
        finally:
            # Only this invocation's untrusted partial file is removed.
            partial.unlink(missing_ok=True)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy", help="Explicit existing download proxy; no settings changed")
    args = parser.parse_args()
    try:
        prepare(args.proxy)
    except Exception as error:
        # HTTP exceptions may contain signed redirect URLs; do not print them.
        raise SystemExit(f"Official mc preparation failed ({type(error).__name__})") from None
    print(f"PASS: official MinIO mc {VERSION}, size and SHA-256 verified")


if __name__ == "__main__":
    main()
