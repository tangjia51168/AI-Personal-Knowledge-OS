"""Initial human revisions against real RLS/transactions, never model substitutes."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest
from app.db.session import get_engine
from app.modules.notes import edits
from app.modules.notes.edit_content import NoteCreateInput, NoteEditInput
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_notes_integration import context, identity, knowledge_accounts

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]


@contextmanager
def accounts():
    yield from knowledge_accounts.__wrapped__()


def payload(**changes):
    item = {"text": "先准备三个材料，再验证输出。", "evidence_keys": ["E001"]}
    return {
        "idempotency_key": str(uuid4()),
        "consent": True,
        "title": "人工首篇维护协议",
        "overview": item,
        "sections": [{"kind": "core_claims", "items": [item]}],
        "personal_notes": "人工理解，不是来源原文",
        **changes,
    }


def create(account, body):
    return edits.save_first_note(
        identity(account),
        UUID(account["tenant_id"]),
        UUID(account["snapshot_id"]),
        NoteCreateInput.model_validate(body),
    )


def headers(account):
    return {"Authorization": f"Bearer {account['access_token']}"}


def endpoint(account):
    return f"/api/v1/spaces/{account['tenant_id']}/snapshots/{account['snapshot_id']}"


def test_initial_revision_evidence_api_export_research_and_idempotency():
    with accounts() as (live, admin, (owner, other)):
        url = endpoint(owner)
        assert live.get(url + "/note-evidence").status_code == 401
        assert live.get(url + "/note-evidence", headers=headers(other)).status_code == 404
        preview = live.get(url + "/note-evidence", headers=headers(owner))
        assert preview.status_code == 200
        assert preview.json()["data"]["evidence"] == [
            {"key": "E001", "text": "先准备3个材料，再验证输出。"}
        ]
        body = payload()
        with admin.begin() as conn:
            conn.execute(
                text("UPDATE tenant_members SET role='member' WHERE tenant_id=:t"),
                {"t": owner["tenant_id"]},
            )
        response = live.post(url + "/notes", json=body, headers=headers(owner))
        assert response.status_code == 200
        note = response.json()["data"]
        assert note["version"] == 1 and create(owner, body) == note
        with pytest.raises(HTTPException) as conflict:
            create(owner, {**body, "title": "同键内容已改变"})
        assert conflict.value.detail["code"] == "IDEMPOTENCY_CONFLICT"
        with pytest.raises(HTTPException) as duplicate:
            create(owner, payload())
        assert duplicate.value.detail["code"] == "NOTE_ALREADY_EXISTS"
        root = f"/api/v1/spaces/{owner['tenant_id']}"
        detail = live.get(root + f"/notes/{note['note_id']}", headers=headers(owner)).json()["data"]
        revision = detail["revision"]
        assert revision["origin"] == "user_edit" and revision["base_revision_id"] is None
        assert "仅用于数据库测试，不是真实平台采集证据" in revision["document"]["limitations"]
        export = live.get(
            root + f"/notes/{note['note_id']}/revisions/{note['revision_id']}/export?format=json",
            headers=headers(owner),
        )
        assert export.status_code == 200
        assert export.json()["evidence"] == [
            {"key": "E001", "quote": "先准备3个材料，再验证输出。"}
        ]
        research = live.get(root + "/knowledge/research?q=三个材料", headers=headers(owner)).json()[
            "data"
        ]
        assert research["answer"] is None and research["answer_status"] == "evidence_ready"
        assert research["sources"][0]["revision_id"] == note["revision_id"]
        absent = live.get(root + "/knowledge/research?q=不存在资料", headers=headers(owner)).json()[
            "data"
        ]
        assert absent["answer_status"] == "insufficient_evidence"
        with get_engine().begin() as conn:
            context(conn, owner)
            assert conn.execute(text("SELECT count(*) FROM note_runs")).scalar_one() == 0
            assert (
                conn.execute(
                    text("SELECT count(*) FROM audit_events WHERE action='notes.edited'")
                ).scalar_one()
                == 1
            )
        edit = {k: v for k, v in payload().items() if k != "consent"}
        second = edits.save_edit(
            identity(owner),
            UUID(owner["tenant_id"]),
            UUID(note["note_id"]),
            NoteEditInput(**edit, base_revision_id=note["revision_id"]),
        )
        assert second["version"] == 2


def test_first_note_concurrent_same_key_and_distinct_key():
    with accounts() as (_live, _admin, (owner, other)):
        body = payload()
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: create(owner, body), range(2)))
        assert results[0] == results[1]
        with ThreadPoolExecutor(max_workers=2) as workers:
            pending = [workers.submit(create, other, payload()) for _ in range(2)]
            successes, conflicts = [], []
            for item in pending:
                try:
                    successes.append(item.result())
                except HTTPException as error:
                    conflicts.append(error.detail["code"])
        assert len(successes) == 1 and conflicts == ["NOTE_ALREADY_EXISTS"]


def test_first_note_invalid_evidence_atomic_audit_and_quota_replay(monkeypatch):
    with accounts() as (_live, _admin, (owner, _other)):
        invalid = payload(overview={"text": "无法引用", "evidence_keys": ["E099"]})
        with pytest.raises(HTTPException) as error:
            create(owner, invalid)
        assert error.value.status_code == 422
        original = edits.record

        def fail(*_args):
            raise RuntimeError("protocol rollback")

        monkeypatch.setattr(edits, "record", fail)
        with pytest.raises(RuntimeError, match="protocol rollback"):
            create(owner, payload())
        with get_engine().begin() as conn:
            context(conn, owner)
            assert conn.execute(text("SELECT count(*) FROM notes")).scalar_one() == 0
            assert conn.execute(text("SELECT count(*) FROM note_revisions")).scalar_one() == 0
        monkeypatch.setattr(edits, "record", original)
        body = payload()
        note = create(owner, body)
        monkeypatch.setattr(edits, "check_edit_quota", fail)
        assert create(owner, body) == note


def test_first_note_database_binding_and_immutability():
    with accounts() as (_live, _admin, (owner, other)):
        note = create(owner, payload())
        with pytest.raises(DBAPIError), get_engine().begin() as conn:
            context(conn, owner)
            conn.execute(
                text("UPDATE note_revisions SET title='changed' WHERE id=:id"),
                {"id": note["revision_id"]},
            )
        # An initial human revision must be version 1 and match its note's source.
        for snapshot, version in [(owner["snapshot_id"], 2), (other["snapshot_id"], 1)]:
            with pytest.raises(DBAPIError), get_engine().begin() as conn:
                context(conn, owner)
                conn.execute(
                    text(
                        "INSERT INTO note_revisions(id,tenant_id,note_id,snapshot_id,created_by,"
                        "origin,"
                        "version,title,content_md,document,edit_key,request_sha256) "
                        "VALUES (:id,:tenant,:note,:snapshot,:user,'user_edit',:version,'x','x',"
                        "'{}',:key,:digest)"
                    ),
                    {
                        "id": uuid4(),
                        "tenant": owner["tenant_id"],
                        "note": note["note_id"],
                        "snapshot": snapshot,
                        "user": owner["user_id"],
                        "version": version,
                        "key": uuid4(),
                        "digest": "0" * 64,
                    },
                )


@pytest.mark.parametrize("revoke", ["membership", "session"])
def test_first_note_rechecks_identity_after_actual_lock_wait(revoke):
    with accounts() as (live, admin, (owner, _other)):
        with ThreadPoolExecutor(max_workers=1) as workers, admin.connect() as blocker:
            transaction = blocker.begin()
            try:
                blocker.execute(
                    text("SELECT id FROM tenants WHERE id=:tenant FOR NO KEY UPDATE"),
                    {"tenant": owner["tenant_id"]},
                )
                pid = blocker.execute(text("SELECT pg_backend_pid()")).scalar_one()
                pending = workers.submit(create, owner, payload())
                deadline, blocked = time.monotonic() + 10, False
                with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
                    while time.monotonic() < deadline:
                        blocked = observer.execute(
                            text(
                                "SELECT EXISTS(SELECT 1 FROM pg_stat_activity a "
                                "WHERE :pid=ANY(pg_blocking_pids(a.pid)))"
                            ),
                            {"pid": pid},
                        ).scalar_one()
                        if blocked:
                            break
                        time.sleep(0.05)
                    assert blocked
                    if revoke == "session":
                        assert (
                            live.post("/api/v1/auth/logout", headers=headers(owner)).status_code
                            == 200
                        )
                    else:
                        observer.execute(
                            text(
                                "UPDATE tenant_members SET active=false "
                                "WHERE tenant_id=:t AND user_id=:u"
                            ),
                            {"t": owner["tenant_id"], "u": owner["user_id"]},
                        )
            finally:
                transaction.rollback()
            with pytest.raises(HTTPException) as error:
                pending.result(timeout=10)
            assert error.value.status_code == (401 if revoke == "session" else 404)
