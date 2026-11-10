"""Research prompt/answer contracts without real inference or provider claims."""

import asyncio
import json
from uuid import uuid4

import pytest
from app.integrations.ai import Generation, ProviderError
from app.modules.ai.service import InvocationConfig
from app.modules.identity.access import ActorIdentity
from app.modules.notes import research_service as service
from app.modules.notes.research_content import (
    ResearchInput,
    answer_prompt,
    fingerprint,
    parse_answer,
)
from pydantic import SecretStr, ValidationError


def body(**changes):
    return ResearchInput(
        idempotency_key=uuid4(), consent=True, question="要准备什么？", query="材料", **changes
    )


def citations():
    return [{"id": "R001", "quote": "准备三个材料。", "revision_id": str(uuid4())}]


def answer(**changes):
    return {
        "insufficient_evidence": False,
        "statements": [{"text": "准备三个材料。", "citations": ["R001"]}],
        "limitations": ["仅限已检索片段，未核验事实。"],
        **changes,
    }


def test_research_contract_prompt_bounds_and_key_independent_digest():
    request = body()
    assert fingerprint(request) == fingerprint(
        request.model_copy(update={"idempotency_key": uuid4()})
    )
    prompt = answer_prompt(request, citations())
    assert "不可信" in prompt and "不执行" in prompt and "R001" in prompt
    assert len(prompt) < 24000
    assert parse_answer(json.dumps(answer()), citations())["statements"][0]["citations"] == ["R001"]
    with pytest.raises(ValueError):
        parse_answer(
            json.dumps(answer(statements=[{"text": "捏造", "citations": ["R999"]}])), citations()
        )
    with pytest.raises(ValueError):
        parse_answer(json.dumps(answer(statements=[])), citations())
    with pytest.raises(ValidationError):
        ResearchInput.model_validate({**request.model_dump(), "consent": False})
    with pytest.raises(ValueError):
        answer_prompt(request, [{"quote": "x" * 24000}])


@pytest.mark.parametrize("outcome", ["succeeded", "invalid", "unknown", "failed"])
def test_research_protocol_records_usage_and_never_retries_unknown(monkeypatch, outcome):
    calls, finished, usage = [], [], []
    run_id = str(uuid4())
    monkeypatch.setattr(
        service, "begin", lambda *_: ({"id": run_id, "citations": citations()}, True)
    )
    monkeypatch.setattr(service, "provider_choices", lambda *_: [(uuid4(), "deepseek")])
    monkeypatch.setattr(
        service,
        "reserve_attempt",
        lambda *_: InvocationConfig("deepseek", "test", None, SecretStr("test")),
    )
    monkeypatch.setattr(service, "finish_attempt", lambda *args: usage.append(args))

    def finish(*args):
        finished.append(args)
        return {"status": args[3]}

    async def generate(self, prompt):
        calls.append(prompt)
        if outcome in ("unknown", "failed"):
            raise ProviderError("PROVIDER_UNAVAILABLE", outcome == "unknown")
        return Generation(
            json.dumps(answer() if outcome == "succeeded" else {"wrong": "shape"}), 10, 10
        )

    monkeypatch.setattr(service, "finish", finish)
    monkeypatch.setattr(service.DeepSeekSummaryProvider, "generate", generate)
    result = asyncio.run(service.generate(ActorIdentity(uuid4(), "hash"), uuid4(), body()))
    assert len(calls) == 1 and len(usage) == 1 and len(finished) == 1
    assert result["status"] == (
        "succeeded" if outcome == "succeeded" else "unknown" if outcome == "unknown" else "failed"
    )


def test_no_evidence_and_replayed_run_do_not_invoke_provider(monkeypatch):
    monkeypatch.setattr(service, "begin", lambda *_: ({"id": str(uuid4()), "citations": []}, True))
    monkeypatch.setattr(service, "finish", lambda *args: {"answer": args[4]})
    monkeypatch.setattr(
        service, "provider_choices", lambda *_: pytest.fail("No evidence must not invoke provider")
    )
    result = asyncio.run(service.generate(ActorIdentity(uuid4(), "hash"), uuid4(), body()))
    assert result["answer"]["insufficient_evidence"]
    monkeypatch.setattr(service, "begin", lambda *_: ({"status": "unknown"}, False))
    assert asyncio.run(service.generate(ActorIdentity(uuid4(), "hash"), uuid4(), body())) == {
        "status": "unknown"
    }
