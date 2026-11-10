"""Local input parsing, provenance disclosure and import API input boundaries."""

import hashlib
import json
from uuid import uuid4

import pytest
from app.modules.notes.content import build_evidence, parse_summary, render_markdown
from app.modules.notes.service import snapshot_scope
from app.modules.sources.import_content import (
    import_text_bytes,
    parse_import_content,
)
from app.modules.sources.imports import ContentImportInput
from pydantic import ValidationError


def request_body(**changes):
    return {
        "idempotency_key": str(uuid4()),
        "consent": True,
        "title": "用户授权的笔记资料",
        "text": "用户提供正文；未访问关联网站。",
        "format": "text",
        "content_kind": "article",
        **changes,
    }


def test_plain_import_normalizes_display_but_hashes_exact_original_utf8():
    raw = "\ufeff第一行\r\n第二行\r第三行\n"
    content = parse_import_content(raw, "text", "article")
    assert content.body_text == "第一行\n第二行\n第三行"
    assert content.raw_sha256 == hashlib.sha256(raw.encode()).hexdigest()
    assert content.utf8_bytes == len(raw.encode())
    assert content.cue_count == 0


@pytest.mark.parametrize(
    "raw,format,first,second",
    [
        (
            "1\n00:00:01,250 --> 00:00:03,000\n连接电源。\n\n"
            "2\n00:00:02,500 --> 00:00:05,000\n检查输出。\n",
            "srt",
            "00:00:01.250",
            "00:00:05.000",
        ),
        (
            "WEBVTT\n\nopening\n00:01.250 --> 00:03.000\n连接电源。\n\n"
            "00:00:03.000 --> 00:00:06.000\n检查输出。\n",
            "vtt",
            "00:00:01.250",
            "00:00:06.000",
        ),
    ],
)
def test_subtitle_cues_keep_human_supplied_times_and_exact_spoken_text(raw, format, first, second):
    content = parse_import_content(raw, format, "transcript")
    assert content.cue_count == 2
    assert first in content.body_text and second in content.body_text
    assert "连接电源。" in content.body_text and "检查输出。" in content.body_text
    assert content.raw_sha256 == hashlib.sha256(raw.encode()).hexdigest()
    assert content.utf8_bytes == len(raw.encode())


@pytest.mark.parametrize("value", [" \n\t", "\x00body", "body\x7f", "\ud800", "\ufeff"])
def test_import_rejects_empty_controls_invalid_unicode(value):
    with pytest.raises(ValueError, match="IMPORT_TEXT_INVALID"):
        parse_import_content(value, "text", "article")


def test_import_limits_both_unicode_characters_and_utf8_bytes():
    assert len(import_text_bytes("汉" * 21845 + "a")) == 65536
    with pytest.raises(ValueError, match="IMPORT_TEXT_LIMIT"):
        import_text_bytes("汉" * 21845 + "aa")
    assert len(import_text_bytes("a" * 60000)) == 60000
    with pytest.raises(ValueError, match="IMPORT_TEXT_LIMIT"):
        import_text_bytes("a" * 60001)


@pytest.mark.parametrize(
    "raw,format,code",
    [
        ("1\n00:00:01,000 --> 00:00:01,000\n空时段", "srt", "INVALID"),
        ("1\n00:00:05,000 --> 00:00:01,000\n反转", "srt", "INVALID"),
        ("1\n00:60:01,000 --> 01:01:01,000\n非法分钟", "srt", "INVALID"),
        ("00:00:01,000 --> 00:00:03,000\n缺少编号", "srt", "INVALID"),
        ("1\n00:00:01.000 --> 00:00:03.000\n错误格式", "srt", "INVALID"),
        ("WEBVTT\n00:01.000 --> 00:03.000\n缺少头部分隔", "vtt", "INVALID"),
        ("WEBVTT\n\n00:01.000 --> 00:03.000 align:start\n暂不支持", "vtt", "INVALID"),
        ("WEBVTT\n\nNOTE private note", "vtt", "UNSUPPORTED"),
        ("WEBVTT\n\nSTYLE\n::cue { color: red }", "vtt", "UNSUPPORTED"),
        ("WEBVTT", "vtt", "INVALID"),
        (
            "1\n00:00:03,000 --> 00:00:05,000\n第一段\n\n2\n00:00:01,000 --> 00:00:02,000\n倒序",
            "srt",
            "INVALID",
        ),
    ],
)
def test_subtitle_rejects_malformed_or_unsupported_content_instead_of_silent_loss(
    raw, format, code
):
    with pytest.raises(ValueError, match=f"IMPORT_SUBTITLE_{code}"):
        parse_import_content(raw, format, "transcript")


def test_subtitle_cue_count_is_bounded_before_parsing():
    raw = "\n\n".join("1\n00:00:01,000 --> 00:00:02,000\nx" for _ in range(1001))
    with pytest.raises(ValueError, match="IMPORT_SUBTITLE_LIMIT"):
        parse_import_content(raw, "srt", "transcript")


@pytest.mark.parametrize(
    "changes",
    [
        {"format": "html"},
        {"format": "srt", "content_kind": "article"},
        {"content_kind": "metadata"},
        {"consent": "true"},
        {"provenance": "platform_collection"},
        {"title": "换行\n标题"},
        {"title": " \t"},
        {"text": "a" * 60001},
        {"text": "汉" * 21846},
    ],
)
def test_import_request_rejects_forged_or_unbounded_fields(changes):
    with pytest.raises(ValidationError):
        ContentImportInput.model_validate(request_body(**changes))


def test_import_request_preserves_original_text_and_normalizes_only_title():
    body = ContentImportInput.model_validate(request_body(title="  用户资料  ", text="正文\r\n"))
    assert body.title == "用户资料" and body.text == "正文\r\n"


def test_imported_note_scope_cannot_be_omitted_by_model_and_both_formats_disclose_it():
    evidence = build_evidence("用户提供的字幕，未对照视频核验。")
    raw = json.dumps(
        {
            "title": "资料笔记",
            "overview": {"text": "这是一份用户资料。", "evidence_keys": ["E001"]},
            "sections": [
                {
                    "kind": "takeaways",
                    "items": [
                        {"text": "资料仍待核验。", "evidence_keys": ["E001"]},
                    ],
                },
            ],
            "limitations": [],
        }
    )
    document = snapshot_scope(parse_summary(raw, evidence), "transcript", "user_import")
    assert document.title.startswith("用户导入笔记：")
    assert "未验证其与关联平台或视频一致" in document.limitations[0]
    assert "时间亦未对照音视频核验" in render_markdown(document, evidence)
