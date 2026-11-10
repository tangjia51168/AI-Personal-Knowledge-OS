"""Explicit opt-in: one imported long source, at most three paid requests.

No retry, no external collection, no secret output. Dedicated local tenant only.
"""

import argparse
import json
import secrets
import time
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from app.modules.notes.content import Evidence, SummaryDocument
from app.modules.notes.quality import validate_quality
from dotenv import dotenv_values
from sqlalchemy import create_engine, text

from scripts.knowledge_acceptance import compose_state, require, response_data
from scripts.operations_check import ROOT, load_acceptance


def run() -> None:
    values = load_acceptance()
    local = dotenv_values(ROOT / ".env", interpolate=False)
    key = local.get("DEEPSEEK_API_KEY") or local.get("DeepSeek_KEY")
    require(bool(key), "DEEPSEEK_KEY_NOT_CONFIGURED")
    directory = ROOT / ".cache" / "long-analysis" / uuid4().hex
    directory.mkdir(parents=True)
    report = {
        "status": "failed",
        "max_paid_requests": 3,
        "max_output_tokens_each": 2048,
        "cost_usd": None,
        "production_verified": False,
        "request_submitted": False,
    }
    account = None
    engine = create_engine(values["MIGRATION_DATABASE_URL"], hide_parameters=True)
    try:
        # Only the existing fixed DeepSeek endpoint is enabled. No new provider
        # credentials are installed into a user's tenant.
        compose_state(False, True, values)
        with httpx.Client(
            base_url="http://127.0.0.1:18000/api/v1",
            timeout=570,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            account = response_data(
                client.post(
                    "/auth/register",
                    json={
                        "email": f"long-{uuid4().hex}@example.invalid",
                        "password": secrets.token_urlsafe(32),
                    },
                ),
                201,
            )
            client.headers["Authorization"] = f"Bearer {account['access_token']}"
            path = f"/spaces/{account['tenant_id']}"
            provider = response_data(
                client.post(
                    path + "/providers",
                    json={
                        "kind": "deepseek",
                        "model": "deepseek-flash",
                        "api_key": key,
                        "enabled": True,
                        "consent": True,
                    },
                ),
                201,
            )
            require(provider["has_key"], "PROVIDER_KEY_MISSING")
            source = response_data(
                client.post(
                    path + "/sources",
                    json={
                        "text": f"https://example.com/long-analysis-{uuid4().hex}",
                        "idempotency_key": str(uuid4()),
                    },
                ),
                201,
            )["sources"][0]
            paragraph = (
                "This is an authored engineering acceptance example, not platform content. "
                "Prepare three samples. Measure latency in milliseconds. "
                "Keep source evidence immutable. "
                "A failed experiment is not proof of a causal relation. "
                "Record conditions and uncertainty.\n"
            )
            body = (
                "开始条件：样本数为3，延迟单位为毫秒。\n"
                + paragraph * 85
                + "\n结尾补充：在高负载条件下延迟为125毫秒，不能当成生产SLA。"
            )
            from app.modules.notes.content import build_evidence
            from app.modules.notes.long_content import batches

            groups = batches(build_evidence(body))
            require(len(groups) == 2, "ACCEPTANCE_CALL_BOUND_INVALID")
            imported = response_data(
                client.post(
                    path + f"/sources/{source['id']}/imports",
                    json={
                        "consent": True,
                        "idempotency_key": str(uuid4()),
                        "title": "长文分段真实模型验收",
                        "text": body,
                        "format": "text",
                        "content_kind": "article",
                    },
                ),
                202,
            )
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                job = response_data(client.get(path + f"/jobs/{imported['id']}"))
                if job["status"] not in {"queued", "running"}:
                    break
                time.sleep(1)
            require(job["status"] == "succeeded", "IMPORT_NOT_SUCCEEDED")
            snapshot = response_data(client.get(path + f"/sources/{source['id']}/snapshots"))[0]
            request = {"consent": True, "idempotency_key": str(uuid4())}
            endpoint = path + f"/snapshots/{snapshot['id']}/summarize"
            # Reserve the entire one-run allowance before any billable call.
            report.update(
                request_submitted=True,
                source_characters=len(body),
                expected_requests=3,
                reserved_at=datetime.now(UTC).isoformat(),
            )
            (directory / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            result = response_data(client.post(endpoint, json=request))
            report.update(run_status=result["status"], run_error_code=result["error_code"])
            usages = response_data(client.get(path + "/ai-usage"))
            report["usage"] = [
                {k: u[k] for k in ("status", "input_tokens", "output_tokens", "cost_usd")}
                for u in usages
            ]
            require(len(usages) <= 3, "PAID_CALL_BOUND_EXCEEDED")
            require(result["status"] == "succeeded", "LONG_ANALYSIS_NOT_SUCCEEDED_NO_RETRY")
            require(response_data(client.post(endpoint, json=request)) == result, "REPLAY_CHANGED")
            require(
                len(response_data(client.get(path + "/ai-usage"))) == len(usages) == 3,
                "REPLAY_CHARGED",
            )
            revision = response_data(
                client.get(path + f"/notes/{result['note_id']}/revisions/{result['revision_id']}")
            )
            document = revision["document"]
            require(result.get("completed_stages") == 3, "DURABLE_STAGES_MISSING")
            require(
                document["prompt_version"] == "evidence-summary-v2"
                and not document["coverage"]["partial"],
                "COVERAGE_INVALID",
            )
            require(len(document["analysis"]["stages"]) == 2, "STAGES_NOT_PERSISTED")
            exported = client.get(
                path
                + f"/notes/{result['note_id']}/revisions/{result['revision_id']}/export?format=json"
            )
            require(exported.status_code == 200, "EXPORT_FAILED")
            data = exported.json()
            validate_quality(
                SummaryDocument.model_validate(document),
                [Evidence(key=e["key"], text=e["quote"]) for e in data["evidence"]],
            )
            require(all(e["quote"] in body for e in data["evidence"]), "QUOTATION_CHANGED")
            (directory / "note.md").write_text(revision["content_md"], encoding="utf-8")
            (directory / "note.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            synthesis_text = " ".join(
                [
                    document["overview"]["text"],
                    *(
                        item["text"]
                        for section in document["sections"]
                        for item in section["items"]
                    ),
                ]
            )
            require(
                "125" in synthesis_text and "毫秒" in synthesis_text and "SLA" in synthesis_text,
                "TAIL_NUMBER_CONDITION_OMITTED_NO_RETRY",
            )
            report.update(
                status="passed",
                idempotent_replay=True,
                exact_quotes=True,
                tail_number_condition_retained=True,
                stages_persisted=True,
                durable_stages=3,
                processing_count_claim_rejected_by_contract=True,
                cited_number_presence_checked=True,
                coverage=document["coverage"],
            )
    except Exception as error:
        report["error_type"] = type(error).__name__
        if type(error).__name__ == "CheckFailed":
            report["error_code"] = str(error)
    finally:
        if account:
            with engine.begin() as connection:
                params = {"tenant": account["tenant_id"], "user": account["user_id"]}
                connection.execute(text("SET LOCAL lock_timeout='5s'"))
                connection.execute(
                    text("SELECT id FROM tenants WHERE id=:tenant FOR UPDATE"), params
                )
                connection.execute(
                    text(
                        "UPDATE ai_providers SET enabled=false,encrypted_key=NULL,key_version=NULL "
                        "WHERE tenant_id=:tenant"
                    ),
                    params,
                )
                connection.execute(text("DELETE FROM auth_sessions WHERE user_id=:user"), params)
                report["fixture_credentials_cleared"] = True
        engine.dispose()
        compose_state(False, False, values)
        report["operator_flags_restored"] = True
        report["completed_at"] = datetime.now(UTC).isoformat()
        (directory / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"{report['status']}: {directory / 'summary.json'}")
    require(report["status"] == "passed", "LONG_ANALYSIS_ACCEPTANCE_FAILED_NO_RETRY")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acceptance", action="store_true", required=True)
    parser.add_argument("--allow-paid-requests", action="store_true", required=True)
    parser.parse_args()
    try:
        run()
    except Exception as error:
        raise SystemExit(
            f"Long analysis acceptance failed ({type(error).__name__}); no retry"
        ) from None
