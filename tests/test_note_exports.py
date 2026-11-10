"""Export encodings and inert HTML, without network or model substitutions."""

import json
import zipfile
from copy import deepcopy
from io import BytesIO
from xml.etree import ElementTree as ET

import pytest
from app.modules.notes.exports import DISCLOSURE, MAX_EXPORT_BYTES, render_export
from fastapi import HTTPException


@pytest.fixture
def export_document():
    return {
        "note_id": "note-id",
        "revision": {
            "id": "revision-id",
            "version": 2,
            "title": "中文导出 & 标题",
            "origin": "user_edit",
            "created_by": "author-id",
            "created_at": "2026-11-07",
            "base_revision_id": "base-id",
            "content_md": "# 原文\n\\*保留转义\\*\n",
            "document": {
                "overview": {"text": "概述", "evidence_keys": ["E001"]},
                "sections": [
                    {"kind": "steps", "items": [{"text": "操作", "evidence_keys": ["E001"]}]}
                ],
                "coverage": {"partial": True, "provided_segments": 1, "total_segments": 2},
                "limitations": ["仅快照前缀", "人工导入未经核验"],
                "edit_disclaimer": "个人理解未作来源或事实核验。",
                "personal_notes": "我的理解",
            },
        },
        "source": {
            "original_url": "https://example.com/?a=1&b=2",
            "canonical_url": "https://example.com/",
        },
        "snapshot": {
            "id": "snapshot-id",
            "version": 1,
            "provenance": "user_import",
            "platform": "user_import",
            "content_kind": "article",
            "title": "原文",
            "author": "作者",
            "created_at": "2026-11-06",
            "raw_sha256": "a" * 64,
            "limitations": ["USER_PROVIDED_CONTENT"],
        },
        "evidence": [
            {"key": "E001", "quote": '<script>alert("证据")</script> & [link](https://example.com)'}
        ],
    }


def test_markdown_is_byte_exact_and_export_does_not_mutate(export_document):
    original = deepcopy(export_document)
    content, media, suffix = render_export(export_document, "markdown")
    assert content == original["revision"]["content_md"].encode("utf-8")
    assert (media, suffix) == ("text/markdown", "md")
    assert export_document == original


def test_json_roundtrip_and_deterministic_bytes(export_document):
    content, media, suffix = render_export(export_document, "json")
    assert json.loads(content) == export_document
    assert "中文导出" in content.decode("utf-8")
    assert (media, suffix) == ("application/json", "json")
    assert content == render_export(export_document, "json")[0]


@pytest.mark.parametrize("format", ["text", "html"])
def test_human_exports_preserve_scope_evidence_provenance_and_missing_categories(
    export_document, format
):
    content = render_export(export_document, format)[0].decode("utf-8")
    for value in (
        DISCLOSURE,
        "user_import",
        "USER_PROVIDED_CONTENT",
        "E001",
        "author-id",
        "base-id",
        "仅快照前缀",
        "provided_segments",
        "个人理解未作来源或事实核验",
        "我的理解",
        "未生成类别",
    ):
        assert value in content


def test_html_escapes_all_untrusted_fields_and_has_no_active_resources(export_document):
    export_document["revision"]["title"] = "</title><img src=x onerror=alert(1)>"
    content, media, suffix = render_export(export_document, "html")
    html = content.decode("utf-8")
    assert "<script>" not in html and "<img" not in html and "<a " not in html
    assert "&lt;script&gt;" in html and "&lt;/title&gt;&lt;img" in html
    assert (
        "default-src 'none'" in html and "base-uri 'none'" in html and "form-action 'none'" in html
    )
    assert (media, suffix) == ("text/html", "html")


@pytest.mark.parametrize("format", ["markdown", "json", "text", "html"])
def test_export_byte_limit_is_enforced_without_truncating(export_document, format):
    export_document["revision"]["content_md"] = "文" * MAX_EXPORT_BYTES
    export_document["revision"]["document"]["personal_notes"] = "文" * MAX_EXPORT_BYTES
    with pytest.raises(HTTPException) as error:
        render_export(export_document, format)
    assert error.value.status_code == 413
    assert error.value.detail == {"code": "NOTE_EXPORT_LIMIT"}


@pytest.mark.parametrize("format", ["image", "flowchart", "pptx"])
def test_visual_exports_are_bounded_inert_artifacts(export_document, format):
    content, media, suffix = render_export(export_document, format)
    assert content and len(content) <= MAX_EXPORT_BYTES
    if format == "pptx":
        assert media.startswith("application/vnd.openxmlformats") and suffix == "pptx"
        with zipfile.ZipFile(BytesIO(content)) as archive:
            assert "ppt/presentation.xml" in archive.namelist()
            for name in archive.namelist():
                if name.endswith((".xml", ".rels")):
                    ET.fromstring(archive.read(name))
    else:
        assert media == "image/svg+xml" and suffix == "svg"
        root = ET.fromstring(content)
        assert root.tag == "{http://www.w3.org/2000/svg}svg"
        assert not any(
            node.tag.split("}")[-1] in ("script", "image", "foreignObject", "a")
            for node in root.iter()
        )
        assert not any(
            key.split("}")[-1] in ("href", "onload", "onclick")
            for node in root.iter()
            for key in node.attrib
        )


def test_visual_formats_preserve_long_text_and_do_not_create_causal_edges(export_document):
    export_document["revision"]["document"]["overview"]["text"] = "完整长文" * 300 + "末尾保留"
    root = ET.fromstring(render_export(export_document, "image")[0])
    assert "末尾保留" in "".join(root.itertext())
    with zipfile.ZipFile(BytesIO(render_export(export_document, "pptx")[0])) as archive:
        slides = [
            ET.fromstring(archive.read(name))
            for name in archive.namelist()
            if name.startswith("ppt/slides/") and name.endswith(".xml")
        ]
        assert "末尾保留" in "".join("".join(root.itertext()) for root in slides)
    export_document["revision"]["document"]["sections"][0]["kind"] = "core_claims"
    flow = ET.fromstring(render_export(export_document, "flowchart")[0])
    assert "没有明确步骤" in "".join(flow.itertext())
    assert not flow.findall(".//{http://www.w3.org/2000/svg}path")


@pytest.mark.parametrize("format", ["image", "flowchart", "pptx"])
def test_visual_exports_reject_xml_control_characters(export_document, format):
    export_document["revision"]["document"]["sections"][0]["items"][0]["text"] = "坏\x00文本"
    with pytest.raises(HTTPException) as error:
        render_export(export_document, format)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "NOTE_EXPORT_TEXT_INVALID"


def test_pptx_internal_relationships_resolve_and_never_load_external_targets(export_document):
    import posixpath

    with zipfile.ZipFile(BytesIO(render_export(export_document, "pptx")[0])) as archive:
        names = set(archive.namelist())
        for name in names:
            if not name.endswith(".rels"):
                continue
            base = posixpath.dirname(posixpath.dirname(name))
            for relation in ET.fromstring(archive.read(name)):
                assert "TargetMode" not in relation.attrib
                target = posixpath.normpath(posixpath.join(base, relation.attrib["Target"]))
                assert target in names
