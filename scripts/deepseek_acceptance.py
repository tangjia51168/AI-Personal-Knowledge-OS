"""One explicitly authorized paid request through the isolated tenant API.

Credentials stay in memory. Reports contain no secrets, prompt, output or account IDs.
No retry, alternate provider, arbitrary URL or user-device connection is allowed.
"""

from __future__ import annotations

import argparse
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values
from sqlalchemy import create_engine, text

from scripts.operations_check import ROOT, load_acceptance


class CheckFailed(Exception):
    """Only fixed local check codes, never exception text from a dependency."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise CheckFailed(code)


def run_acceptance() -> Path:
    values = load_acceptance()
    local = dotenv_values(ROOT / ".env", interpolate=False)
    # Legacy name supports the user's existing local file without copying the key.
    key = local.get("DEEPSEEK_API_KEY") or local.get("DeepSeek_KEY")
    require(bool(key), "DEEPSEEK_KEY_NOT_CONFIGURED")
    directory = ROOT / ".cache" / "model-reports" / uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    report: dict = {
        "status": "failed",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "max_real_requests": 1,
        "request_submitted": False,
        "production_verified": False,
        "checks": {},
    }
    checks = report["checks"]
    account = None
    inference_verified = False
    engine = create_engine(
        values["MIGRATION_DATABASE_URL"],
        hide_parameters=True,
        connect_args={"connect_timeout": 3},
    )
    try:
        # The browser's fixed BFF paths exercise the same flow as the models page.
        with httpx.Client(
            base_url="http://127.0.0.1:13000/api/account",
            timeout=70,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = client.post(
                "/auth/register",
                json={
                    "email": f"deepseek-{uuid4().hex}@example.invalid",
                    "password": secrets.token_urlsafe(32),
                },
            )
            require(response.status_code == 201, "FIXTURE_REGISTER_FAILED")
            account = response.json()["data"]
            client.headers["Authorization"] = f"Bearer {account['access_token']}"
            path = f"/spaces/{account['tenant_id']}"
            options = client.get(path + "/model-options")
            require(
                options.status_code == 200 and options.json()["data"]["deepseek_enabled"],
                "DEEPSEEK_DISABLED_BY_OPERATOR",
            )
            response = client.post(
                path + "/providers",
                json={
                    "kind": "deepseek",
                    "model": "deepseek-chat",
                    "api_key": key,
                    "enabled": True,
                    "consent": True,
                },
            )
            require(response.status_code == 201, "PROVIDER_CONFIGURATION_FAILED")
            provider = response.json()["data"]
            require(key not in response.text, "CREDENTIAL_EXPOSED")
            with engine.connect() as connection:
                encrypted = connection.execute(
                    text("SELECT encrypted_key FROM ai_providers WHERE id=:id"),
                    {"id": provider["id"]},
                ).scalar_one()
            require(bool(encrypted) and key not in encrypted, "CREDENTIAL_NOT_ENCRYPTED")
            checks["tenant_key_encrypted"] = True
            denied = client.post(path + "/ai-test", json={"prompt": "不发送", "consent": False})
            require(denied.status_code == 400, "CONSENT_NOT_ENFORCED")
            checks["consent_required"] = True
            # This is the only request in this script that can reach a model.
            report["request_submitted"] = True
            response = client.post(
                path + "/ai-test", json={"prompt": "请只回复：测试成功", "consent": True}
            )
            require(response.status_code == 200, "REAL_MODEL_REQUEST_FAILED_OR_UNKNOWN")
            result = response.json()["data"]
            require(
                isinstance(result.get("text"), str) and bool(result["text"].strip()),
                "REAL_MODEL_EMPTY_OUTPUT",
            )
            require(result["provider_id"] == provider["id"], "UNEXPECTED_PROVIDER")
            require(result["fallback_errors"] == [], "UNEXPECTED_FALLBACK")
            for field in ("input_tokens", "output_tokens"):
                require(type(result.get(field)) is int and result[field] > 0, "USAGE_MISSING")
            report["input_tokens"] = result["input_tokens"]
            report["output_tokens"] = result["output_tokens"]
            checks["real_short_inference"] = True
            response = client.get(path + "/ai-usage")
            require(response.status_code == 200, "USAGE_READ_FAILED")
            rows = response.json()["data"]
            require(len(rows) == 1 and rows[0]["status"] == "succeeded", "USAGE_NOT_DURABLE")
            require(
                rows[0]["input_tokens"] == result["input_tokens"]
                and rows[0]["output_tokens"] == result["output_tokens"],
                "USAGE_MISMATCH",
            )
            require(rows[0]["cost_usd"] is None, "COST_MUST_REMAIN_UNKNOWN")
            checks["durable_usage_matches_response"] = True
            checks["cost_unknown"] = True
            inference_verified = True
            response = client.post(path + f"/providers/{provider['id']}/disable")
            require(
                response.status_code == 200 and response.json()["data"]["has_key"] is False,
                "KEY_CLEAR_FAILED",
            )
            checks["provider_key_cleared"] = True
            report["status"] = "passed"
    except Exception as error:
        # Exception messages may contain credentials or transport request details.
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
                    if report["request_submitted"] and not inference_verified:
                        # Keep reservations on every uncertain path. No retry and no
                        # loss of possible charges, even when the client timed out.
                        connection.execute(
                            text(
                                "UPDATE ai_providers SET enabled=false, encrypted_key=NULL, "
                                "key_version=NULL WHERE tenant_id=:tenant"
                            ),
                            params,
                        )
                        connection.execute(
                            text("DELETE FROM auth_sessions WHERE user_id=:user"), params
                        )
                        report["fixture_retained_for_review"] = True
                        checks["provider_key_cleared"] = True
                    else:
                        cleanup_fixture(connection, params)
                        checks["own_fixture_cleaned"] = True
        except Exception as error:
            report["status"] = "failed"
            report["cleanup_error_type"] = type(error).__name__
        finally:
            engine.dispose()
        report["completed_at"] = datetime.now(UTC).isoformat()
        report_path = directory / "summary.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if report["status"] != "passed":
        raise RuntimeError(f"Acceptance failed; sanitized report: {report_path}")
    return report_path


def cleanup_fixture(connection, params: dict[str, str]) -> None:
    for table in ("audit_events", "ai_usage", "ai_providers", "user_settings", "tenant_members"):
        connection.execute(text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), params)
    for table in ("auth_security_events", "auth_action_tokens", "auth_sessions", "auth_identities"):
        connection.execute(text(f"DELETE FROM {table} WHERE user_id=:user"), params)
    connection.execute(text("DELETE FROM tenants WHERE id=:tenant"), params)
    connection.execute(text("DELETE FROM users WHERE id=:user"), params)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acceptance", action="store_true", required=True)
    parser.add_argument("--allow-paid-request", action="store_true", required=True)
    parser.parse_args()
    try:
        report = run_acceptance()
    except Exception as error:
        raise SystemExit(f"DeepSeek acceptance failed ({type(error).__name__}); no retry") from None
    print(f"PASS: one real DeepSeek request; sanitized report: {report}")


if __name__ == "__main__":
    main()
