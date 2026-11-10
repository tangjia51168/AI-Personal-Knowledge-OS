"""Real revision evidence, RLS, concurrency, quotas and atomic human definitions."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from app.db.session import get_engine
from app.modules.organization import concepts
from app.modules.organization.concept_content import ConceptInput
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
def concept_accounts():
    yield from organization_accounts.__wrapped__()


def payload(**changes):
    return {
        "idempotency_key": str(uuid4()),
        "name": "证据概念",
        "definition": "人工定义，不是事实核验",
        "evidence_key": "E001",
        "expected_quote": "先准备3个材料，再验证输出。",
        **changes,
    }


def create(account, note, body=None):
    return concepts.create_concept(
        identity(account),
        UUID(account["tenant_id"]),
        UUID(note["note_id"]),
        UUID(note["revision_id"]),
        ConceptInput.model_validate(body or payload()),
    )


def path(account, note):
    return endpoint(account) + f"/notes/{note['note_id']}/revisions/{note['revision_id']}/concepts"


def test_concept_http_identity_exact_revision_evidence_and_no_queries(concept_accounts):
    live, _admin, (owner, other) = concept_accounts
    note = owner["notes"][0]
    url = path(owner, note)
    assert live.get(url).status_code == 401
    assert live.get(url, headers=auth(other)).status_code == 404
    for change, code in (
        ({"expected_quote": "伪造原文"}, 409),
        ({"evidence_key": "E999"}, 409),
        ({"definition": " "}, 422),
        ({"confidence": 1}, 422),
    ):
        assert live.post(url, headers=auth(owner), json=payload(**change)).status_code == code
    assert live.get(url + "?tenant_id=other", headers=auth(owner)).status_code == 422
    assert live.post(url + "?x=1", headers=auth(owner), json=payload()).status_code == 422
    response = live.post(url, headers=auth(owner), json=payload())
    assert response.status_code == 200
    row = response.json()["data"]
    assert row["quote"] == note["source_text"] and row["method"] == "manual"
    assert row["confidence"] is None and row["created_by"] == owner["user_id"]
    assert row["id"] in {r["id"] for r in live.get(url, headers=auth(owner)).json()["data"]}
    wrong = {**note, "revision_id": owner["notes"][1]["revision_id"]}
    assert live.post(path(owner, wrong), headers=auth(owner), json=payload()).status_code == 404
    new = protocol_revision(owner, note["snapshot_id"], note["source_text"], "后续修订")
    assert live.get(path(owner, new), headers=auth(owner)).json()["data"] == []


def test_concept_same_key_concurrency_audit_and_conflict(concept_accounts):
    _live, admin, (owner, _other) = concept_accounts
    note, body = owner["notes"][0], payload(name="并发定义")
    with ThreadPoolExecutor(max_workers=3) as workers:
        result = list(workers.map(lambda _: create(owner, note, body), range(3)))
    assert len({r["id"] for r in result}) == 1
    with pytest.raises(HTTPException) as denied:
        create(owner, note, {**body, "definition": "不同定义"})
    assert denied.value.status_code == 409
    with admin.connect() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM audit_events WHERE resource_id=:id "
                    "AND action='organization.concept_created'"
                ),
                {"id": result[0]["id"]},
            ).scalar_one()
            == 1
        )


def test_concept_atomic_audit_failure_and_quota_replay(concept_accounts, monkeypatch):
    _live, admin, (owner, _other) = concept_accounts
    note, body = owner["notes"][1], payload(name="回滚定义")

    def failed(*args, **kwargs):
        raise RuntimeError("controlled concept audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(concepts, "record", failed)
        with pytest.raises(RuntimeError, match="controlled concept"):
            create(owner, note, body)
    with admin.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM knowledge_concepts WHERE idempotency_key=:key"),
                {"key": body["idempotency_key"]},
            ).scalar_one()
            == 0
        )
    saved = create(owner, note, body)
    for name in ("MAX_REVISION_CONCEPTS", "MAX_SPACE_CONCEPTS"):
        with monkeypatch.context() as patch:
            patch.setattr(concepts, name, 0)
            assert create(owner, note, body)["id"] == saved["id"]
            with pytest.raises(HTTPException) as denied:
                create(owner, note)
            assert denied.value.status_code == 429


def test_concept_application_rls_composite_fks_and_immutability(concept_accounts):
    _live, admin, (owner, other) = concept_accounts
    note = owner["notes"][0]
    with get_engine().begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM knowledge_concepts")).scalar_one() == 0
        context(conn, other)
        assert (
            conn.execute(
                text("SELECT count(*) FROM knowledge_concepts WHERE tenant_id=:tenant"),
                {"tenant": owner["tenant_id"]},
            ).scalar_one()
            == 0
        )
    params = {
        "id": uuid4(),
        "tenant": owner["tenant_id"],
        "user": owner["user_id"],
        "note": note["note_id"],
        "snapshot": note["snapshot_id"],
        "revision": note["revision_id"],
        "key": uuid4(),
    }
    insert = (
        "INSERT INTO knowledge_concepts(id,tenant_id,note_id,snapshot_id,revision_id,created_by,"
        "idempotency_key,request_sha256,name,definition,evidence_key) "
        "VALUES(:id,:tenant,:note,:snapshot,:revision,:user,:key,repeat('a',64),'定义','人工','E001')"
    )
    for sql, changes in (
        ("UPDATE knowledge_concepts SET name='tamper'", {}),
        ("DELETE FROM knowledge_concepts", {}),
        (insert, {"user": other["user_id"]}),
    ):
        with pytest.raises(DBAPIError) as denied, get_engine().begin() as conn:
            context(conn, owner)
            conn.execute(text(sql), {**params, **changes})
        assert denied.value.orig.sqlstate == "42501"
    for changes in (
        {"revision": other["notes"][0]["revision_id"]},
        {"note": owner["notes"][1]["note_id"]},
        {"snapshot": uuid4()},
        {"user": uuid4()},
    ):
        with pytest.raises(DBAPIError) as denied, admin.begin() as conn:
            conn.execute(text(insert), {**params, **changes})
        assert denied.value.orig.sqlstate == "23503"
    with pytest.raises(DBAPIError) as denied, admin.begin() as conn:
        conn.execute(text(insert.replace("'E001'", "'E999'")), params)
    assert denied.value.orig.sqlstate == "23503"
    with admin.connect() as conn:
        assert conn.execute(
            text(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                "WHERE oid='knowledge_concepts'::regclass"
            )
        ).scalar_one()


def test_concept_membership_revocation_and_real_inventory_limit(concept_accounts):
    live, admin, (owner, _other) = concept_accounts
    note = protocol_note(owner, "概念上限协议")
    for index in range(20):
        create(owner, note, payload(name=f"概念{index}"))
    assert len(live.get(path(owner, note), headers=auth(owner)).json()["data"]) == 20
    with pytest.raises(HTTPException) as denied:
        create(owner, note)
    assert denied.value.status_code == 429
    try:
        with admin.begin() as conn:
            conn.execute(
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
        with admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE tenant_members SET active=true "
                    "WHERE tenant_id=:tenant AND user_id=:user"
                ),
                {"tenant": owner["tenant_id"], "user": owner["user_id"]},
            )


def test_concept_logout_during_actual_tenant_lock_wait(concept_accounts):
    live, admin, (owner, _other) = concept_accounts
    # Last case for this disposable module account: revoke its existing real
    # session instead of consuming an unrelated shared login IP bucket.
    suspended = owner
    body = payload(name="锁等待定义")
    with ThreadPoolExecutor(max_workers=1) as workers, admin.connect() as blocker:
        transaction = blocker.begin()
        try:
            blocker.execute(
                text("SELECT id FROM tenants WHERE id=:tenant FOR NO KEY UPDATE"),
                {"tenant": owner["tenant_id"]},
            )
            pid = blocker.execute(text("SELECT pg_backend_pid()")).scalar_one()
            pending = workers.submit(create, suspended, owner["notes"][0], body)
            blocked = False
            deadline = time.monotonic() + 10
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
            assert live.post("/api/v1/auth/logout", headers=auth(suspended)).status_code == 200
        finally:
            transaction.rollback()
        with pytest.raises(HTTPException) as denied:
            pending.result(timeout=10)
        assert denied.value.status_code == 401
