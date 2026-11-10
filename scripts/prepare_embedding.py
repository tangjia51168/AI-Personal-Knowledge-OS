"""Download only pinned public model files; bound bytes and verify before rename.

No user content is transmitted. This explicit operator command uses the configured
network proxy; the inference service itself never downloads or accesses a hub.
"""

import argparse
import hashlib
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "infrastructure/embedding/model.json"


def verify(path: Path, record: dict) -> bool:
    if not path.is_file() or path.stat().st_size != record["size"]:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == record["sha256"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    target = ROOT / ".cache/embedding-model" / manifest["identity"]
    target.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, trust_env=True, follow_redirects=True) as client:
        for name, record in manifest["files"].items():
            path = target / name
            if verify(path, record):
                print(f"verified: {name}", flush=True)
                continue
            if not args.download:
                raise SystemExit("Model missing or invalid; run with --download")
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".partial")
            url = (
                f"https://huggingface.co/{manifest['repository']}/resolve/"
                f"{manifest['revision']}/{name}"
            )
            try:
                count = 0
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    with temporary.open("wb") as stream:
                        for chunk in response.iter_bytes(1024 * 1024):
                            count += len(chunk)
                            if count > record["size"]:
                                raise ValueError("MODEL_SIZE_EXCEEDED")
                            stream.write(chunk)
                if not verify(temporary, record):
                    raise ValueError("MODEL_DIGEST_MISMATCH")
                temporary.replace(path)
            except (httpx.HTTPError, OSError, ValueError):
                # Do not print signed CDN URLs or proxy credentials from exceptions.
                raise SystemExit(f"Pinned model download failed: {name}") from None
            print(f"downloaded and verified: {name}", flush=True)
    print("Pinned local embedding model ready")


if __name__ == "__main__":
    main()
