"""Check that runtime container references use reviewed immutable image digests."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "python": "4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54",
    "node": "a34ab6d7f6f4813f4d0c5469d74685604b962fbdbf5793a7bc7b4998ecb867c8",
    "pgvector": "0dd3149a06e4579a0d70d8fef9945636cd514005e7237deb27f9b67886f8dc4f",
    "redis": "8da19c7399dca900c6822a1cf30f9d2285585e2c4b457ff4095b0f4500054d3f",
    "nginx": "583b7f2fce1cdb8da5ad5c39ebd3c33b98b87d493190e548bdee1fa8c619c311",
}


def check() -> dict[str, bool]:
    python = (ROOT / "infrastructure/docker/python.Dockerfile").read_text(encoding="utf-8")
    web = (ROOT / "infrastructure/docker/web.Dockerfile").read_text(encoding="utf-8")
    minio = (ROOT / "infrastructure/docker/minio.Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    gateway = (ROOT / "docker-compose.gateway.yml").read_text(encoding="utf-8")
    gateway_image = (ROOT / "infrastructure/docker/gateway.Dockerfile").read_text(encoding="utf-8")
    checks = {
        "python_base_digest": f"python:3.12-slim@sha256:{EXPECTED['python']}" in python,
        "minio_base_digest": f"python:3.12-slim@sha256:{EXPECTED['python']}" in minio,
        "node_builder_digest": f"node:24-alpine@sha256:{EXPECTED['node']}" in web,
        "node_runner_digest": web.count(f"node:24-alpine@sha256:{EXPECTED['node']}") == 2,
        "pgvector_digest": f"pgvector/pgvector:pg16@sha256:{EXPECTED['pgvector']}" in compose,
        "redis_digest": f"redis:7-alpine@sha256:{EXPECTED['redis']}" in compose,
        "nginx_digest": f"nginx:stable-alpine@sha256:{EXPECTED['nginx']}" in gateway_image,
        "gateway_uses_reviewed_build": (
            "dockerfile: infrastructure/docker/gateway.Dockerfile" in gateway
            and "image: knowledge-os/gateway:nginx-1.30.6-local" in gateway
            and "pull_policy: build" in gateway
        ),
        "no_mutable_runtime_base": not bool(
            re.search(
                r"(?:FROM\s+|image:\s*)(?:registry\.hub\.docker\.com/library/)?"
                r"(?:python:3\.12-slim|node:24-alpine|pgvector/pgvector:pg16|"
                r"redis:7-alpine|nginx:stable-alpine)(?:\s|$)",
                python + web + minio + compose + gateway + gateway_image,
                re.MULTILINE,
            )
        ),
    }
    return checks


def main() -> None:
    checks = check()
    if not all(checks.values()):
        raise SystemExit("Immutable image pin check failed")
    print("PASS: runtime image references use reviewed immutable digests")


if __name__ == "__main__":
    main()
