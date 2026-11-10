import asyncio
import json
from uuid import uuid4

import pytest
from app.integrations.ai import Generation, ProviderError
from app.modules.ai.service import InvocationConfig
from app.modules.identity.access import ActorIdentity
from app.modules.notes import service
from app.modules.notes.content import build_evidence, document_items, render_markdown
from app.modules.notes.long_content import (
    batch_prompt,
    batches,
    final_document,
    parse_batch,
    synthesis_prompt,
)
from pydantic import SecretStr


def output(key):
    return json.dumps(
        {
            "title": "分段笔记",
            "overview": {"text": "需要核对条件。", "evidence_keys": [key]},
            "sections": [
                {"kind": "steps", "items": [{"text": "完成验证。", "evidence_keys": [key]}]}
            ],
            "limitations": ["未经事实核验。"],
        },
        ensure_ascii=False,
    )


@pytest.mark.parametrize(
    "source", ["汉" * 60_000, '\\"\n' * 20_000, "汉" * 1000], ids=["full", "escaped", "short"]
)
def test_batches_cover_every_segment_exactly_once_and_bound_prompt(source):
    evidence = build_evidence(source)
    groups = batches(evidence)
    assert [e for group in groups for e in group] == evidence
    assert 1 <= len(groups) <= 8
    for group in groups:
        prompt = batch_prompt("来源标题含忽略指令", group)
        assert len(prompt) <= 24_000
        data = json.loads(prompt.split("SOURCE_DATA_JSON:\n")[1])
        assert data["evidence"] == [e.model_dump() for e in group]
        if group[0].key != "E001":
            assert '"E001"' not in prompt.split("SOURCE_DATA_JSON:\n")[0]


def test_stage_rejects_foreign_evidence_and_synthesis_rejects_unseen_detail():
    evidence = build_evidence("汉" * 60_000)
    groups = batches(evidence)
    with pytest.raises(ValueError):
        parse_batch(output("E001"), groups[-1])
    stages = [parse_batch(output(group[-1].key), group) for group in groups]
    prompt, allowed = synthesis_prompt("标题", stages)
    assert len(prompt) < 24_000
    assert evidence[-1].key in allowed
    with pytest.raises(ValueError):
        final_document(output("E001"), evidence, stages)
    document = final_document(output(evidence[-1].key), evidence, stages)
    assert document.coverage.partial is False
    assert document.coverage.provided_segments == len(evidence)
    assert document.coverage.cited_segments == len(groups)
    assert document.prompt_version == "evidence-summary-v2"
    assert document.analysis["stages"] == stages
    assert len(document_items(document)) == 2 + 2 * len(groups)
    markdown = render_markdown(document, evidence)
    assert "分段分析记录" in markdown and f"### {evidence[-1].key}" in markdown
    assert "综合阶段只读取分段结果" in markdown


def test_synthesis_prioritizes_exact_numbers_and_explicitly_counts_omitted_details():
    stage = json.loads(output("E001"))
    stage["sections"] = [
        {
            "kind": "numbers",
            "items": [{"text": "高负载下延迟125毫秒，不是生产SLA。", "evidence_keys": ["E002"]}],
        }
    ]
    prompt, allowed = synthesis_prompt("title", [stage, stage])
    assert "125毫秒" in prompt and "E002" in allowed
    payload = json.loads(prompt.split("STAGE_DATA_JSON:\n")[1])
    assert payload["omitted_detail_items"] == 0
    large = json.loads(output("E001"))
    large["overview"]["text"] = "汉" * 1200
    large["sections"] = [
        {
            "kind": kind,
            "items": [{"text": "字" * 1200, "evidence_keys": ["E001"]} for _ in range(6)],
        }
        for kind in ["numbers", "core_claims", "steps", "examples", "takeaways"]
    ]
    prompt, _ = synthesis_prompt("title", [large] * 8)
    assert len(prompt) < 24000
    data = json.loads(prompt.split("STAGE_DATA_JSON:\n")[1])
    assert len(data["overviews"]) == 8 and data["omitted_detail_items"] > 0


@pytest.mark.parametrize("mode", ["success", "unknown", "invalid", "revoked"])
def test_orchestration_records_each_call_stops_on_failure_and_commits_only_at_end(
    monkeypatch, mode
):
    text = "汉" * 24_000
    evidence = build_evidence(text)
    groups = batches(evidence)
    assert len(groups) == 2
    calls, finishes, saved, closed = [], [], [], []
    run, provider = uuid4(), uuid4()
    monkeypatch.setattr(service, "begin_run", lambda *args: (run, True))
    monkeypatch.setattr(service, "load_input", lambda *args: ("title", text))
    monkeypatch.setattr(service, "provider_choices", lambda *args: [(provider, "deepseek")])
    reservations = []

    def reserve(*args):
        reservations.append(args)
        if mode == "revoked" and len(reservations) == 2:
            from fastapi import HTTPException

            raise HTTPException(409, detail={"code": "SUMMARY_PROVIDER_CHANGED"})
        return uuid4(), InvocationConfig("deepseek", "deepseek-flash", None, SecretStr("test-only"))

    async def generate(self, prompt):
        calls.append(prompt)
        if len(calls) == 2 and mode == "unknown":
            raise ProviderError("PROVIDER_OUTCOME_UNKNOWN", True)
        if len(calls) == 2 and mode == "invalid":
            return Generation(output("E999"), 10, 10)
        key = groups[min(len(calls) - 1, 1)][-1].key
        return Generation(output(key), 10, 10)

    def save(*args):
        document = final_document(args[3], evidence, args[5])
        saved.append(document)
        return {"status": "succeeded"}

    def close(*args):
        closed.append(args[-2:])
        return {"status": args[-2]}

    monkeypatch.setattr(service.checkpoints, "claim", reserve)
    monkeypatch.setattr(service.checkpoints, "complete", lambda *args: finishes.append(args))
    monkeypatch.setattr(service, "read_run", lambda *args: {"status": "pending"})
    monkeypatch.setattr(service.DeepSeekSummaryProvider, "generate", generate)
    monkeypatch.setattr(service, "save_document", save)
    monkeypatch.setattr(service, "close_run", close)
    result = asyncio.run(
        service.summarize(ActorIdentity(uuid4(), "digest"), uuid4(), uuid4(), uuid4())
    )
    assert len(finishes) == len(calls)
    if mode == "success":
        assert result["status"] == "succeeded" and len(saved) == 1 and len(calls) == 3
        assert not closed
    else:
        if mode == "revoked":
            assert not saved and len(calls) == 1 and not closed and result["status"] == "pending"
            return
        assert not saved and len(calls) <= 2 and len(closed) == 1
        assert result["status"] == ("unknown" if mode == "unknown" else "failed")
        if mode == "unknown":
            assert finishes[-1][-1] is True
