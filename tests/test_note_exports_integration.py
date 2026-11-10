"""Real application-role exports from immutable DB revisions; no provider calls."""

import hashlib
import io
import json
import os
import zipfile
from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest
from app.modules.notes.edit_content import NoteEditInput
from app.modules.notes.edits import save_edit
from sqlalchemy import text
from test_organization_integration import (
    auth,
    endpoint,
    identity,
    organization_accounts,
    protocol_note,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]


@pytest.fixture(scope="module")
def export_accounts():
    yield from organization_accounts.__wrapped__()


@contextmanager
def fixture_accounts():
    yield from organization_accounts.__wrapped__()


def path(account, note):
    return endpoint(account) + f"/notes/{note['note_id']}/revisions/{note['revision_id']}/export"


def test_real_export_identity_and_revision_binding(export_accounts):
    live, _admin, (owner, other) = export_accounts
    note, sibling = owner["notes"]
    url = path(owner, note)
    assert live.get(url).status_code == 401
    assert (
        live.get(url, headers={**auth(other), "X-Tenant-ID": owner["tenant_id"]}).status_code == 404
    )
    foreign = path(other, note)
    assert live.get(foreign, headers=auth(other)).status_code == 404
    mismatched = url.replace(note["note_id"], sibling["note_id"])
    assert live.get(mismatched, headers=auth(owner)).status_code == 404
    assert live.get(url, params={"format": "pdf"}, headers=auth(owner)).status_code == 422
    assert live.post(url, headers=auth(owner)).status_code == 405


def test_real_export_formats_preserve_revision_and_do_not_write(export_accounts):
    live, admin, (owner, _other) = export_accounts
    note = owner["notes"][0]
    params = {"tenant": owner["tenant_id"]}
    with admin.connect() as connection:
        before = [
            connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"), params
            ).scalar_one()
            for table in (
                "note_revisions",
                "audit_events",
                "note_runs",
                "ai_usage",
                "processing_jobs",
            )
        ]
    detail = live.get(path(owner, note).removesuffix("/export"), headers=auth(owner)).json()["data"]
    contents = {}
    for format, media in (
        ("markdown", "text/markdown"),
        ("html", "text/html"),
        ("text", "text/plain"),
        ("json", "application/json"),
    ):
        response = live.get(path(owner, note), params={"format": format}, headers=auth(owner))
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(media)
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-disposition"].startswith(
            'attachment; filename="knowledge-'
        )
        assert "sandbox" in response.headers["content-security-policy"]
        contents[format] = response
    assert contents["markdown"].text == detail["content_md"]
    data = contents["json"].json()
    assert data["schema"] == "knowledge-note-export-v1"
    assert data["revision"]["document"] == detail["document"]
    assert (
        data["revision"]["content_md_sha256"]
        == hashlib.sha256(contents["markdown"].content).hexdigest()
    )
    assert data["source"]["original_url"].startswith("https://example.com/organization-protocol/")
    assert data["snapshot"]["provenance"] == "user_import"
    assert data["evidence"] == [{"key": "E001", "quote": note["source_text"]}]
    assert not {"run_id", "job_id", "edit_key", "request_sha256"} & data["revision"].keys()
    for format in ("text", "html"):
        for value in (
            "user_import",
            "USER_PROVIDED_CONTENT",
            "E001",
            note["source_text"],
            str(note["snapshot_id"]),
        ):
            assert value in contents[format].text
    with admin.connect() as connection:
        after = [
            connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"), params
            ).scalar_one()
            for table in (
                "note_revisions",
                "audit_events",
                "note_runs",
                "ai_usage",
                "processing_jobs",
            )
        ]
    assert before == after


def test_real_export_pins_history_and_preserves_personal_understanding(export_accounts):
    live, _admin, (owner, _other) = export_accounts
    note = protocol_note(owner, "导出历史协议")
    url = path(owner, note)
    original = live.get(url, params={"format": "json"}, headers=auth(owner)).content
    edited = save_edit(
        identity(owner),
        UUID(owner["tenant_id"]),
        UUID(note["note_id"]),
        NoteEditInput(
            idempotency_key=uuid4(),
            base_revision_id=UUID(note["revision_id"]),
            title="人工导出修订",
            overview={"text": "重新整理", "evidence_keys": ["E001"]},
            sections=[
                {"kind": "steps", "items": [{"text": "准备材料", "evidence_keys": ["E001"]}]}
            ],
            personal_notes="个人理解仅用于导出测试",
        ),
    )
    assert live.get(url, params={"format": "json"}, headers=auth(owner)).content == original
    for format in ("markdown", "text", "html", "json"):
        response = live.get(path(owner, edited), params={"format": format}, headers=auth(owner))
        assert response.status_code == 200
        assert "个人理解仅用于导出测试" in response.text
        assert "个人理解未作来源或事实核验" in response.text


def test_real_export_member_revocation_and_logout_are_effective(export_accounts):
    live, _admin, (owner, other) = export_accounts
    note = owner["notes"][0]
    member = endpoint(owner) + "/members"
    assert (
        live.post(
            member, headers=auth(owner), json={"user_id": other["user_id"], "role": "member"}
        ).status_code
        == 201
    )
    assert live.get(path(owner, note), headers=auth(other)).status_code == 200
    assert live.delete(member + "/" + other["user_id"], headers=auth(owner)).status_code == 200
    assert live.get(path(owner, note), headers=auth(other)).status_code == 404
    assert live.post("/api/v1/auth/logout", headers=auth(other)).status_code == 200
    assert live.get(path(other, other["notes"][0]), headers=auth(other)).status_code == 401


def test_real_obsidian_identity_isolation_and_revocation():
    # Independent accounts: the export suite above deliberately revokes its second actor.
    with fixture_accounts() as (live, _admin, accounts):
        owner, other = accounts
        note, sibling = owner["notes"]
        url = path(owner, note) + "?format=obsidian"
        assert live.get(url).status_code == 401
        assert (
            live.get(url, headers={**auth(other), "X-Tenant-ID": owner["tenant_id"]}).status_code
            == 404
        )
        assert (
            live.get(
                path(other, note), params={"format": "obsidian"}, headers=auth(other)
            ).status_code
            == 404
        )
        assert (
            live.get(
                url.replace(note["note_id"], sibling["note_id"]), headers=auth(owner)
            ).status_code
            == 404
        )
        members = endpoint(owner) + "/members"
        assert (
            live.post(
                members, headers=auth(owner), json={"user_id": other["user_id"], "role": "member"}
            ).status_code
            == 201
        )
        assert live.get(url, headers=auth(other)).status_code == 200
        assert live.delete(members + "/" + other["user_id"], headers=auth(owner)).status_code == 200
        assert live.get(url, headers=auth(other)).status_code == 404
        assert live.post("/api/v1/auth/logout", headers=auth(other)).status_code == 200
        assert live.get(url, headers=auth(other)).status_code == 401


def test_real_obsidian_is_deterministic_readonly_and_revision_pinned():
    with fixture_accounts() as (live, admin, accounts):
        owner = accounts[0]
        note = owner["notes"][0]
        url = path(owner, note)
        tables = ("note_revisions", "audit_events", "note_runs", "ai_usage", "processing_jobs")

        def counts():
            with admin.connect() as connection:
                return [
                    connection.execute(
                        text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                        {"tenant": owner["tenant_id"]},
                    ).scalar_one()
                    for table in tables
                ]

        before = counts()
        response = live.get(url, params={"format": "obsidian"}, headers=auth(owner))
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-disposition"].endswith('.zip"')
        assert "sandbox" in response.headers["content-security-policy"]
        assert counts() == before
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        prefix = f"knowledge-{note['note_id']}/revision-{note['revision_id']}/"
        detail = live.get(url.removesuffix("/export"), headers=auth(owner)).json()["data"]
        assert archive.read(prefix + "original.md") == detail["content_md"].encode()
        assert note["source_text"] in archive.read(prefix + "evidence.md").decode()
        source = json.loads(archive.read(prefix + "source.json"))
        assert source["snapshot"]["provenance"] == "user_import"
        assert source["source"]["original_url"].startswith(
            "https://example.com/organization-protocol/"
        )
        assert source["revision_id"] == note["revision_id"]
        edited = save_edit(
            identity(owner),
            UUID(owner["tenant_id"]),
            UUID(note["note_id"]),
            NoteEditInput(
                idempotency_key=uuid4(),
                base_revision_id=UUID(note["revision_id"]),
                title="Obsidian人工协议修订",
                overview={"text": "人工概述", "evidence_keys": ["E001"]},
                sections=[
                    {"kind": "steps", "items": [{"text": "人工步骤", "evidence_keys": ["E001"]}]}
                ],
                personal_notes="Obsidian个人理解，尚未核验",
            ),
        )
        assert (
            live.get(url, params={"format": "obsidian"}, headers=auth(owner)).content
            == response.content
        )
        latest = live.get(path(owner, edited), params={"format": "obsidian"}, headers=auth(owner))
        assert latest.status_code == 200
        with zipfile.ZipFile(io.BytesIO(latest.content)) as current:
            content = current.read(
                next(name for name in current.namelist() if name.endswith("/note.md"))
            ).decode()
            assert "Obsidian个人理解" in content and "未经来源或事实核验" in content


def test_real_visual_formats_and_batch_are_pinned_readonly_and_tenant_bound():
    from xml.etree import ElementTree as ET

    with fixture_accounts() as (live, admin, (owner, other)):
        note, sibling = owner["notes"]
        batch_url = endpoint(owner) + "/notes/export-batch"
        body = {
            "items": [
                {"note_id": item["note_id"], "revision_id": item["revision_id"]}
                for item in (note, sibling)
            ]
        }
        assert live.post(batch_url, json=body).status_code == 401
        assert live.post(batch_url, json=body, headers=auth(other)).status_code == 404
        assert live.get(batch_url, headers=auth(owner)).status_code in (405, 422)
        for items in ([], body["items"] * 11, [body["items"][0]] * 2):
            assert (
                live.post(batch_url, json={"items": items}, headers=auth(owner)).status_code == 422
            )
        foreign = {
            "note_id": other["notes"][0]["note_id"],
            "revision_id": other["notes"][0]["revision_id"],
        }
        assert (
            live.post(
                batch_url, json={"items": [body["items"][0], foreign]}, headers=auth(owner)
            ).status_code
            == 404
        )
        mismatch = {"note_id": note["note_id"], "revision_id": sibling["revision_id"]}
        assert (
            live.post(batch_url, json={"items": [mismatch]}, headers=auth(owner)).status_code == 404
        )

        def counts():
            with admin.connect() as connection:
                return [
                    connection.execute(
                        text(f"SELECT count(*) FROM {table} WHERE tenant_id=:t"),
                        {"t": owner["tenant_id"]},
                    ).scalar_one()
                    for table in ("note_revisions", "audit_events", "ai_usage", "processing_jobs")
                ]

        before = counts()
        for format in ("pptx", "image", "flowchart"):
            response = live.get(path(owner, note), params={"format": format}, headers=auth(owner))
            assert response.status_code == 200 and len(response.content) <= 1_048_576
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert "sandbox" in response.headers["content-security-policy"]
            assert (
                live.get(
                    path(owner, note), params={"format": format}, headers=auth(other)
                ).status_code
                == 404
            )
            if format == "pptx":
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    texts = "".join(
                        "".join(ET.fromstring(archive.read(name)).itertext())
                        for name in archive.namelist()
                        if name.startswith("ppt/slides/") and name.endswith(".xml")
                    )
                    assert "E001" in texts and "不是完整备份" in texts
            else:
                assert ET.fromstring(response.content).tag.endswith("svg")
        response = live.post(batch_url, json=body, headers=auth(owner))
        assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
        assert response.headers["cache-control"] == "no-store"
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            manifest = json.loads(archive.read("batch-manifest.json"))
            assert len(manifest["items"]) == 2
            for entry in manifest["files"]:
                assert hashlib.sha256(archive.read(entry["path"])).hexdigest() == entry["sha256"]
        assert counts() == before
        assert (
            live.post(
                batch_url, json={"items": list(reversed(body["items"]))}, headers=auth(owner)
            ).content
            == response.content
        )
        members = endpoint(owner) + "/members"
        assert (
            live.post(
                members, json={"user_id": other["user_id"], "role": "member"}, headers=auth(owner)
            ).status_code
            == 201
        )
        assert live.post(batch_url, json=body, headers=auth(other)).status_code == 200
        assert live.delete(members + "/" + other["user_id"], headers=auth(owner)).status_code == 200
        assert live.post(batch_url, json=body, headers=auth(other)).status_code == 404
        assert live.post("/api/v1/auth/logout", headers=auth(other)).status_code == 200
        assert live.post(batch_url, json=body, headers=auth(other)).status_code == 401
