"""Publish an explicitly authorized, verified source snapshot using Windows Git credentials.

Never stores tokens, changes global Git settings, overwrites repositories or force pushes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from scripts.source_release import verify

ACCOUNT = "tangjia51168"
ACCOUNT_ID = 312099756
REPOSITORY = "AI-Personal-Knowledge-OS"
REMOTE = f"https://github.com/{ACCOUNT}/{REPOSITORY}.git"


def execute(arguments: list[str], cwd: Path, *, input: str | None = None) -> str:
    if arguments[0] == "git":
        # Trust only this verified snapshot for this command; never alter global config.
        arguments = ["git", "-c", f"safe.directory={cwd.resolve().as_posix()}", *arguments[1:]]
    result = subprocess.run(
        arguments,
        cwd=cwd,
        input=input,
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        timeout=120,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"},
    )
    if result.returncode:
        raise RuntimeError("PUBLICATION_COMMAND_FAILED")
    return result.stdout.strip()


def credential(staging: Path) -> str:
    output = execute(
        ["git", "credential", "fill"],
        staging,
        input="protocol=https\nhost=github.com\n\n",
    )
    values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    token = values.get("password", "")
    if not re.fullmatch(r"[A-Za-z0-9_]{20,255}", token):
        raise RuntimeError("GITHUB_CREDENTIAL_UNAVAILABLE")
    return token


def api(
    token: str, route: str, *, payload: dict | None = None, method: str = "POST"
) -> tuple[int, dict]:
    if method not in {"POST", "PATCH"}:
        raise ValueError("INVALID_GITHUB_METHOD")
    if not re.fullmatch(
        r"/(?:user|user/repos|repos/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+"
        r"(?:/git/(?:refs/heads/main|commits(?:/[a-f0-9]{40})?|trees))?)",
        route,
    ):
        raise ValueError("INVALID_GITHUB_ROUTE")
    config = (
        f'url = "https://api.github.com{route}"\n'
        f'header = "Authorization: Bearer {token}"\n'
        'header = "Accept: application/vnd.github+json"\n'
        'header = "X-GitHub-Api-Version: 2022-11-28"\n'
        'user-agent = "knowledge-os-source-release"\n'
    )
    if payload is not None:
        # curl config uses its own double-quoted string escapes. No shell involved.
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
        escaped = body.replace("\\", "\\\\").replace('"', '\\"')
        config += f'request = "{method}"\nheader = "Content-Type: application/json"\n'
        config += f'data = "{escaped}"\n'
    result = subprocess.run(
        [
            "curl.exe",
            "-q",
            "--silent",
            "--show-error",
            "--max-time",
            "30",
            "--config",
            "-",
            "--write-out",
            "\n%{http_code}",
        ],
        input=config,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=40,
    )
    if result.returncode:
        raise RuntimeError("GITHUB_NETWORK_FAILED")
    try:
        body, status = result.stdout.rsplit("\n", 1)
        return int(status), json.loads(body)
    except (ValueError, TypeError):
        raise RuntimeError("GITHUB_RESPONSE_INVALID") from None


def publish(staging: Path) -> dict:
    staging = staging.resolve(strict=True)
    verify(staging)
    if (staging / ".git").exists():
        raise ValueError("FRESH_SOURCE_SNAPSHOT_REQUIRED")
    token = credential(staging)
    status, user = api(token, "/user")
    if status != 200 or user.get("login") != ACCOUNT or user.get("id") != ACCOUNT_ID:
        raise RuntimeError("GITHUB_ACCOUNT_MISMATCH")
    status, _existing = api(token, f"/repos/{ACCOUNT}/{REPOSITORY}")
    if status != 404:
        raise RuntimeError("EXISTING_REPOSITORY_NOT_MODIFIED")
    # Prepare a concrete local commit before creating anything externally.
    execute(["git", "init", "--initial-branch=main"], staging)
    execute(["git", "config", "user.name", ACCOUNT], staging)
    execute(
        ["git", "config", "user.email", f"{ACCOUNT_ID}+{ACCOUNT}@users.noreply.github.com"], staging
    )
    execute(["git", "config", "core.autocrlf", "false"], staging)
    execute(["git", "add", "--", "."], staging)
    tracked = set(execute(["git", "ls-files"], staging).splitlines())
    manifest = json.loads((staging / "SOURCE_MANIFEST.json").read_text("utf-8"))
    if tracked != {*manifest["files"], "SOURCE_MANIFEST.json"}:
        raise RuntimeError("STAGED_SOURCE_MANIFEST_MISMATCH")
    execute(["git", "commit", "-m", "Publish self-hosted knowledge OS source preview"], staging)
    verify(staging)
    status, repository = api(
        token,
        "/user/repos",
        payload={
            "name": REPOSITORY,
            "private": False,
            "auto_init": False,
            "description": "Self-hosted evidence-based personal knowledge OS — development preview",
            "has_issues": True,
            "has_wiki": False,
        },
    )
    del token
    if (
        status != 201
        or repository.get("full_name") != f"{ACCOUNT}/{REPOSITORY}"
        or repository.get("private") is not False
    ):
        raise RuntimeError("GITHUB_CREATION_NOT_CONFIRMED")
    execute(["git", "remote", "add", "origin", REMOTE], staging)
    execute(["git", "push", "--set-upstream", "origin", "main"], staging)
    commit = execute(["git", "rev-parse", "HEAD"], staging)
    remote_commit = execute(["git", "ls-remote", "origin", "refs/heads/main"], staging).split()[0]
    if remote_commit != commit:
        raise RuntimeError("GITHUB_PUSH_NOT_VERIFIED")
    return {
        "url": f"https://github.com/{ACCOUNT}/{REPOSITORY}",
        "commit": commit,
        "scope": "public_source_preview",
        "hosted_application": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--publish-authorized", action="store_true", required=True)
    args = parser.parse_args()
    try:
        result = publish(args.source)
        destination = args.source.parent / "publication.json"
        destination.write_text(json.dumps(result, indent=2), "utf-8")
        print(json.dumps(result))
    except Exception as error:
        # Raw curl/Git outputs and credential-manager output are never disclosed.
        code = str(error)
        known = code if re.fullmatch(r"[A-Z_]{4,80}", code) else type(error).__name__
        raise SystemExit(f"FAIL: GitHub publication ({known}); state retained") from None


if __name__ == "__main__":
    main()
