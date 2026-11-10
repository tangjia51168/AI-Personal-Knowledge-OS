"""One explicit real fallback check through the isolated tenant API.

The first configuration names a unique, nonexistent model and must be rejected.
Only the second configuration targets the authorized deepseek-flash model. There
is one invocation, at most two provider requests, and no retries. If the first
model unexpectedly succeeds, the check fails without submitting another call.
Secrets, prompts, responses and account IDs never enter the report or console.
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

from scripts.deepseek_acceptance import CheckFailed, cleanup_fixture, require
from scripts.operations_check import ROOT, load_acceptance

MODEL = "deepseek-flash"


def run_acceptance() -> Path:
    values = load_acceptance()
    local = dotenv_values(ROOT / ".env", interpolate=False)
    key = local.get("DEEPSEEK_API_KEY") or local.get("DeepSeek_KEY")
    require(bool(key), "DEEPSEEK_KEY_NOT_CONFIGURED")
    directory = ROOT / ".cache" / "model-reports" / uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    report: dict = {
        "status": "failed",
        "provider": "deepseek",
        "model": MODEL,
        "max_provider_requests": 2,
        "max_valid_model_requests": 1,
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
        with httpx.Client(
            base_url="http://127.0.0.1:13000/api/account",
            timeout=70,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = client.post(
                "/auth/register",
                json={
                    "email": f"fallback-{uuid4().hex}@example.invalid",
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
            missing_model = "phase0-definite-missing-" + uuid4().hex
            providers = []
            for priority, model in enumerate((missing_model, MODEL)):
                response = client.post(
                    path + "/providers",
                    json={
                        "kind": "deepseek",
                        "model": model,
                        "api_key": key,
                        "priority": priority,
                        "enabled": True,
                        "consent": True,
                    },
                )
                require(response.status_code == 201, "PROVIDER_CONFIGURATION_FAILED")
                require(key not in response.text, "CREDENTIAL_EXPOSED")
                providers.append(response.json()["data"])
            params = {"tenant": account["tenant_id"]}
            with engine.connect() as connection:
                encrypted = (
                    connection.execute(
                        text("SELECT encrypted_key FROM ai_providers WHERE tenant_id=:tenant"),
                        params,
                    )
                    .scalars()
                    .all()
                )
            require(
                len(encrypted) == 2 and all(value and key not in value for value in encrypted),
                "CREDENTIAL_NOT_ENCRYPTED",
            )
            checks["tenant_keys_encrypted"] = True
            denied = client.post(path + "/ai-test", json={"prompt": "不发送", "consent": False})
            require(denied.status_code == 400, "CONSENT_NOT_ENFORCED")
            checks["consent_required"] = True
            usage = client.get(path + "/ai-usage")
            require(
                usage.status_code == 200 and usage.json()["data"] == [],
                "UNEXPECTED_PRIOR_USAGE",
            )
            # The only invocation. The application stops on an unknown outcome
            # and invokes at most the two freshly created configurations.
            report["request_submitted"] = True
            response = client.post(
                path + "/ai-test", json={"prompt": "请只回复：测试成功", "consent": True}
            )
            require(response.status_code == 200, "REAL_FALLBACK_FAILED_OR_UNKNOWN")
            result = response.json()["data"]
            require(result.get("provider_id") == providers[1]["id"], "UNEXPECTED_PROVIDER")
            require(
                result.get("fallback_errors")
                == [{"provider_id": providers[0]["id"], "code": "PROVIDER_HTTP_ERROR"}],
                "DEFINITE_FAILURE_NOT_OBSERVED",
            )
            require(
                isinstance(result.get("text"), str) and bool(result["text"].strip()),
                "REAL_MODEL_EMPTY_OUTPUT",
            )
            for field in ("input_tokens", "output_tokens"):
                require(type(result.get(field)) is int and result[field] > 0, "USAGE_MISSING")
            require(result["output_tokens"] <= 256, "OUTPUT_TOKEN_LIMIT_EXCEEDED")
            require(result.get("cost_usd") is None, "COST_MUST_REMAIN_UNKNOWN")
            with engine.connect() as connection:
                rows = (
                    connection.execute(
                        text(
                            "SELECT provider_id,model,status,input_tokens,output_tokens,cost_usd,"
                            "error_code FROM ai_usage WHERE tenant_id=:tenant"
                        ),
                        params,
                    )
                    .mappings()
                    .all()
                )
            require(len(rows) == 2, "USAGE_ATTEMPT_COUNT_MISMATCH")
            attempts = {str(row["provider_id"]): row for row in rows}
            require(
                set(attempts) == {provider["id"] for provider in providers},
                "USAGE_PROVIDER_MISMATCH",
            )
            failed, succeeded = (attempts[provider["id"]] for provider in providers)
            require(
                failed["model"] == missing_model
                and failed["status"] == "failed"
                and failed["error_code"] == "PROVIDER_HTTP_ERROR"
                and failed["input_tokens"] is None
                and failed["output_tokens"] is None
                and failed["cost_usd"] is None,
                "DEFINITE_FAILURE_USAGE_MISMATCH",
            )
            require(
                succeeded["model"] == MODEL
                and succeeded["status"] == "succeeded"
                and succeeded["error_code"] is None
                and succeeded["input_tokens"] == result["input_tokens"]
                and succeeded["output_tokens"] == result["output_tokens"]
                and succeeded["cost_usd"] is None,
                "SUCCESS_USAGE_MISMATCH",
            )
            usage = client.get(path + "/ai-usage")
            require(usage.status_code == 200, "USAGE_READ_FAILED")
            visible = usage.json()["data"]
            require(
                len(visible) == 2
                and sorted(row["status"] for row in visible) == ["failed", "succeeded"]
                and all(row["cost_usd"] is None for row in visible),
                "PUBLIC_USAGE_MISMATCH",
            )
            checks.update(
                definite_first_rejection=True,
                real_short_fallback_inference=True,
                durable_usage_matches_response=True,
                cost_unknown=True,
            )
            report["input_tokens"] = result["input_tokens"]
            report["output_tokens"] = result["output_tokens"]
            report["verified_usage_attempts"] = 2
            inference_verified = True
            for provider in providers:
                response = client.post(path + f"/providers/{provider['id']}/disable")
                require(
                    response.status_code == 200 and response.json()["data"]["has_key"] is False,
                    "KEY_CLEAR_FAILED",
                )
            checks["provider_keys_cleared"] = True
            report["status"] = "passed"
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
                    if report["request_submitted"] and not inference_verified:
                        # Keep every uncertain outcome and possible charge for
                        # review; remove reusable credentials and revoke sessions.
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
                        checks["provider_keys_cleared"] = True
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acceptance", action="store_true", required=True)
    parser.add_argument("--allow-paid-request", action="store_true", required=True)
    parser.parse_args()
    try:
        report = run_acceptance()
    except Exception as error:
        raise SystemExit(f"DeepSeek fallback failed ({type(error).__name__}); no retry") from None
    print(f"PASS: one real DeepSeek fallback; sanitized report: {report}")


if __name__ == "__main__":
    main()
