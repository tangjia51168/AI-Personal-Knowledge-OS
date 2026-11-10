"""Real PostgreSQL/API annotation isolation, transactions and races; no inference."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from app.db.session import get_engine
from app.modules.notes import annotations
from app.modules.notes.annotation_content import AnnotationInput
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_organization_integration import (
    auth,
    context,
    endpoint,
    identity,
    organization_accounts,
    protocol_note,
    protocol_revision,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]


@pytest.fixture(scope="module")
def annotation_accounts():
    yield from organization_accounts.__wrapped__()


def payload(**changes):
    return {
        "idempotency_key": str(uuid4()),
        "kind": "overview",
        "item_index": 0,
        "expected_quote": "准备材料后验证输出。",
        "body": "批注协议内容，未调用模型。",
        **changes,
    }


def create(account, note, body=None):
    return annotations.create_annotation(
        identity(account),
        UUID(account["tenant_id"]),
        UUID(note["note_id"]),
        UUID(note["revision_id"]),
        AnnotationInput.model_validate(body or payload()),
    )


def path(account, note):
    return (
        endpoint(account) + f"/notes/{note['note_id']}/revisions/{note['revision_id']}/annotations"
    )


def test_real_annotation_api_checks_identity_revision_and_quote(annotation_accounts):
    live, _admin, (owner, other) = annotation_accounts
    note = owner["notes"][0]
    url = path(owner, note)
    assert live.get(url).status_code == 401
    assert live.get(url, headers=auth(other)).status_code == 404
    for changes, status in (
        ({"expected_quote": "不是原段"}, 409),
        ({"kind": "overview", "item_index": 1}, 409),
        ({"kind": "examples"}, 409),
        ({"body": " "}, 422),
    ):
        assert live.post(url, headers=auth(owner), json=payload(**changes)).status_code == status
    response = live.post(url, headers=auth(owner), json=payload())
    assert response.status_code == 200
    assert response.json()["data"]["quote"] == "准备材料后验证输出。"
    assert response.json()["data"]["created_by"] == owner["user_id"]
    wrong = {**note, "revision_id": owner["notes"][1]["revision_id"]}
    assert live.get(path(owner, wrong), headers=auth(owner)).status_code == 404


def test_real_annotation_concurrent_idempotency_and_audit(annotation_accounts):
    _live, admin, (owner, _other) = annotation_accounts
    note, body = owner["notes"][0], payload(body="并发同键协议批注")
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _: create(owner, note, body), range(4)))
    assert len({row["id"] for row in results}) == 1
    for changed in (
        {**body, "body": "新内容"},
        {**body, "kind": "steps", "expected_quote": "先准备3个材料。"},
    ):
        with pytest.raises(HTTPException) as conflict:
            create(owner, note, changed)
        assert conflict.value.status_code == 409
    with admin.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM audit_events "
                    "WHERE resource_id=:id AND action='notes.annotated'"
                ),
                {"id": results[0]["id"]},
            ).scalar_one()
            == 1
        )


def test_real_annotations_stay_on_original_revision(annotation_accounts):
    live, _admin, (owner, _other) = annotation_accounts
    note = protocol_note(owner, "批注版本协议")
    original = create(owner, note, payload(kind="steps", expected_quote="先准备3个材料。"))
    newer = protocol_revision(owner, note["snapshot_id"], note["source_text"], "新版本协议")
    assert newer["revision_id"] != note["revision_id"]
    assert live.get(path(owner, newer), headers=auth(owner)).json()["data"] == []
    old = live.get(path(owner, note), headers=auth(owner)).json()["data"]
    assert [row["id"] for row in old] == [original["id"]]
    assert original["quote"] == "先准备3个材料。"


def test_real_annotation_rls_append_permissions_quote_and_composite_fk(annotation_accounts):
    _live, admin, (owner, other) = annotation_accounts
    note = owner["notes"][0]
    create(owner, note)
    with get_engine().begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM note_annotations")).scalar_one() == 0
        context(connection, other)
        assert (
            connection.execute(
                text("SELECT count(*) FROM note_annotations WHERE tenant_id=:tenant"),
                {"tenant": owner["tenant_id"]},
            ).scalar_one()
            == 0
        )
    parameters = {
        "id": uuid4(),
        "tenant": owner["tenant_id"],
        "note": note["note_id"],
        "snapshot": note["snapshot_id"],
        "revision": note["revision_id"],
        "user": owner["user_id"],
        "key": uuid4(),
    }
    insert = (
        "INSERT INTO note_annotations(id,tenant_id,note_id,snapshot_id,revision_id,created_by,"
        "idempotency_key,request_sha256,kind,item_index,quote,body) "
        "VALUES(:id,:tenant,:note,:snapshot,:revision,:user,:key,repeat('a',64),"
        "'overview',0,'准备材料后验证输出。','权限协议')"
    )
    for statement in (
        "UPDATE note_annotations SET body='tamper' WHERE tenant_id=:tenant",
        "DELETE FROM note_annotations WHERE tenant_id=:tenant",
        insert.replace("准备材料后验证输出。", "伪造引文"),
    ):
        with pytest.raises(DBAPIError) as denied, get_engine().begin() as connection:
            context(connection, owner)
            connection.execute(text(statement), parameters)
        assert denied.value.orig.sqlstate == "42501"
    with pytest.raises(DBAPIError) as denied, get_engine().begin() as connection:
        context(connection, owner)
        connection.execute(text(insert), {**parameters, "user": other["user_id"]})
    assert denied.value.orig.sqlstate == "42501"
    for changes in (
        {"revision": other["notes"][0]["revision_id"]},
        {"note": owner["notes"][1]["note_id"]},
        {"snapshot": uuid4()},
        {"user": uuid4()},
    ):
        with pytest.raises(DBAPIError) as denied, admin.begin() as connection:
            connection.execute(text(insert), {**parameters, **changes})
        assert denied.value.orig.sqlstate == "23503"
    with admin.connect() as connection:
        assert connection.execute(
            text(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                "WHERE oid='note_annotations'::regclass"
            )
        ).scalar_one()


def test_real_annotation_audit_rollback_and_quota_replay(annotation_accounts, monkeypatch):
    _live, admin, (owner, _other) = annotation_accounts
    note = owner["notes"][1]
    body = payload(body="审计原子协议")

    def failed(*_args, **_kwargs):
        raise RuntimeError("controlled annotation audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(annotations, "record", failed)
        with pytest.raises(RuntimeError, match="controlled annotation"):
            create(owner, note, body)
    with admin.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM note_annotations "
                    "WHERE tenant_id=:tenant AND idempotency_key=:key"
                ),
                {"tenant": owner["tenant_id"], "key": body["idempotency_key"]},
            ).scalar_one()
            == 0
        )
    saved = create(owner, note, body)
    for name in ("MAX_NOTE_ANNOTATIONS", "MAX_SPACE_ANNOTATIONS"):
        with monkeypatch.context() as patch:
            patch.setattr(annotations, name, 0)
            assert create(owner, note, body)["id"] == saved["id"]
            with pytest.raises(HTTPException) as quota:
                create(owner, note)
            assert quota.value.status_code == 429


def test_real_annotation_cursor_and_membership_revocation(annotation_accounts):
    live, admin, (owner, other) = annotation_accounts
    note = protocol_note(owner, "批注分页协议")
    ids = {create(owner, note)["id"] for _ in range(3)}
    result = []
    cursor = None
    for _ in range(3):
        response = live.get(
            path(owner, note),
            headers=auth(owner),
            params={"limit": 1, **({"cursor": cursor} if cursor else {})},
        )
        assert response.status_code == 200
        page = response.json()
        result.extend(item["id"] for item in page["data"])
        cursor = page["next_cursor"]
    assert cursor is None and set(result) == ids and len(result) == 3
    foreign = create(other, other["notes"][0])
    assert (
        live.get(
            path(owner, note), headers=auth(owner), params={"cursor": foreign["id"]}
        ).status_code
        == 404
    )
    for params in ({"limit": 0}, {"limit": 51}, {"cursor": "invalid"}):
        assert live.get(path(owner, note), headers=auth(owner), params=params).status_code == 422
    try:
        with admin.begin() as connection:
            connection.execute(
                text(
                    "UPDATE tenant_members SET active=false "
                    "WHERE tenant_id=:tenant AND user_id=:user"
                ),
                {"tenant": owner["tenant_id"], "user": owner["user_id"]},
            )
        assert live.get(path(owner, note), headers=auth(owner)).status_code == 404
        with pytest.raises(HTTPException) as denied:
            create(owner, note)
        assert denied.value.status_code == 404
    finally:
        with admin.begin() as connection:
            connection.execute(
                text(
                    "UPDATE tenant_members SET active=true "
                    "WHERE tenant_id=:tenant AND user_id=:user"
                ),
                {"tenant": owner["tenant_id"], "user": owner["user_id"]},
            )


def test_real_annotation_logout_while_waiting_for_tenant_lock(annotation_accounts):
    live, admin, (owner, _other) = annotation_accounts
    email = live.get("/api/v1/auth/me", headers=auth(owner)).json()["data"]["email"]
    response = live.post(
        "/api/v1/auth/login", json={"email": email, "password": "organization-fixture-only-2026"}
    )
    assert response.status_code == 200
    suspended = {**owner, "access_token": response.json()["data"]["access_token"]}
    body = payload(body="锁等待注销协议")
    with ThreadPoolExecutor(max_workers=1) as workers, admin.connect() as blocker:
        transaction = blocker.begin()
        try:
            blocker.execute(
                text("SELECT id FROM tenants WHERE id=:tenant FOR NO KEY UPDATE"),
                {"tenant": owner["tenant_id"]},
            )
            pid = blocker.execute(text("SELECT pg_backend_pid()")).scalar_one()
            pending = workers.submit(create, suspended, owner["notes"][0], body)
            deadline = time.monotonic() + 10
            blocked = False
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
            assert blocked, "Writer must actually wait for the PostgreSQL tenant lock"
            assert live.post("/api/v1/auth/logout", headers=auth(suspended)).status_code == 200
        finally:
            transaction.rollback()
        with pytest.raises(HTTPException) as denied:
            pending.result(timeout=10)
        assert denied.value.status_code == 401
    with admin.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM note_annotations "
                    "WHERE tenant_id=:tenant AND idempotency_key=:key"
                ),
                {"tenant": owner["tenant_id"], "key": body["idempotency_key"]},
            ).scalar_one()
            == 0
        )
