"""Prepare a local image context from a pinned official MinIO release, never a mirror."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

VERSION = "RELEASE.2025-04-22T22-12-26Z"
NAME = f"minio.linux-amd64.{VERSION}"
SHA256 = "53e2a2cb16c5366ea6fbbc479c19ddb4c6a0948273e752f740fb1fbf27bb817c"
SIZE = 118849720
CONTEXT = Path(__file__).resolve().parents[1] / ".cache/minio"


def verified(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size != SIZE:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == SHA256


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy", help="Optional existing download proxy; no settings changed")
    args = parser.parse_args()
    CONTEXT.mkdir(parents=True, exist_ok=True)
    target = CONTEXT / "minio"
    if verified(target):
        print("PASS: cached official MinIO binary SHA-256 verified")
        return
    curl = ["curl.exe", "--fail", "--silent", "--show-error", "--connect-timeout", "15"]
    metadata = json.loads(
        subprocess.check_output(
            curl
            + [
                "--max-time",
                "30",
                f"https://api.github.com/repos/minio/minio/releases/tags/{VERSION}",
            ]
        )
    )
    asset = next(item for item in metadata["assets"] if item["name"] == NAME)
    if asset["size"] != SIZE or asset["digest"] != f"sha256:{SHA256}":
        raise RuntimeError("Official MinIO metadata differs from pinned artifact")
    if not re.fullmatch(
        r"https://api.github.com/repos/minio/minio/releases/assets/[0-9]+", asset["url"]
    ):
        raise RuntimeError("Unexpected official asset URL")
    headers = subprocess.check_output(
        curl
        + [
            "--head",
            "--max-time",
            "30",
            "--header",
            "Accept: application/octet-stream",
            asset["url"],
        ]
    ).decode()
    locations = re.findall(r"^location:\s*(https://[^\r\n]+)", headers, re.I | re.M)
    if len(locations) != 1 or not locations[0].startswith(
        "https://release-assets.githubusercontent.com/"
    ):
        raise RuntimeError("Expected official GitHub release redirect")
    if args.proxy:
        curl += ["--proxy", args.proxy]
    partial = CONTEXT / "minio.partial"
    subprocess.run(
        curl
        + [
            "--location",
            "--retry",
            "3",
            "--max-time",
            "600",
            "--output",
            str(partial),
            locations[0],
        ],
        check=True,
    )
    if not verified(partial):
        raise RuntimeError("Official MinIO size/SHA-256 verification failed; do not execute")
    partial.replace(target)
    print(f"PASS: official MinIO {VERSION}, SHA-256 {SHA256}")


if __name__ == "__main__":
    main()
