from uuid import uuid4

import pytest
from app.modules.organization.concept_content import ConceptInput
from pydantic import ValidationError


def body(**changes):
    return {
        "idempotency_key": str(uuid4()),
        "name": "  Ａ 概念  ",
        "definition": " 人工定义\n来源待核验 ",
        "evidence_key": "E001",
        "expected_quote": "原文证据",
        **changes,
    }


def test_concept_normalization_digest_and_exact_evidence():
    value = ConceptInput.model_validate(body())
    assert value.name == "A 概念" and value.definition == "人工定义\n来源待核验"
    note, revision = uuid4(), uuid4()
    replay = value.model_copy(update={"idempotency_key": uuid4()})
    assert replay.digest(note, revision) == value.digest(note, revision)
    assert value.digest(note, uuid4()) != value.digest(note, revision)
    assert value.model_copy(update={"expected_quote": "其他"}).digest(
        note, revision
    ) != value.digest(note, revision)


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "\u202e方向"},
        {"name": "x" * 61},
        {"name": " "},
        {"definition": "\u0000"},
        {"definition": " "},
        {"definition": "\ud800"},
        {"evidence_key": "E１２３"},
        {"evidence_key": "E001\n"},
        {"expected_quote": "\ud800"},
        {"confidence": 1},
        {"method": "ai"},
    ],
)
def test_concept_input_rejects_unbounded_or_forged_fields(changes):
    with pytest.raises(ValidationError):
        ConceptInput.model_validate(body(**changes))
