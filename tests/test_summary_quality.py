import json

import pytest
from app.modules.notes.content import Evidence, _ModelSummary, build_evidence
from app.modules.notes.long_content import final_document, parse_batch
from app.modules.notes.quality import validate_quality


def summary(text, key="E001"):
    return _ModelSummary.model_validate(
        {
            "title": "笔记",
            "overview": {"text": text, "evidence_keys": [key]},
            "sections": [{"kind": "numbers", "items": [{"text": text, "evidence_keys": [key]}]}],
            "limitations": ["未经事实核验。"],
        }
    )


@pytest.mark.parametrize(
    "text", ["快照共21段", "覆盖二十七个片段", "provided 21 segments", "chunks: 21", "共27段落"]
)
def test_rejects_model_processing_counts_even_correct_counts(text):
    with pytest.raises(ValueError, match="SUMMARY_PROCESSING_COUNT_CLAIM"):
        validate_quality(summary(text), [Evidence(key="E001", text=text)])


def test_number_must_occur_in_cited_quote_not_some_other_quote():
    evidence = [
        Evidence(key="E001", text="样本数3，高负载125毫秒。"),
        Evidence(key="E002", text="延迟120毫秒。"),
    ]
    validate_quality(summary("高负载125毫秒。"), evidence)
    with pytest.raises(ValueError, match="SUMMARY_NUMBER_UNSUPPORTED"):
        validate_quality(summary("高负载120毫秒。"), evidence)
    with pytest.raises(ValueError, match="SUMMARY_NUMBER_UNSUPPORTED"):
        validate_quality(summary("高负载125毫秒。", "E002"), evidence)


def test_historical_real_count_misstatement_is_now_rejected():
    evidence = build_evidence("可重复测量。" * 5000)
    from app.modules.notes.long_content import batches

    stages = [
        parse_batch(summary("需要核对条件。", group[-1].key).model_dump_json(), group)
        for group in batches(evidence)
    ]
    with pytest.raises(ValueError, match="SUMMARY_PROCESSING_COUNT_CLAIM"):
        final_document(
            json.dumps(
                summary("本笔记仅覆盖提供的21段快照。", evidence[-1].key).model_dump(),
                ensure_ascii=False,
            ),
            evidence,
            stages,
        )
