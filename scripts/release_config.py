"""Read-only deployment input checks. Never starts services or prints secret values.

Uses literal KEY=value files: no interpolation, export prefixes or multiline values.
Run before Compose; production preflight and release approval are separate gates.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    if not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("INVALID_ENV_FILE")
    values: dict[str, str] = {}
    for line in path.read_text("utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if (
            not separator
            or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
            or key in values
            or "$" in value
            or value != value.strip()
            or value.startswith(("'", '"'))
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("INVALID_LITERAL_ENV")
        values[key] = value
    return values


def check(path: Path, *, embedding: bool = False) -> None:
    # Compose shell variables override --env-file; null environment entries may
    # also inherit the shell's migration URL. Reject ambiguity before Compose.
    if any(key.startswith("KOS_") or key == "MIGRATION_DATABASE_URL" for key in os.environ):
        raise ValueError("DEPLOYMENT_SHELL_OVERRIDE_FORBIDDEN")
    values = read_env(path)
    names = ["API", "WEB", "GATEWAY"] + (["EMBEDDING"] if embedding else [])
    for name in names:
        reference = values.get(f"KOS_{name}_IMAGE", "")
        if not re.fullmatch(
            r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?/"
            r"[a-z0-9]+(?:[._/-][a-z0-9]+)*@sha256:[0-9a-f]{64}",
            reference,
        ):
            raise ValueError("IMMUTABLE_IMAGE_REFERENCE_REQUIRED")
    paths: dict[str, Path] = {}
    for name in ["RUNTIME_ENV", "MIGRATION_ENV", "GATEWAY_CONFIG", "TLS_CERT", "TLS_KEY"]:
        candidate = Path(values.get(f"KOS_{name}", ""))
        if not candidate.is_absolute() or not candidate.is_file():
            raise ValueError("ABSOLUTE_DEPLOYMENT_FILE_REQUIRED")
        paths[name] = candidate.resolve()
    if len(set(paths.values())) != len(paths):
        raise ValueError("SEPARATE_DEPLOYMENT_FILES_REQUIRED")
    runtime = read_env(paths["RUNTIME_ENV"])
    migration = read_env(paths["MIGRATION_ENV"])
    if (
        "MIGRATION_DATABASE_URL" in runtime
        or runtime.get("APP_ENV") != "production"
        or runtime.get("REGISTRATION_ENABLED") != "false"
        or set(migration) != {"MIGRATION_DATABASE_URL"}
        or not migration["MIGRATION_DATABASE_URL"]
    ):
        raise ValueError("SEPARATE_PRODUCTION_CREDENTIALS_REQUIRED")
    if embedding:
        model = Path(values.get("KOS_EMBEDDING_MODELS", ""))
        if not model.is_absolute() or not model.is_dir():
            raise ValueError("ABSOLUTE_MODEL_DIRECTORY_REQUIRED")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-env", type=Path, required=True)
    parser.add_argument("--embedding", action="store_true")
    args = parser.parse_args()
    try:
        check(args.compose_env, embedding=args.embedding)
    except Exception:
        raise SystemExit("FAIL: deployment inputs; no values printed") from None
    print("PASS: literal deployment inputs only; run production preflight next")


if __name__ == "__main__":
    main()
