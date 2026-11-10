"""Input boundaries and immutable paragraph anchors, without services."""

from uuid import uuid4

import pytest
from app.modules.notes.annotation_content import AnnotationInput, paragraph
from pydantic import ValidationError


def data(**changes):
    return {
        "idempotency_key": str(uuid4()),
        "kind": "overview",
        "item_index": 0,
        "expected_quote": "原段",
        "body": "个人观点",
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"body": "   "},
        {"body": "a" * 2001},
        {"body": "x\x00y"},
        {"body": "x\u202ey"},
        {"body": "\ud800"},
        {"expected_quote": "\ud800"},
        {"kind": "personal_notes"},
        {"item_index": True},
        {"item_index": -1},
        {"item_index": 6},
        {"unexpected": "reject"},
    ],
)
def test_annotation_invalid_input(changes):
    with pytest.raises(ValidationError):
        AnnotationInput.model_validate(data(**changes))


def test_annotation_plain_text_is_bounded_but_not_executed_or_inferred():
    value = AnnotationInput.model_validate(data(body="  我的理解\n<script>仅作为文本</script>\t  "))
    assert value.body == "我的理解\n<script>仅作为文本</script>"
    assert AnnotationInput.model_validate(data(body="😀" * 2000)).body == "😀" * 2000


def test_digest_binds_content_note_revision_but_not_retry_key():
    note, revision = uuid4(), uuid4()
    first = AnnotationInput.model_validate(data())
    retry = AnnotationInput.model_validate(data())
    assert first.digest(note, revision) == retry.digest(note, revision)
    assert first.digest(note, revision) != first.digest(uuid4(), revision)
    assert first.digest(note, revision) != first.digest(note, uuid4())
    assert first.digest(note, revision) != AnnotationInput.model_validate(
        data(body="另一观点")
    ).digest(note, revision)


def test_paragraph_extracts_version_content_and_rejects_invalid_anchor():
    document = {
        "overview": {"text": "概览原文"},
        "sections": [{"kind": "steps", "items": [{"text": "第一段"}, {"text": "第二段"}]}],
    }
    assert paragraph(document, "overview", 0) == "概览原文"
    assert paragraph(document, "steps", 1) == "第二段"
    for kind, index in (("overview", 1), ("steps", 2), ("examples", 0)):
        with pytest.raises(ValueError, match="ANNOTATION_ANCHOR_INVALID"):
            paragraph(document, kind, index)
