"""Summary protocol and charge-uncertainty boundaries, without paid requests."""

import asyncio
import copy
import json
import threading
from uuid import uuid4

import pytest
from app.integrations import summary
from app.integrations.ai import Generation, ProviderError
from app.modules.ai.service import InvocationConfig
from app.modules.identity.access import ActorIdentity
from app.modules.notes import service
from app.modules.notes.content import build_evidence, parse_summary, render_markdown
from fastapi import HTTPException
from pydantic import SecretStr


def successful_response():
    return {
        "choices": [{"finish_reason": "stop", "message": {"content": '{"title":"笔记"}'}}],
        "usage": {"prompt_tokens": 123, "completion_tokens": 456},
    }


def test_summary_uses_fixed_endpoint_json_non_thinking_and_authorized_output_limit(monkeypatch):
    calls = []

    async def request(url, payload, key, *, total_timeout):
        assert total_timeout == 60
        calls.append((url, payload, key))
        return successful_response()

    monkeypatch.setattr(summary, "request_json", request)
    result = asyncio.run(
        summary.DeepSeekSummaryProvider("deepseek-flash", SecretStr("key")).generate("真实快照证据")
    )
    assert result.input_tokens == 123 and result.output_tokens == 456
    assert len(calls) == 1
    endpoint, payload, key = calls[0]
    assert endpoint == "https://api.deepseek.com/chat/completions"
    assert payload["max_tokens"] == 2048
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["stream"] is False
    assert key.get_secret_value() == "key"


@pytest.mark.parametrize("finish", ["length", "content_filter", "tool_calls", None])
def test_incomplete_summary_outcome_is_unknown_and_not_retried(monkeypatch, finish):
    response, calls = successful_response(), []
    response["choices"][0]["finish_reason"] = finish

    async def request(*args, **kwargs):
        calls.append(1)
        return response

    monkeypatch.setattr(summary, "request_json", request)
    with pytest.raises(ProviderError) as error:
        asyncio.run(
            summary.DeepSeekSummaryProvider("deepseek-flash", SecretStr("key")).generate("x")
        )
    assert error.value.code == "PROVIDER_RESPONSE_INCOMPLETE" and error.value.outcome_unknown
    assert len(calls) == 1


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"prompt_tokens": 1},
        {"prompt_tokens": True, "completion_tokens": 1},
        {"prompt_tokens": 1, "completion_tokens": -1},
        {"prompt_tokens": 1, "completion_tokens": 2049},
        {"prompt_tokens": 0, "completion_tokens": 1},
    ],
)
def test_missing_or_impossible_usage_is_unknown(monkeypatch, usage):
    response = successful_response()
    response["usage"] = usage

    async def request(*args, **kwargs):
        return response

    monkeypatch.setattr(summary, "request_json", request)
    with pytest.raises(ProviderError) as error:
        asyncio.run(
            summary.DeepSeekSummaryProvider("deepseek-flash", SecretStr("key")).generate("x")
        )
    assert error.value.code == "PROVIDER_USAGE_INVALID" and error.value.outcome_unknown


def test_oversized_input_never_reaches_transport(monkeypatch):
    async def request(*args, **kwargs):
        raise AssertionError("Transport must not be called")

    monkeypatch.setattr(summary, "request_json", request)
    with pytest.raises(ProviderError) as error:
        asyncio.run(
            summary.DeepSeekSummaryProvider("deepseek-flash", SecretStr("key")).generate(
                "x" * 24001
            )
        )
    assert error.value.code == "PROVIDER_INPUT_LIMIT" and not error.value.outcome_unknown


def test_metadata_limit_is_server_generated_in_document_and_markdown():
    evidence = build_evidence("视频标题：电力电子入门\n作者：公开作者")
    raw = json.dumps(
        {
            "title": "电力电子入门",
            "overview": {"text": "视频标题涉及电力电子。", "evidence_keys": ["E001"]},
            "sections": [
                {
                    "kind": "core_claims",
                    "items": [{"text": "作者为公开作者。", "evidence_keys": ["E001"]}],
                }
            ],
            "limitations": ["模型未主动说明内容局限"] * 8,
        },
        ensure_ascii=False,
    )
    document = parse_summary(raw, evidence)
    unchanged = copy.deepcopy(document)
    scoped = service.snapshot_scope(document, "metadata")
    assert document == unchanged
    assert scoped.title.startswith("元数据笔记：")
    assert len(scoped.limitations) <= 8
    assert "不是视频内容总结" in scoped.limitations[0]
    assert "不是视频内容总结" in render_markdown(scoped, evidence)
    assert service.snapshot_scope(document, "article") is document


def test_replay_database_phase_runs_outside_async_event_loop_thread(monkeypatch):
    main_thread = threading.get_ident()
    run_id = uuid4()
    identity = ActorIdentity(uuid4(), "opaque-hash")
    monkeypatch.setattr(service, "begin_run", lambda *args: (run_id, False))

    def read_run(*args):
        assert threading.get_ident() != main_thread
        return {"run_id": str(run_id), "status": "unknown"}

    monkeypatch.setattr(service, "read_run", read_run)
    assert (
        asyncio.run(service.summarize(identity, uuid4(), uuid4(), uuid4()))["status"] == "unknown"
    )


@pytest.mark.parametrize("status", [None, 503])
def test_unknown_provider_stops_second_choice_and_retains_unknown_run(monkeypatch, status):
    provider_id, second_id, run_id = uuid4(), uuid4(), uuid4()
    reservations, closes, finishes = [], [], []
    monkeypatch.setattr(service, "begin_run", lambda *args: (run_id, True))
    monkeypatch.setattr(
        service,
        "provider_choices",
        lambda *args: [(provider_id, "deepseek"), (second_id, "deepseek")],
    )
    monkeypatch.setattr(service, "load_input", lambda *args: ("title", "real source"))

    def reserve(*args):
        reservations.append(args[2])
        return InvocationConfig("deepseek", "deepseek-flash", None, SecretStr("key"))

    async def unknown(*args):
        raise ProviderError("PROVIDER_OUTCOME_UNKNOWN", True, http_status=status)

    def close(*args):
        closes.append(args[-2:])
        return {"status": args[-2]}

    monkeypatch.setattr(service, "reserve_attempt", reserve)
    monkeypatch.setattr(service.DeepSeekSummaryProvider, "generate", unknown)
    monkeypatch.setattr(service, "finish_attempt", lambda *args: finishes.append(args))
    monkeypatch.setattr(service, "close_run", close)
    result = asyncio.run(
        service.summarize(ActorIdentity(uuid4(), "hash"), uuid4(), uuid4(), uuid4())
    )
    assert result["status"] == "unknown"
    assert reservations == [provider_id]
    assert finishes[0][-1] is True
    assert closes == [("unknown", "MODEL_HTTP_503_UNKNOWN" if status else "MODEL_OUTCOME_UNKNOWN")]


def test_post_request_accounting_failure_cannot_release_unknown_run_for_recharge(monkeypatch):
    run_id, closes = uuid4(), []
    monkeypatch.setattr(service, "begin_run", lambda *args: (run_id, True))
    monkeypatch.setattr(service, "provider_choices", lambda *args: [(uuid4(), "deepseek")])
    monkeypatch.setattr(service, "load_input", lambda *args: ("title", "real source"))
    monkeypatch.setattr(
        service,
        "reserve_attempt",
        lambda *args: InvocationConfig("deepseek", "deepseek-flash", None, SecretStr("key")),
    )

    async def generate(*args):
        return Generation("{}", 1, 1)

    def finish(*args):
        raise HTTPException(409, detail={"code": "MODEL_RESERVATION_CHANGED"})

    def close(*args):
        closes.append(args[-2:])
        return {"status": args[-2]}

    monkeypatch.setattr(service.DeepSeekSummaryProvider, "generate", generate)
    monkeypatch.setattr(service, "finish_attempt", finish)
    monkeypatch.setattr(service, "close_run", close)
    result = asyncio.run(
        service.summarize(ActorIdentity(uuid4(), "hash"), uuid4(), uuid4(), uuid4())
    )
    assert result["status"] == "unknown"
    assert closes == [("unknown", "MODEL_OUTCOME_UNKNOWN")]
