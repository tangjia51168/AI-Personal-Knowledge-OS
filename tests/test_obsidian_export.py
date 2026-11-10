"""ZIP format, deterministic manifest and hostile content boundaries, without a vault."""

import hashlib
import io
import json
import stat
import zipfile
from copy import deepcopy
from uuid import UUID

import pytest
from app.modules.notes.exports import render_export
from app.modules.notes.obsidian import MAX_BUNDLE_BYTES, render_obsidian_batch
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


@pytest.fixture
def bundle_document(export_document):
    data = deepcopy(export_document)
    data["note_id"] = "00000000-0000-0000-0000-000000000001"
    data["revision"]["id"] = "00000000-0000-0000-0000-000000000002"
    return data


def unzip(data):
    raw, media, suffix = render_export(data, "obsidian")
    assert (media, suffix) == ("application/zip", "zip")
    return raw, zipfile.ZipFile(io.BytesIO(raw))


def test_bundle_paths_are_uuid_scoped_regular_files_with_deterministic_bytes(bundle_document):
    original = deepcopy(bundle_document)
    raw, archive = unzip(bundle_document)
    prefix = f"knowledge-{original['note_id']}/revision-{original['revision']['id']}/"
    assert len(raw) <= MAX_BUNDLE_BYTES
    assert set(archive.namelist()) == {
        prefix + name
        for name in (
            "README.md",
            "note.md",
            "evidence.md",
            "source.json",
            "original.md",
            "manifest.json",
        )
    }
    for entry in archive.infolist():
        assert ".." not in entry.filename and "\\" not in entry.filename
        assert not entry.filename.startswith(("/", "\\"))
        assert entry.compress_type == zipfile.ZIP_STORED
        assert stat.S_ISREG(entry.external_attr >> 16)
        assert entry.date_time == (1980, 1, 1, 0, 0, 0)
    assert archive.read(prefix + "original.md") == original["revision"]["content_md"].encode()
    assert raw == render_export(bundle_document, "obsidian")[0]
    assert bundle_document == original


def test_bundle_manifest_authenticates_each_payload_and_exact_source_metadata(bundle_document):
    _raw, archive = unzip(bundle_document)
    files = {entry.rsplit("/", 1)[1]: archive.read(entry) for entry in archive.namelist()}
    manifest = json.loads(files["manifest.json"])
    assert manifest["schema"] == "knowledge-obsidian-bundle-v1"
    assert len(manifest["files"]) == 5
    assert {entry["path"] for entry in manifest["files"]} == set(files) - {"manifest.json"}
    for entry in manifest["files"]:
        assert entry["bytes"] == len(files[entry["path"]])
        assert entry["sha256"] == hashlib.sha256(files[entry["path"]]).hexdigest()
    source = json.loads(files["source.json"])
    assert source["source"] == bundle_document["source"]
    assert source["snapshot"] == bundle_document["snapshot"]
    assert source["created_by"] == bundle_document["revision"]["created_by"]
    assert source["revision_id"] == bundle_document["revision"]["id"]
    assert not {"access_token", "run_id", "job_id", "edit_key"} & source.keys()
    assert "不是完整备份" in files["README.md"].decode()


def test_bundle_links_only_known_evidence_and_retains_scope_and_personal_notes(bundle_document):
    _raw, archive = unzip(bundle_document)
    note = archive.read(
        next(name for name in archive.namelist() if name.endswith("/note.md"))
    ).decode()
    evidence = archive.read(
        next(name for name in archive.namelist() if name.endswith("/evidence.md"))
    ).decode()
    folder = f"knowledge-{bundle_document['note_id']}/revision-{bundle_document['revision']['id']}/"
    assert f"[[{folder}evidence#E001|E001]]" in note
    assert f"[[{folder}note#概述|返回笔记概述]]" in evidence
    assert "## E001" in evidence
    for value in (
        "未生成类别",
        "仅涵盖快照前缀",
        "个人理解",
        "我的理解",
        "不是事实核验",
        "仅指定笔记",
    ):
        assert value in note


def test_bundle_never_promotes_hostile_text_to_links_embeds_html_or_paths(bundle_document):
    hostile = "<script>alert(1)</script> ![[secret]] [[evil|x]] ![image](https://example.com)"
    bundle_document["revision"]["title"] = "../../outside/.obsidian/plugins/" + hostile
    bundle_document["revision"]["document"]["personal_notes"] = hostile
    bundle_document["evidence"][0]["quote"] = hostile
    _raw, archive = unzip(bundle_document)
    for name in archive.namelist():
        assert "outside" not in name and ".obsidian" not in name
    for suffix in ("/note.md", "/evidence.md"):
        content = archive.read(
            next(name for name in archive.namelist() if name.endswith(suffix))
        ).decode()
        assert "<script>" not in content and "![[secret]]" not in content
        assert "[[evil|x]]" not in content and "![image](" not in content
        assert "&lt;script&gt;" in content


@pytest.mark.parametrize("key", ["../secret", "E001|bad", "E１２３", "E999"])
def test_bundle_rejects_invalid_or_missing_evidence_keys(bundle_document, key):
    bundle_document["revision"]["document"]["overview"]["evidence_keys"] = [key]
    with pytest.raises(HTTPException) as error:
        render_export(bundle_document, "obsidian")
    assert error.value.status_code == 409


def test_bundle_limit_covers_uncompressed_total_and_no_truncation(bundle_document):
    bundle_document["revision"]["content_md"] = "中" * MAX_BUNDLE_BYTES
    with pytest.raises(HTTPException) as error:
        render_export(bundle_document, "obsidian")
    assert error.value.status_code == 413


def test_bundle_ids_never_allow_paths(bundle_document):
    bundle_document["note_id"] = "../../outside"
    with pytest.raises(ValueError):
        render_export(bundle_document, "obsidian")
    with pytest.raises(ValueError):
        UUID(bundle_document["note_id"])


def test_batch_manifest_hashes_all_files_and_scoped_links_resolve(bundle_document):
    import re

    other = deepcopy(bundle_document)
    other["note_id"] = "00000000-0000-0000-0000-000000000003"
    other["revision"]["id"] = "00000000-0000-0000-0000-000000000004"
    raw = render_obsidian_batch([bundle_document, other])
    assert raw == render_obsidian_batch([other, bundle_document])
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("batch-manifest.json"))
        assert len(manifest["items"]) == 2
        assert {row["path"] for row in manifest["files"]} == names - {"batch-manifest.json"}
        for row in manifest["files"]:
            content = archive.read(row["path"])
            assert row["bytes"] == len(content)
            assert row["sha256"] == hashlib.sha256(content).hexdigest()
        for name in names:
            if not name.endswith(("/note.md", "/evidence.md")):
                continue
            text = archive.read(name).decode()
            for target, heading in re.findall(r"\[\[([^#\]|]+)#([^|\]]+)\|[^\]]+\]\]", text):
                assert target + ".md" in names
                assert "## " + heading in archive.read(target + ".md").decode()
                assert name.split("/")[:2] == target.split("/")[:2]


def test_batch_rejects_empty_duplicate_and_oversized_selections(bundle_document):
    for documents in ([], [bundle_document, bundle_document], [bundle_document] * 21):
        with pytest.raises(HTTPException):
            render_obsidian_batch(documents)


def test_batch_limit_includes_outer_manifest_without_silent_truncation(
    bundle_document, monkeypatch
):
    from app.modules.notes import obsidian

    original = render_obsidian_batch([bundle_document])
    monkeypatch.setattr(obsidian, "MAX_BATCH_BYTES", len(original) - 1)
    with pytest.raises(HTTPException) as error:
        render_obsidian_batch([bundle_document])
    assert error.value.status_code == 413


def test_batch_preserves_multiple_historical_revisions_of_same_note(bundle_document):
    other = deepcopy(bundle_document)
    other["revision"]["id"] = "00000000-0000-0000-0000-000000000005"
    other["revision"]["content_md"] = "历史原文\n"
    with zipfile.ZipFile(io.BytesIO(render_obsidian_batch([bundle_document, other]))) as archive:
        originals = [
            archive.read(name) for name in archive.namelist() if name.endswith("/original.md")
        ]
        assert len(originals) == 2 and "历史原文\n".encode() in originals
