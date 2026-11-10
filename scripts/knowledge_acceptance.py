"""Opt-in real public collection and bounded paid summary acceptance.

Only the isolated local stack and the fixed public inputs below are used. The
authorization ledger is written before a potentially billable request. No model
retry, fallback provider, secret logging, or production deployment is performed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values
from sqlalchemy import create_engine, text

from scripts.operations_check import ROOT, compose_args, docker_path, load_acceptance

PUBLIC_INPUTS = (
    "https://www.bilibili.com/video/BV1ZsDNBMESZ/",
    "https://www.bilibili.com/video/BV1n7411U7Qp/",
    "https://www.python.org/doc/",
)
REPORT_ROOT = ROOT / ".cache" / "knowledge-real-acceptance"
LEDGER = REPORT_ROOT / "authorized-3-requests-20261107.json"
MODEL = "deepseek-flash"
# Official peak, cache-miss rates checked on the acceptance date. Reserve using
# a deliberately conservative one-token-per-UTF8-byte ceiling plus 256 framing
# tokens, and CNY 8/USD. This is a cap estimate, never an invoice assertion.
INPUT_USD_PER_MILLION = Decimal("0.30")
OUTPUT_USD_PER_MILLION = Decimal("1.20")
USD_CNY_CEILING = Decimal("8")


class CheckFailed(Exception):
    """Fixed non-sensitive diagnostic code."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise CheckFailed(code)


def save_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".writing")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=True), encoding="utf-8")
    temporary.replace(path)


def reserve_paid_request(prompt_bytes: int, run_key: str) -> Decimal:
    require(0 < prompt_bytes <= 96_000, "PROMPT_BOUND_EXCEEDED")
    amount = (
        (
            Decimal(prompt_bytes + 256) * INPUT_USD_PER_MILLION
            + Decimal(2048) * OUTPUT_USD_PER_MILLION
        )
        / Decimal(1_000_000)
        * USD_CNY_CEILING
    )
    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {"requests": []}
    requests = ledger["requests"]
    require(len(requests) < 3, "AUTHORIZED_REQUEST_LIMIT_REACHED")
    require(
        sum((Decimal(row["reserved_cny"]) for row in requests), Decimal(0)) + amount
        <= Decimal("1"),
        "AUTHORIZED_BUDGET_LIMIT_REACHED",
    )
    requests.append(
        {
            "run_key": run_key,
            "model": MODEL,
            "reserved_cny": str(amount),
            "max_output_tokens": 2048,
            "submitted_or_uncertain": True,
            "reserved_at": datetime.now(UTC).isoformat(),
        }
    )
    save_json(LEDGER, ledger)
    return amount


def compose_state(enabled: bool, paid: bool, values: dict[str, str]) -> None:
    args = compose_args(values)
    args[3:3] = ["--env-file", str(ROOT / ".env.storage-runtime.acceptance")]
    args += [
        "-f",
        str(ROOT / "docker-compose.storage-runtime.yml"),
        "-f",
        str(ROOT / "docker-compose.hardened.yml"),
        "up",
        "-d",
        "--no-build",
        "--no-deps",
        "--wait",
        "--wait-timeout",
        "180",
        "api",
        "worker",
        "beat",
    ]
    original = dotenv_values(ROOT / ".env.acceptance", interpolate=False)
    environment = {
        **os.environ,
        "COLLECTOR_ENABLED": "true"
        if enabled
        else str(original.get("COLLECTOR_ENABLED") or "false"),
        "DEEPSEEK_ENABLED": "true" if paid else str(original.get("DEEPSEEK_ENABLED") or "false"),
    }
    completed = subprocess.run(
        [docker_path(), *args], env=environment, capture_output=True, timeout=210, check=False
    )
    require(completed.returncode == 0, "LOCAL_STACK_CONFIGURE_FAILED")


def response_data(response: httpx.Response, expected: int = 200) -> dict | list:
    if response.status_code != expected:
        code = "LOCAL_API_REQUEST_FAILED"
        try:
            candidate = response.json().get("error", {}).get("code", "")
            if isinstance(candidate, str) and re.fullmatch(r"[A-Z_]{1,64}", candidate):
                code = candidate
        except (ValueError, AttributeError):
            pass
        raise CheckFailed(f"LOCAL_API_{response.status_code}_{code}")
    return response.json()["data"]


def collect_source(client: httpx.Client, path: str, url: str) -> dict:
    result: dict = {"url": url, "status": "failed"}
    batch = response_data(
        client.post(path + "/sources", json={"text": url, "idempotency_key": str(uuid4())}), 201
    )
    source = batch["sources"][0]
    require(source["status"] == "accepted", "PUBLIC_SOURCE_REJECTED")
    source_id = source["id"]
    body = {"consent": True, "idempotency_key": str(uuid4())}
    job = response_data(client.post(path + f"/sources/{source_id}/collect", json=body), 202)
    replay = response_data(client.post(path + f"/sources/{source_id}/collect", json=body), 202)
    require(replay["id"] == job["id"], "COLLECTION_REPLAY_CHANGED_JOB")
    deadline = time.monotonic() + 95
    while time.monotonic() < deadline:
        current = response_data(client.get(path + f"/jobs/{job['id']}"))
        if current["status"] not in {"queued", "running"}:
            break
        time.sleep(1)
    else:
        raise CheckFailed("COLLECTION_JOB_OUTCOME_UNKNOWN")
    result.update(job_status=current["status"], error_code=current["error_code"])
    snapshots = response_data(client.get(path + f"/sources/{source_id}/snapshots"))
    if current["status"] != "succeeded":
        require(not snapshots, "FAILED_COLLECTION_CREATED_SNAPSHOT")
        return result
    require(len(snapshots) == 1, "SNAPSHOT_COUNT_INVALID")
    snapshot_id = snapshots[0]["id"]
    snapshot = response_data(client.get(path + f"/sources/{source_id}/snapshots/{snapshot_id}"))
    require(bool(snapshot["body_text"].strip()), "SNAPSHOT_BODY_EMPTY")
    result.update(
        status="passed",
        snapshot_id=snapshot_id,
        platform=snapshot["platform"],
        content_kind=snapshot["content_kind"],
        body_characters=len(snapshot["body_text"]),
        raw_sha256=snapshot["raw_sha256"],
        limitations=snapshot["limitations"],
        idempotent_collection=True,
    )
    return result


def summarize_snapshot(client: httpx.Client, path: str, snapshot: dict, directory: Path) -> dict:
    from app.modules.notes.content import build_evidence, build_prompt

    row = snapshot
    # Read immutable source bytes from the tenant-authorized API, not a replacement fixture.
    sources = response_data(client.get(path + "/sources"))
    source = next(source for source in sources if source["canonical_url"] == row["url"])
    details = response_data(
        client.get(path + f"/sources/{source['id']}/snapshots/{row['snapshot_id']}")
    )
    prefix = "仅元数据/简介，未取得视频字幕：" if details["content_kind"] == "metadata" else ""
    title = (prefix + (details["title"] or "来源内容"))[:300]
    prompt = build_prompt(title, build_evidence(details["body_text"]))
    key = str(uuid4())
    endpoint = path + f"/snapshots/{row['snapshot_id']}/summarize"
    denied = client.post(endpoint, json={"consent": False, "idempotency_key": key})
    require(denied.status_code == 400, "SUMMARY_CONSENT_NOT_ENFORCED")
    reserve = reserve_paid_request(len(prompt.encode("utf-8")), key)
    before = response_data(client.get(path + "/ai-usage"))
    response = client.post(endpoint, json={"consent": True, "idempotency_key": key})
    data = response_data(response)
    row["summary"] = {"status": data["status"], "error_code": data["error_code"]}
    save_json(directory / "latest-source.json", row)
    # Even a definite model refusal is not automatically retried under this authorization.
    require(data["status"] == "succeeded", "SUMMARY_FAILED_OR_OUTCOME_UNKNOWN")
    replay = response_data(client.post(endpoint, json={"consent": True, "idempotency_key": key}))
    require(replay == data, "SUMMARY_IDEMPOTENCY_FAILED")
    after = response_data(client.get(path + "/ai-usage"))
    require(len(after) == len(before) + 1, "UNEXPECTED_MODEL_REQUEST_COUNT")
    usage = next(item for item in after if item["id"] not in {old["id"] for old in before})
    require(usage["status"] == "succeeded", "USAGE_NOT_SUCCEEDED")
    require(
        type(usage["input_tokens"]) is int
        and type(usage["output_tokens"]) is int
        and usage["output_tokens"] <= 2048,
        "REAL_USAGE_INVALID",
    )
    require(usage["cost_usd"] is None, "UNKNOWN_INVOICE_COST_FABRICATED")
    note = response_data(client.get(path + f"/notes/{data['note_id']}"))
    revision = note["revision"]
    require(revision["id"] == data["revision_id"], "REVISION_MISMATCH")
    require(bool(revision["document"]["coverage"]["cited_segments"]), "NO_CITED_EVIDENCE")
    output = directory / f"note-{row['platform']}-{row['snapshot_id']}.md"
    output.write_text(revision["content_md"], encoding="utf-8")
    row["summary"].update(
        model=MODEL,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        reserved_cny=str(reserve),
        cost_usd=None,
        idempotent_replay=True,
        markdown_file=str(output),
    )
    return row


def run_acceptance(paid: bool, alternate_source: bool = False, final_source: bool = False) -> Path:
    values = load_acceptance()
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    directory = REPORT_ROOT / uuid4().hex
    directory.mkdir(exist_ok=False)
    report: dict = {
        "status": "failed",
        "public_platforms_verified": False,
        "production_verified": False,
        "authorized_max_requests": 3,
        "authorized_budget_cny": "1",
        "sources": [],
        "pricing_reference": "https://api-docs.deepseek.com/quick_start/pricing/",
    }
    local = dotenv_values(ROOT / ".env", interpolate=False)
    key = local.get("DEEPSEEK_API_KEY") or local.get("DeepSeek_KEY")
    require(not paid or bool(key), "DEEPSEEK_KEY_NOT_CONFIGURED")
    # A second concurrent acceptance must not race the persistent paid-call ledger.
    lock = REPORT_ROOT / "active.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    account = None
    engine = create_engine(
        values["MIGRATION_DATABASE_URL"], hide_parameters=True, connect_args={"connect_timeout": 3}
    )
    try:
        compose_state(True, paid, values)
        with httpx.Client(
            base_url="http://127.0.0.1:13000/api/account",
            timeout=145,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            account = response_data(
                client.post(
                    "/auth/register",
                    json={
                        "email": f"knowledge-{uuid4().hex}@example.invalid",
                        "password": secrets.token_urlsafe(32),
                    },
                ),
                201,
            )
            client.headers["Authorization"] = f"Bearer {account['access_token']}"
            path = f"/spaces/{account['tenant_id']}"
            inputs = (
                ("https://www.python.org/about/",)
                if final_source
                else ("https://api-docs.deepseek.com/quick_start/pricing/",)
                if alternate_source
                else PUBLIC_INPUTS
            )
            for url in inputs:
                row = collect_source(client, path, url)
                report["sources"].append(row)
                save_json(directory / "summary.json", report)
            successes = [item for item in report["sources"] if item["status"] == "passed"]
            require(bool(successes), "NO_REAL_PUBLIC_SNAPSHOT")
            if paid:
                provider = response_data(
                    client.post(
                        path + "/providers",
                        json={
                            "kind": "deepseek",
                            "model": MODEL,
                            "api_key": key,
                            "enabled": True,
                            "consent": True,
                        },
                    ),
                    201,
                )
                require(provider["has_key"], "TENANT_PROVIDER_KEY_NOT_SET")
                for row in successes:
                    summarize_snapshot(client, path, row, directory)
                    save_json(directory / "summary.json", report)
            report["public_platforms_verified"] = all(
                item["status"] == "passed" for item in report["sources"]
            )
            report["status"] = "passed" if report["public_platforms_verified"] else "partial"
    except Exception as error:
        report["error_type"] = type(error).__name__
        if isinstance(error, CheckFailed):
            report["error_code"] = str(error)
    finally:
        try:
            if account:
                params = {"tenant": account["tenant_id"], "user": account["user_id"]}
                with engine.begin() as connection:
                    connection.execute(text("SET LOCAL lock_timeout='5s'"))
                    connection.execute(text("SET LOCAL statement_timeout='10s'"))
                    connection.execute(
                        text("SELECT id FROM tenants WHERE id=:tenant FOR UPDATE"), params
                    )
                    connection.execute(
                        text(
                            "UPDATE ai_providers SET enabled=false,encrypted_key=NULL,"
                            "key_version=NULL "
                            "WHERE tenant_id=:tenant"
                        ),
                        params,
                    )
                    connection.execute(
                        text("DELETE FROM auth_sessions WHERE user_id=:user"), params
                    )
                    report["credentials_cleared_and_sessions_revoked"] = True
                    report["isolated_fixture_retained_for_recovery"] = True
        except Exception as error:
            report["status"] = "failed"
            report["cleanup_error_type"] = type(error).__name__
        finally:
            engine.dispose()
        try:
            compose_state(False, False, values)
            report["operator_flags_restored"] = True
        except Exception as error:
            report["status"] = "failed"
            report["restore_error_type"] = type(error).__name__
        report["completed_at"] = datetime.now(UTC).isoformat()
        save_json(directory / "summary.json", report)
        lock.unlink()
    print(f"{report['status'].upper()}: report: {directory / 'summary.json'}")
    require(report["status"] in {"passed", "partial"}, "REAL_KNOWLEDGE_ACCEPTANCE_FAILED")
    return directory / "summary.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acceptance", action="store_true", required=True)
    parser.add_argument("--allow-paid-requests", action="store_true")
    parser.add_argument("--alternate-source", action="store_true")
    parser.add_argument("--final-source", action="store_true")
    args = parser.parse_args()
    try:
        run_acceptance(args.allow_paid_requests, args.alternate_source, args.final_source)
    except Exception as error:
        raise SystemExit(
            f"Knowledge acceptance failed ({type(error).__name__}); no retry"
        ) from None


if __name__ == "__main__":
    main()
