import asyncio
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.integrations.ai import Generation
from app.modules.ai.service import InvocationConfig
from app.modules.identity.access import ActorIdentity
from app.modules.notes import checkpoints, service
from app.modules.notes.content import build_evidence
from app.modules.notes.long_content import batch_prompt, batches, parse_batch, synthesis_prompt
from fastapi import HTTPException
from pydantic import SecretStr


def output(key):
    return {
        "title": "笔记",
        "overview": {"text": "核对条件。", "evidence_keys": [key]},
        "sections": [{"kind": "steps", "items": [{"text": "准备材料。", "evidence_keys": [key]}]}],
        "limitations": ["协议夹具。"],
    }


@pytest.mark.parametrize("completed", [1, 2, 3])
def test_resume_reuses_successful_outputs_and_never_recharges_them(monkeypatch, completed):
    identity, tenant, run, provider = ActorIdentity(uuid4(), "digest"), uuid4(), uuid4(), uuid4()
    source, title = "材料条件。" * 5000, "title"
    groups = batches(build_evidence(source))
    assert len(groups) == 2
    stages, saved, calls, claimed, committed = [], [], [], [], []
    for i in range(completed):
        prompt = batch_prompt(title, groups[i]) if i < 2 else synthesis_prompt(title, stages)[0]
        data = output(groups[min(i, 1)][-1].key)
        saved.append(
            SimpleNamespace(
                position=i,
                protocol=checkpoints.PROTOCOL,
                provider_id=provider,
                model="deepseek-flash",
                prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                output=data,
            )
        )
        if i < 2:
            stages.append(parse_batch(json.dumps(data, ensure_ascii=False), groups[i]))
    monkeypatch.setattr(checkpoints, "load", lambda *args: saved)
    monkeypatch.setattr(service, "load_input", lambda *args: (title, source))

    def claim(*args):
        claimed.append(args[3])
        return uuid4(), InvocationConfig("deepseek", "deepseek-flash", None, SecretStr("test"))

    monkeypatch.setattr(checkpoints, "claim", claim)
    monkeypatch.setattr(checkpoints, "complete", lambda *args: None)

    async def generate(self, prompt):
        calls.append(prompt)
        return Generation(json.dumps(output(groups[-1][-1].key), ensure_ascii=False), 10, 10)

    monkeypatch.setattr(service.DeepSeekSummaryProvider, "generate", generate)
    monkeypatch.setattr(
        service, "save_document", lambda *args: committed.append(args) or {"status": "succeeded"}
    )
    assert asyncio.run(service.resume_summary(identity, tenant, run))["status"] == "succeeded"
    assert (
        len(calls) == 3 - completed and claimed == list(range(completed, 3)) and len(committed) == 1
    )


def test_resume_rejects_prompt_changes_before_new_call(monkeypatch):
    saved = [
        SimpleNamespace(
            position=0,
            protocol=checkpoints.PROTOCOL,
            provider_id=uuid4(),
            model="deepseek-flash",
            prompt_sha256="0" * 64,
            output=output("E001"),
        )
    ]
    monkeypatch.setattr(checkpoints, "load", lambda *args: saved)
    monkeypatch.setattr(service, "load_input", lambda *args: ("title", "材料条件。" * 5000))
    with pytest.raises(HTTPException) as error:
        asyncio.run(service.resume_summary(ActorIdentity(uuid4(), "digest"), uuid4(), uuid4()))
    assert error.value.detail == {"code": "SUMMARY_CHECKPOINT_INVALID"}
