"""Real learning-state and reminder acceptance; no notification delivery is implied."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.db.session import get_engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_organization_integration import (
    auth,
    context,
    endpoint,
    identity,
)
from test_organization_integration import (
    organization_accounts as organization_accounts_fixture,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]


def learning_path(account, note_id):
    return endpoint(account) + f"/notes/{note_id}/learning"


@pytest.fixture(scope="module")
def learning_accounts():
    yield from organization_accounts_fixture.__wrapped__()


def test_learning_requires_identity_and_preserves_statuses_and_due_records(learning_accounts):
    live, _admin, (owner, other) = learning_accounts
    note = owner["notes"][0]
    path = learning_path(owner, note["note_id"])

    assert live.get(endpoint(owner) + "/learning").status_code == 401
    assert (
        live.put(
            path,
            headers={**auth(other), "X-Tenant-ID": owner["tenant_id"]},
            json={"status": "seen"},
        ).status_code
        == 404
    )
    assert live.put(path, headers=auth(owner), json={"status": "invalid"}).status_code == 422

    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    for status in ("unseen", "seen", "skimmed", "deep_read"):
        response = live.put(
            path,
            headers=auth(owner),
            json={"status": status, "reminder_at": past},
        )
        assert response.status_code == 200
        assert response.json()["data"]["status"] == status

    due = live.get(endpoint(owner) + "/learning", params={"due": "true"}, headers=auth(owner))
    assert due.status_code == 200
    assert all(row["note_id"] != note["note_id"] for row in due.json()["data"])

    response = live.put(
        path,
        headers=auth(owner),
        json={"status": "seen", "reminder_at": past},
    )
    assert response.status_code == 200
    due = live.get(endpoint(owner) + "/learning", params={"due": "true"}, headers=auth(owner))
    assert any(row["note_id"] == note["note_id"] for row in due.json()["data"])

    audit = live.get(endpoint(owner) + "/audit-events", headers=auth(owner))
    assert audit.status_code == 200
    assert any(event["action"] == "learning.updated" for event in audit.json()["data"])


def test_learning_is_per_member_and_revocation_blocks_access(learning_accounts):
    live, _admin, (owner, other) = learning_accounts
    note = owner["notes"][0]
    members = endpoint(owner) + "/members"
    owner_path = learning_path(owner, note["note_id"])
    assert live.put(owner_path, headers=auth(owner), json={"status": "seen"}).status_code == 200
    added = live.post(
        members, headers=auth(owner), json={"user_id": other["user_id"], "role": "member"}
    )
    assert added.status_code == 201
    shared = {**other, "tenant_id": owner["tenant_id"]}
    path = learning_path(shared, note["note_id"])
    assert live.put(path, headers=auth(shared), json={"status": "skimmed"}).status_code == 200
    owner_rows = live.get(endpoint(owner) + "/learning", headers=auth(owner)).json()["data"]
    other_rows = live.get(endpoint(shared) + "/learning", headers=auth(shared)).json()["data"]
    assert any(row["note_id"] == note["note_id"] and row["status"] == "seen" for row in owner_rows)
    assert any(
        row["note_id"] == note["note_id"] and row["status"] == "skimmed" for row in other_rows
    )

    assert live.delete(members + f"/{other['user_id']}", headers=auth(owner)).status_code == 200
    assert live.get(endpoint(shared) + "/learning", headers=auth(shared)).status_code == 404
    assert live.put(path, headers=auth(shared), json={"status": "deep_read"}).status_code == 404


def test_learning_rls_permissions_composite_foreign_keys_and_logout():
    for live, admin, (owner, other) in organization_accounts_fixture.__wrapped__():
        path = learning_path(owner, owner["notes"][0]["note_id"])
        assert live.put(path, headers=auth(owner), json={"status": "unseen"}).status_code == 200
        with get_engine().begin() as connection:
            context(connection, other)
            assert (
                connection.execute(
                    text("SELECT count(*) FROM note_learning WHERE tenant_id=:t"),
                    {"t": owner["tenant_id"]},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text("SELECT rolbypassrls FROM pg_roles WHERE rolname=current_user")
                ).scalar_one()
                is False
            )
            assert (
                connection.execute(
                    text("SELECT relforcerowsecurity FROM pg_class WHERE relname='note_learning'")
                ).scalar_one()
                is True
            )
        with get_engine().begin() as connection:
            context(connection, owner)
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text("DELETE FROM note_learning WHERE tenant_id=:t"), {"t": owner["tenant_id"]}
                )
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text("UPDATE note_learning SET user_id=:u WHERE tenant_id=:t"),
                    {"u": other["user_id"], "t": owner["tenant_id"]},
                )
        with admin.begin() as connection:
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO note_learning(id,tenant_id,note_id,user_id,status) "
                        "VALUES(:id,:t,:n,:u,'unseen')"
                    ),
                    {
                        "id": uuid4(),
                        "t": UUID(owner["tenant_id"]),
                        "n": UUID(other["notes"][0]["note_id"]),
                        "u": UUID(owner["user_id"]),
                    },
                )
        assert live.post("/api/v1/auth/logout", headers=auth(owner)).status_code == 200
        assert live.get(endpoint(owner) + "/learning", headers=auth(owner)).status_code == 401
        assert live.put(path, headers=auth(owner), json={"status": "seen"}).status_code == 401


def test_learning_exact_note_read_is_not_hidden_by_list_limit(learning_accounts):
    live, admin, (owner, _other) = learning_accounts
    note = owner["notes"][0]
    path = learning_path(owner, note["note_id"])
    assert (
        live.put(
            path, headers=auth(owner), json={"status": "seen", "reminder_at": "2026-11-08T12:00:00"}
        ).status_code
        == 422
    )
    assert live.put(path, headers=auth(owner), json={"status": "seen"}).status_code == 200
    # Dedicated disposable tenant only: create 101 legitimate note identities
    # attached to distinct existing protocol sources, without model/network calls.
    with admin.begin() as connection:
        source_ids = (
            connection.execute(
                text(
                    "INSERT INTO sources(id,tenant_id,batch_id,original_url,canonical_url,"
                    "canonical_sha256,status) SELECT gen_random_uuid(),tenant_id,batch_id,"
                    "original_url||'/limit-'||g,canonical_url||'/limit-'||g, "
                    "encode(sha256(convert_to(canonical_url||'/limit-'||g,'UTF8')),'hex'),"
                    "'accepted' FROM sources CROSS JOIN generate_series(1,101) g "
                    "WHERE id=(SELECT source_id FROM notes WHERE id=:n) RETURNING id"
                ),
                {"n": note["note_id"]},
            )
            .scalars()
            .all()
        )
        for source_id in source_ids:
            new_id = uuid4()
            connection.execute(
                text("INSERT INTO notes(id,tenant_id,source_id,created_by) VALUES(:n,:t,:s,:u)"),
                {"n": new_id, "t": owner["tenant_id"], "s": source_id, "u": owner["user_id"]},
            )
            connection.execute(
                text(
                    "INSERT INTO note_learning(id,tenant_id,note_id,user_id,status,reminder_at) "
                    "VALUES(:id,:t,:n,:u,'unseen',now()-interval '1 minute')"
                ),
                {"id": uuid4(), "t": owner["tenant_id"], "n": new_id, "u": owner["user_id"]},
            )
    rows = live.get(endpoint(owner) + "/learning", headers=auth(owner)).json()["data"]
    assert len(rows) == 100 and all(row["note_id"] != note["note_id"] for row in rows)
    exact = live.get(
        endpoint(owner) + "/learning", params={"note_id": note["note_id"]}, headers=auth(owner)
    )
    assert exact.status_code == 200 and len(exact.json()["data"]) == 1
    assert exact.json()["data"][0]["note_id"] == note["note_id"]


def test_learning_logout_while_waiting_for_tenant_lock(learning_accounts):
    from app.modules.learning.content import LearningUpdate
    from app.modules.learning.service import update
    from fastapi import HTTPException

    live, admin, (owner, _other) = learning_accounts
    # Last case for this disposable fixture: no extra login or quota reset.
    suspended = owner
    with ThreadPoolExecutor(max_workers=1) as workers, admin.connect() as blocker:
        transaction = blocker.begin()
        try:
            blocker.execute(
                text("SELECT id FROM tenants WHERE id=:t FOR NO KEY UPDATE"),
                {"t": owner["tenant_id"]},
            )
            pid = blocker.execute(text("SELECT pg_backend_pid()")).scalar_one()
            pending = workers.submit(
                update,
                identity(suspended),
                UUID(owner["tenant_id"]),
                UUID(owner["notes"][0]["note_id"]),
                LearningUpdate(status="deep_read"),
            )
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
