"""Validate deployable configuration against real Docker parsers, with no deployment.

Uses only inspected local acceptance images, synthetic credentials and disposable
read-only containers. No production services, migrations or public ports are started.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import subprocess
from pathlib import Path
from uuid import uuid4

from scripts import release_package
from scripts.operations_check import ROOT, docker_path


def run(args: list[str], directory: Path, *, environment: dict[str, str] | None = None) -> None:
    result = subprocess.run(
        args, cwd=directory, env=environment, capture_output=True, timeout=60, check=False
    )
    if result.returncode:
        raise RuntimeError("RELEASE_CONFIG_CHECK_FAILED")


def image(service: str) -> str:
    result = subprocess.run(
        [
            docker_path(),
            "inspect",
            "--format",
            "{{.Image}}",
            f"knowledge-os-acceptance-{service}-1",
        ],
        capture_output=True,
        timeout=15,
        check=False,
    )
    value = result.stdout.decode().strip()
    import re

    if result.returncode or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise RuntimeError("LOCAL_IMAGE_UNAVAILABLE")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acceptance", action="store_true", required=True)
    parser.parse_args()
    directory = ROOT / ".cache/release-acceptance" / uuid4().hex
    directory.mkdir(parents=True)
    report: dict = {"status": "failed", "scope": "deployable_configuration_only", "checks": {}}
    try:
        bundle = release_package.package("knowledge.example.com")
        release_package.verify(bundle)
        report["package"] = str(bundle)
        report["checks"]["allowlist_and_manifest"] = True
        # Use actual running immutable image IDs, not pullable invented registry refs.
        images = {name: image(name) for name in ("api", "web", "gateway", "embedding")}
        report["images"] = images
        template = (ROOT / "infrastructure/deployment/nginx.conf.template").read_text("utf-8")
        gateway = directory / "nginx.conf"
        gateway.write_text(
            template.replace("__KOS_HOST__", "knowledge.example.com"), encoding="utf-8"
        )
        # Only synthetic values, created here; never copy any operator .env.
        runtime = directory / "runtime.env"
        runtime.write_text("APP_ENV=production\nREGISTRATION_ENABLED=false\n", encoding="ascii")
        migration = directory / "migration.env"
        migration.write_text(
            "MIGRATION_DATABASE_URL=postgresql+psycopg://unused@unreachable/db\n", encoding="ascii"
        )
        base = [
            docker_path(),
            "compose",
            "--project-directory",
            str(ROOT),
            "-f",
            str(ROOT / "docker-compose.production.yml"),
        ]
        env = {
            **os.environ,
            **{f"KOS_{name.upper()}_IMAGE": value for name, value in images.items()},
            "KOS_RUNTIME_ENV": str(runtime),
            "KOS_MIGRATION_ENV": str(migration),
            "KOS_GATEWAY_CONFIG": str(gateway),
            "KOS_TLS_CERT": str(ROOT / ".cache/tls-gateway/server.crt"),
            "KOS_TLS_KEY": str(ROOT / ".cache/tls-gateway/server.key"),
            "KOS_EMBEDDING_MODELS": str(
                ROOT
                / ".cache/embedding-model"
                / "multilingual-minilm-l12-v2-quint8-avx2-e8f8c211-mean128-v1"
            ),
        }
        run([*base, "config", "--quiet"], directory, environment=env)
        run(
            [
                *base,
                "-f",
                str(ROOT / "docker-compose.production-embedding.yml"),
                "config",
                "--quiet",
            ],
            directory,
            environment=env,
        )
        report["checks"]["real_compose_base_and_embedding"] = True
        # Exercise the actual production validator in the immutable runtime image.
        # Synthetic ephemeral secrets are never copied from acceptance credentials.
        runtime.write_text(
            "\n".join(
                [
                    "APP_ENV=production",
                    "REGISTRATION_ENABLED=false",
                    "EMAIL_VERIFICATION_REQUIRED=false",
                    "DATABASE_URL=postgresql+psycopg://knowledge_app:unused@unreachable/db",
                    "REDIS_URL=redis://default:unused@unreachable:6379/0",
                    "S3_ENDPOINT_URL=https://objects.example.com",
                    "S3_ACCESS_KEY=synthetic-runtime-identity",
                    f"S3_SECRET_KEY={secrets.token_hex(32)}",
                    f"SYSTEM_API_TOKEN={secrets.token_hex(32)}",
                    f"DOWNLOAD_SIGNING_KEY={secrets.token_hex(32)}",
                    "CREDENTIAL_KEYS="
                    + json.dumps({"v1": base64.b64encode(secrets.token_bytes(32)).decode()}),
                    "CREDENTIAL_ACTIVE_KEY=v1",
                    "PUBLIC_APP_URL=https://knowledge.example.com",
                    'CORS_ORIGINS=["https://knowledge.example.com"]',
                    'TRUSTED_HOSTS=["api","knowledge.example.com"]',
                ]
            )
            + "\n",
            encoding="ascii",
        )
        probe = [
            docker_path(),
            "run",
            "--rm",
            "--pull=never",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--network=none",
            "--tmpfs=/tmp:size=33554432",
            "--env-file",
            str(runtime),
            "--entrypoint=python",
            images["api"],
        ]
        run(
            [
                *probe,
                "-c",
                "from app.core.config import get_settings; "
                "from app.core.production import validate_production; "
                "validate_production(get_settings())",
            ],
            directory,
        )
        run([*probe, "-m", "app.modules.identity.provision", "--help"], directory)
        report["checks"]["network_disabled_runtime_preflight_and_provision_cli"] = True
        # Syntax test against actual Nginx binary. It resolves existing acceptance web
        # but does not start a listener or modify a running service.
        run(
            [
                docker_path(),
                "run",
                "--rm",
                "--pull=never",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges:true",
                "--user=101:101",
                "--network=knowledge-os-acceptance_default",
                "--tmpfs=/tmp:size=33554432",
                "--mount",
                f"type=bind,src={gateway},dst=/etc/nginx/nginx.conf,readonly",
                "--mount",
                f"type=bind,src={ROOT / '.cache/tls-gateway/server.crt'},"
                "dst=/etc/nginx/tls/server.crt,readonly",
                "--mount",
                f"type=bind,src={ROOT / '.cache/tls-gateway/server.key'},"
                "dst=/etc/nginx/tls/server.key,readonly",
                images["gateway"],
                "-t",
            ],
            directory,
        )
        report["checks"]["real_nginx_config_test"] = True
        report["status"] = "passed"
    except Exception:
        report["error"] = "RELEASE_CONFIG_CHECK_FAILED"
    finally:
        (directory / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"{report['status']}: {directory / 'summary.json'}")
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
