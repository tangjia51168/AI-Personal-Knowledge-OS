"""Human edits remain bounded, source-bound and visibly separate from inference."""

from uuid import uuid4

import pytest
from app.modules.notes.content import build_evidence
from app.modules.notes.edit_content import (
    EDIT_DISCLOSURE,
    EditedDocument,
    NoteCreateInput,
    NoteEditInput,
    edited_summary,
    render_edited_markdown,
    request_digest,
)
from app.modules.notes.service import snapshot_scope
from pydantic import ValidationError


def edit_body(**changes):
    return {
        "idempotency_key": str(uuid4()),
        "base_revision_id": str(uuid4()),
        "title": "材料验证笔记",
        "overview": {"text": "先准备材料再验证。", "evidence_keys": ["E001"]},
        "sections": [
            {"kind": "steps", "items": [{"text": "准备3个材料。", "evidence_keys": ["E001"]}]}
        ],
        "personal_notes": "我准备结合自己的实验复核。",
        **changes,
    }


def test_human_edit_recomputes_coverage_preserves_scope_and_renders_plain_understanding():
    evidence = build_evidence("先准备3个材料，再验证输出。")
    body = NoteEditInput.model_validate(edit_body())
    summary = edited_summary(body, {"limitations": ["协议输入，不代表模型生成"]}, evidence)
    summary = snapshot_scope(summary, "transcript", "user_import")
    document = EditedDocument(**summary.model_dump(), personal_notes=body.personal_notes)
    markdown = render_edited_markdown(document, evidence)
    assert document.coverage.cited_segments == 1
    assert document.editor == "user" and document.edit_disclaimer == EDIT_DISCLOSURE
    assert "本次没有调用模型" in markdown and "本次向模型提供" not in markdown
    assert "个人理解未作来源或事实核验" in markdown
    assert "未验证其与关联平台或视频一致" in markdown
    assert "协议输入，不代表模型生成" in markdown
    assert "我准备结合自己的实验复核" in markdown
    assert "### E001" in markdown
    assert snapshot_scope(document, "transcript", "user_import").title == document.title


def test_human_edit_rejects_fabricated_or_unprovided_evidence():
    evidence = build_evidence("a" * 60_000)
    for key in ("E999", "E060"):
        body = NoteEditInput.model_validate(
            edit_body(overview={"text": "有待复核。", "evidence_keys": [key]})
        )
        with pytest.raises(ValueError, match="SUMMARY_EVIDENCE_INVALID"):
            edited_summary(body, {"limitations": []}, evidence)


@pytest.mark.parametrize(
    "field", ["run_id", "snapshot_id", "content_md", "coverage", "limitations"]
)
def test_human_edit_disallows_client_control_of_provenance_and_rendered_fields(field):
    with pytest.raises(ValidationError):
        NoteEditInput.model_validate(edit_body(**{field: "client-control"}))


@pytest.mark.parametrize(
    "changes",
    [
        {"title": " "},
        {"title": "a" * 201},
        {"personal_notes": "a" * 4001},
        {"personal_notes": "\x00unsafe"},
        {"personal_notes": "\ud800"},
        {"personal_notes": "<script>unsafe</script>"},
        {"personal_notes": "[unsafe](javascript:payload)"},
        {"overview": {"text": "x", "evidence_keys": []}},
        {"sections": []},
        {"sections": [{"kind": "steps", "items": []}]},
    ],
)
def test_human_edit_rejects_invalid_or_unsafe_text(changes):
    with pytest.raises(ValidationError):
        NoteEditInput.model_validate(edit_body(**changes))


def test_human_edit_bounds_utf8_bytes_independently_of_character_counts():
    item = {"text": "汉" * 1200, "evidence_keys": ["E001"]}
    sections = [
        {"kind": kind, "items": [item] * 6}
        for kind in ("core_claims", "steps", "examples", "numbers", "takeaways")
    ]
    with pytest.raises(ValidationError, match="NOTE_EDIT_LIMIT"):
        NoteEditInput.model_validate(edit_body(sections=sections))
    # The exact same number of ASCII characters fits the independent byte limit.
    sections = [
        {"kind": section["kind"], "items": [{**item, "text": "a" * 1200}] * 6}
        for section in sections
    ]
    assert NoteEditInput.model_validate(edit_body(sections=sections))


def test_human_edit_key_binds_note_actor_payload_and_original_base():
    note = uuid4()
    body = NoteEditInput.model_validate(edit_body())
    reordered = NoteEditInput.model_validate(dict(reversed(list(body.model_dump().items()))))
    assert request_digest(note, body) == request_digest(note, reordered)
    assert request_digest(note, body) != request_digest(uuid4(), body)
    changed = NoteEditInput.model_validate({**body.model_dump(), "personal_notes": "不同理解"})
    assert request_digest(note, body) != request_digest(note, changed)
    changed = NoteEditInput.model_validate({**body.model_dump(), "base_revision_id": uuid4()})
    assert request_digest(note, body) != request_digest(note, changed)


def test_empty_personal_notes_are_explicitly_disclosed():
    body = NoteEditInput.model_validate(edit_body(personal_notes=" \n "))
    evidence = build_evidence("来源资料。")
    summary = edited_summary(body, {"limitations": []}, evidence)
    document = EditedDocument(**summary.model_dump(), personal_notes=body.personal_notes)
    assert document.personal_notes == ""
    assert "本版本未追加个人理解" in render_edited_markdown(document, evidence)


def test_first_human_note_input_is_explicit_and_does_not_accept_a_base_revision():
    value = edit_body()
    value.pop("base_revision_id")
    value["consent"] = True
    created = NoteCreateInput.model_validate(value)
    assert created.consent is True
    with pytest.raises(ValidationError):
        NoteCreateInput.model_validate({**value, "consent": False})
    with pytest.raises(ValidationError):
        NoteCreateInput.model_validate({**value, "base_revision_id": str(uuid4())})
