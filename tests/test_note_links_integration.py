"""Real PostgreSQL/API manual note-link isolation, transactions and races."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from app.db.session import get_engine
from app.modules.notes import links
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
def link_accounts():
    yield from organization_accounts.__wrapped__()


def set_link(account, note, target, *, active=True):
    return links.set_link(
        identity(account),
        UUID(account["tenant_id"]),
        UUID(note["note_id"]),
        UUID(target["note_id"]),
        active=active,
    )


def path(account, note, target=None):
    result = endpoint(account) + f"/notes/{note['note_id']}/links"
    return result if target is None else result + f"/{target['note_id']}"


def test_real_link_api_identity_validation_and_bidirectional_visibility(link_accounts):
    live, _admin, (owner, other) = link_accounts
    left, right = owner["notes"]
    assert live.get(path(owner, left)).status_code == 401
    assert live.get(path(owner, left), headers=auth(other)).status_code == 404
    assert live.put(path(owner, left, left), headers=auth(owner)).status_code == 409
    assert live.put(path(owner, left) + "/not-a-uuid", headers=auth(owner)).status_code == 422
    foreign = other["notes"][0]
    assert live.put(path(owner, left, foreign), headers=auth(owner)).status_code == 404
    response = live.put(path(owner, left, right), headers=auth(owner))
    assert response.status_code == 200
    forward = response.json()["data"]
    reverse = live.get(path(owner, right), headers=auth(owner)).json()["data"]
    assert len(forward) == len(reverse) == 1
    assert forward[0]["id"] == reverse[0]["id"]
    assert forward[0]["note_id"] == right["note_id"]
    assert reverse[0]["note_id"] == left["note_id"]
    assert forward[0]["method"] == "manual" and forward[0]["confidence"] is None


def test_real_link_concurrent_association_is_idempotent_with_one_audit(link_accounts):
    _live, admin, (owner, _other) = link_accounts
    left, right = protocol_note(owner, "并发链接甲"), protocol_note(owner, "并发链接乙")
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _: set_link(owner, left, right), range(4)))
    assert len({row[0]["id"] for row in results}) == 1
    link_id = results[0][0]["id"]
    with admin.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM audit_events "
                    "WHERE resource_id=:id AND action='notes.linked'"
                ),
                {"id": link_id},
            ).scalar_one()
            == 1
        )


def test_real_link_transitions_keep_one_row_and_bind_the_current_actor(link_accounts):
    live, admin, (owner, other) = link_accounts
    left, right = protocol_note(owner, "状态链接甲"), protocol_note(owner, "状态链接乙")
    assert (
        live.post(
            endpoint(owner) + "/members",
            headers=auth(owner),
            json={"user_id": other["user_id"], "role": "member"},
        ).status_code
        == 201
    )
    member = {**other, "tenant_id": owner["tenant_id"]}
    try:
        created = set_link(member, left, right)
        link_id = created[0]["id"]
        assert set_link(member, left, right) == created
        assert set_link(member, left, right, active=False) == []
        assert set_link(member, left, right, active=False) == []
        restored = set_link(owner, left, right)
        assert restored[0]["id"] == link_id
        with admin.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT active,created_by,updated_by FROM note_links "
                    "WHERE tenant_id=:tenant AND id=:id"
                ),
                {"tenant": owner["tenant_id"], "id": link_id},
            ).one()
            assert row.active
            assert str(row.created_by) == other["user_id"]
            assert str(row.updated_by) == owner["user_id"]
            actions = (
                connection.execute(
                    text(
                        "SELECT action FROM audit_events WHERE tenant_id=:tenant "
                        "AND resource_id=:id AND action IN ('notes.linked','notes.unlinked')"
                    ),
                    {"tenant": owner["tenant_id"], "id": link_id},
                )
                .scalars()
                .all()
            )
            assert actions.count("notes.linked") == 2
            assert actions.count("notes.unlinked") == 1
        assert (
            live.delete(
                endpoint(owner) + f"/members/{other['user_id']}", headers=auth(owner)
            ).status_code
            == 200
        )
        with pytest.raises(HTTPException) as denied:
            set_link(member, left, right, active=False)
        assert denied.value.status_code == 404
    finally:
        live.delete(endpoint(owner) + f"/members/{other['user_id']}", headers=auth(owner))


def test_real_links_survive_revisions_and_report_latest_target_title(link_accounts):
    live, _admin, (owner, _other) = link_accounts
    left, right = protocol_note(owner, "跨版本链接甲"), protocol_note(owner, "跨版本链接旧标题")
    set_link(owner, left, right)
    newer = protocol_revision(owner, right["snapshot_id"], right["source_text"], "跨版本链接新标题")
    rows = live.get(path(owner, left), headers=auth(owner)).json()["data"]
    latest = live.get(endpoint(owner) + f"/notes/{right['note_id']}", headers=auth(owner)).json()[
        "data"
    ]["revision"]
    assert len(rows) == 1
    assert rows[0]["title"] == "用户导入笔记：跨版本链接新标题"
    assert latest["id"] == newer["revision_id"]
    assert rows[0]["version"] == latest["version"]
    assert (
        live.get(path(owner, right), headers=auth(owner)).json()["data"][0]["note_id"]
        == left["note_id"]
    )


def test_real_link_rls_permissions_canonical_order_and_composite_fks(link_accounts):
    _live, admin, (owner, other) = link_accounts
    left, right = protocol_note(owner, "RLS链接甲"), protocol_note(owner, "RLS链接乙")
    saved = set_link(owner, left, right)[0]
    with get_engine().begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM note_links")).scalar_one() == 0
        context(connection, other)
        assert (
            connection.execute(
                text("SELECT count(*) FROM note_links WHERE tenant_id=:tenant"),
                {"tenant": owner["tenant_id"]},
            ).scalar_one()
            == 0
        )
    parameters = {
        "id": uuid4(),
        "tenant": owner["tenant_id"],
        "left": min(UUID(left["note_id"]), UUID(right["note_id"])),
        "right": max(UUID(left["note_id"]), UUID(right["note_id"])),
        "user": owner["user_id"],
        "other": other["user_id"],
        "saved": saved["id"],
    }
    insert = (
        "INSERT INTO note_links"
        "(id,tenant_id,left_note_id,right_note_id,created_by,updated_by,active) "
        "VALUES(:id,:tenant,:left,:right,:user,:user,true)"
    )
    for statement in (
        "UPDATE note_links SET left_note_id=right_note_id WHERE id=:saved",
        "UPDATE note_links SET created_by=:other WHERE id=:saved",
        "UPDATE note_links SET updated_by=:other,active=false WHERE id=:saved",
        "DELETE FROM note_links WHERE id=:saved",
        insert.replace(":user,:user", ":other,:other"),
    ):
        with pytest.raises(DBAPIError) as denied, get_engine().begin() as connection:
            context(connection, owner)
            connection.execute(text(statement), parameters)
        assert denied.value.orig.sqlstate == "42501"
    foreign_pair = sorted(
        (UUID(other["notes"][0]["note_id"]), UUID(left["note_id"])),
        key=lambda value: value.int,
    )
    actor_pair = sorted(
        (UUID(left["note_id"]), UUID(owner["notes"][0]["note_id"])),
        key=lambda value: value.int,
    )
    order_pair = sorted(
        (UUID(right["note_id"]), UUID(owner["notes"][1]["note_id"])),
        key=lambda value: value.int,
    )
    for changes, state in (
        ({"left": foreign_pair[0], "right": foreign_pair[1]}, "23503"),
        ({"left": actor_pair[0], "right": actor_pair[1], "user": str(uuid4())}, "23503"),
        ({"left": order_pair[1], "right": order_pair[0]}, "23514"),
    ):
        with pytest.raises(DBAPIError) as denied, admin.begin() as connection:
            connection.execute(text(insert), {**parameters, "id": uuid4(), **changes})
        assert denied.value.orig.sqlstate == state
    with admin.connect() as connection:
        assert connection.execute(
            text(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                "WHERE oid='note_links'::regclass"
            )
        ).scalar_one()


def test_real_link_audit_rollback_and_both_quotas(link_accounts, monkeypatch):
    _live, admin, (owner, _other) = link_accounts
    left, right = protocol_note(owner, "原子链接甲"), protocol_note(owner, "原子链接乙")

    def failed(*_args, **_kwargs):
        raise RuntimeError("controlled link audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(links, "record", failed)
        with pytest.raises(RuntimeError, match="controlled link"):
            set_link(owner, left, right)
    canonical = sorted((left["note_id"], right["note_id"]), key=UUID)
    with admin.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM note_links WHERE tenant_id=:tenant "
                    "AND left_note_id=:left AND right_note_id=:right"
                ),
                {"tenant": owner["tenant_id"], "left": canonical[0], "right": canonical[1]},
            ).scalar_one()
            == 0
        )
    saved = set_link(owner, left, right)
    third = protocol_note(owner, "单笔记配额链接")
    with monkeypatch.context() as patch:
        patch.setattr(links, "MAX_NOTE_LINKS", 1)
        assert set_link(owner, left, right) == saved
        with pytest.raises(HTTPException) as quota:
            set_link(owner, left, third)
        assert quota.value.status_code == 429
        assert quota.value.detail["code"] == "NOTE_LINK_QUOTA_EXCEEDED"
    with monkeypatch.context() as patch:
        patch.setattr(links, "MAX_SPACE_LINKS", 1)
        with pytest.raises(HTTPException) as quota:
            set_link(owner, right, third)
        assert quota.value.status_code == 429
        assert quota.value.detail["code"] == "SPACE_LINK_QUOTA_EXCEEDED"


def test_real_link_logout_while_waiting_for_tenant_lock(link_accounts):
    live, admin, (owner, _other) = link_accounts
    email = live.get("/api/v1/auth/me", headers=auth(owner)).json()["data"]["email"]
    response = live.post(
        "/api/v1/auth/login", json={"email": email, "password": "organization-fixture-only-2026"}
    )
    assert response.status_code == 200
    suspended = {**owner, "access_token": response.json()["data"]["access_token"]}
    left, right = protocol_note(owner, "锁等待链接甲"), protocol_note(owner, "锁等待链接乙")
    with ThreadPoolExecutor(max_workers=1) as workers, admin.connect() as blocker:
        transaction = blocker.begin()
        try:
            blocker.execute(
                text("SELECT id FROM tenants WHERE id=:tenant FOR UPDATE"),
                {"tenant": owner["tenant_id"]},
            )
            pid = blocker.execute(text("SELECT pg_backend_pid()")).scalar_one()
            pending = workers.submit(set_link, suspended, left, right)
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
        canonical = sorted((left["note_id"], right["note_id"]), key=UUID)
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM note_links WHERE tenant_id=:tenant "
                    "AND left_note_id=:left AND right_note_id=:right"
                ),
                {"tenant": owner["tenant_id"], "left": canonical[0], "right": canonical[1]},
            ).scalar_one()
            == 0
        )
