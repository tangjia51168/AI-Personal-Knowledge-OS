"""Run the real local audit CLI with a disposable fixture and no paid/external requests."""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from sqlalchemy import create_engine

from scripts.audit_archive import ARCHIVE_ROOT, ROOT, verify_archive
from scripts.deepseek_acceptance import cleanup_fixture
from scripts.operations_check import load_acceptance


def run_acceptance() -> str:
    values = load_acceptance()
    engine = create_engine(
        values["MIGRATION_DATABASE_URL"], hide_parameters=True, connect_args={"connect_timeout": 3}
    )
    account = None
    directory = None
    try:
        with httpx.Client(
            base_url="http://127.0.0.1:18000", trust_env=False, follow_redirects=False, timeout=15
        ) as client:
            response = client.post(
                "/api/v1/auth/register",
                json={
                    "email": f"archive-{uuid4().hex}@example.invalid",
                    "password": secrets.token_urlsafe(32),
                },
            )
            if response.status_code != 201:
                raise RuntimeError("Archive fixture registration failed")
            account = response.json()["data"]
            token = account["access_token"]
            headers = {"Authorization": "Bearer " + token}
            response = client.post(
                f"/api/v1/spaces/{account['tenant_id']}/settings",
                headers=headers,
                json={"theme": "dark", "allow_cloud_fallback": False},
            )
            if response.status_code != 200:
                raise RuntimeError("Archive fixture mutation failed")
            before = set(ARCHIVE_ROOT.iterdir()) if ARCHIVE_ROOT.exists() else set()
            args = [
                sys.executable,
                "-m",
                "scripts.audit_archive",
                "export",
                "--acceptance",
                "--tenant-id",
                account["tenant_id"],
            ]
            result = subprocess.run(
                args,
                input=token + "\n",
                text=True,
                capture_output=True,
                timeout=60,
                cwd=ROOT,
            )
            if result.returncode:
                raise RuntimeError("Archive export CLI failed; output withheld")
            created = set(ARCHIVE_ROOT.iterdir()) - before
            if len(created) != 1:
                raise RuntimeError("One archive directory required")
            directory = created.pop()
            summary = verify_archive(directory)
            if summary["business_events"] != 2 or summary["account_events"] != 1:
                raise RuntimeError("Archive counts differ from actual fixture")
            # Independent process, no database/API access needed for verification.
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.audit_archive",
                    "verify",
                    "--directory",
                    str(directory),
                ],
                text=True,
                capture_output=True,
                timeout=30,
                cwd=ROOT,
            )
            if result.returncode or json.loads(result.stdout) != summary:
                raise RuntimeError("Offline archive CLI verification failed")
            if client.post("/api/v1/auth/logout", headers=headers).status_code != 200:
                raise RuntimeError("Fixture session revocation failed")
            before = set(ARCHIVE_ROOT.iterdir())
            rejected = subprocess.run(
                args,
                input=token + "\n",
                text=True,
                capture_output=True,
                timeout=60,
                cwd=ROOT,
            )
            if not rejected.returncode or token in rejected.stdout + rejected.stderr:
                raise RuntimeError("Revoked token export was not safely rejected")
            if set(ARCHIVE_ROOT.iterdir()) != before:
                raise RuntimeError("Rejected export created an archive")
        report = {
            "status": "passed",
            "completed_at": datetime.now(UTC).isoformat(),
            "real_cli_export": True,
            "offline_cli_verified": True,
            "revoked_token_denied": True,
            "business_events": summary["business_events"],
            "account_events": summary["account_events"],
            "production_verified": False,
            "independent_archive_verified": False,
        }
    finally:
        if account is not None:
            with engine.begin() as connection:
                cleanup_fixture(
                    connection, {"tenant": account["tenant_id"], "user": account["user_id"]}
                )
        engine.dispose()
    assert directory is not None
    (directory / "acceptance-summary.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return str(directory / "acceptance-summary.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acceptance", action="store_true", required=True)
    parser.parse_args()
    try:
        path = run_acceptance()
    except Exception as error:
        raise SystemExit(
            f"Audit CLI acceptance failed ({type(error).__name__}); output withheld"
        ) from None
    print(f"PASS: real encrypted audit CLI and offline verification; report: {path}")


if __name__ == "__main__":
    main()
