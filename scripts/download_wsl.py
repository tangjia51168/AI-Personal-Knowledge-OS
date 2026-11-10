"""Download the official x64 WSL MSI in bounded concurrent HTTP ranges.

Artifacts stay in the ignored cache. Exact size, ranges, SHA-256 and the published
Microsoft release metadata are validated. Run with network approval.
"""

import argparse
import concurrent.futures
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

RELEASE = "https://api.github.com/repos/microsoft/WSL/releases/tags/2.7.14"
NAME = "wsl.2.7.14.0.x64.msi"
CACHE = Path(__file__).resolve().parents[1] / ".cache/container-prerequisites"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--proxy", help="Explicit existing HTTP proxy; never changes system settings"
    )
    args = parser.parse_args()
    curl = ["curl.exe", "--fail", "--silent", "--show-error", "--location"]
    # Windows curl uses the OS certificate store; do not disable TLS verification.
    release = json.loads(
        subprocess.check_output(
            curl
            + [
                "--connect-timeout",
                "15",
                "--max-time",
                "30",
                RELEASE,
            ]
        )
    )
    asset = next(item for item in release["assets"] if item["name"] == NAME)
    url, size, digest = asset["browser_download_url"], asset["size"], asset["digest"]
    if url != f"https://github.com/microsoft/WSL/releases/download/2.7.14/{NAME}":
        raise RuntimeError("Unexpected release download URL")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise RuntimeError("Official release is missing a SHA-256 digest")
    # The official API reaches the same Microsoft release without github.com routing.
    url = asset["url"]
    if not re.fullmatch(r"https://api.github.com/repos/microsoft/WSL/releases/assets/[0-9]+", url):
        raise RuntimeError("Unexpected official asset API URL")
    # Resolve the official redirect once, instead of spending one API request per range.
    redirect_headers = subprocess.check_output(
        [
            "curl.exe",
            "--fail",
            "--silent",
            "--show-error",
            "--head",
            "--connect-timeout",
            "15",
            "--max-time",
            "30",
            "--header",
            "Accept: application/octet-stream",
            url,
        ]
    ).decode("utf-8")
    locations = re.findall(r"^location:\s*(https://[^\r\n]+)", redirect_headers, re.I | re.M)
    if len(locations) != 1 or not locations[0].startswith(
        "https://release-assets.githubusercontent.com/"
    ):
        raise RuntimeError("Expected an official GitHub release asset redirect")
    url = locations[0]
    if args.proxy:
        curl += ["--proxy", args.proxy]
    CACHE.mkdir(parents=True, exist_ok=True)
    chunks = CACHE / "wsl-msi-chunks"
    chunks.mkdir(exist_ok=True)
    chunk_size = 2 * 1024 * 1024
    count = (size + chunk_size - 1) // chunk_size

    def download(index: int) -> int:
        start = index * chunk_size
        end = min(size - 1, start + chunk_size - 1)
        target = chunks / f"{index:04d}.part"
        expected = end - start + 1
        if target.exists() and target.stat().st_size == expected:
            return expected
        for attempt in range(4):
            try:
                headers = chunks / f"{index:04d}.headers"
                data = subprocess.check_output(
                    curl
                    + [
                        "--header",
                        "Accept: application/octet-stream",
                        "--connect-timeout",
                        "15",
                        "--max-time",
                        "150",
                        "--range",
                        f"{start}-{end}",
                        "--dump-header",
                        str(headers),
                        url,
                    ]
                )
                if f"content-range: bytes {start}-{end}/{size}" not in (
                    headers.read_text(encoding="utf-8").lower()
                ):
                    raise RuntimeError("Server did not honor requested byte range")
                if len(data) != expected:
                    raise RuntimeError("Incomplete download chunk")
                target.write_bytes(data)
                return expected
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(1)
        raise RuntimeError("Unreachable")

    print(f"Official WSL {size // (1024 * 1024)} MiB; {count} verified ranges", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(download, index) for index in range(count)]
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            future.result()
            completed += 1
            if completed % 5 == 0 or completed == count:
                print(f"Downloaded {completed}/{count} ranges", flush=True)
    output = CACHE / "wsl.2.7.14.0.x64.verified.msi"
    checksum = hashlib.sha256()
    with output.open("wb") as stream:
        for index in range(count):
            data = (chunks / f"{index:04d}.part").read_bytes()
            checksum.update(data)
            stream.write(data)
    if output.stat().st_size != size or f"sha256:{checksum.hexdigest()}" != digest:
        raise RuntimeError("MSI checksum mismatch; do NOT execute the file")
    print(f"PASS: official SHA-256 {checksum.hexdigest()}", flush=True)
    print(f"Ready for Authenticode verification: {output}", flush=True)


if __name__ == "__main__":
    main()
