"""Actual topic trees, concurrent guards and atomic audit through non-bypass RLS."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from app.db.session import get_engine
from app.modules.organization import service
from app.modules.organization.content import LabelUpdateInput
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_organization_integration import (
    auth,
    context,
    create,
    endpoint,
    identity,
    organization_accounts,
    set_label,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]


@pytest.fixture(scope="module")
def hierarchy_accounts():
    yield from organization_accounts.__wrapped__()


def update(account, label, version=1, **changes):
    return service.update_label(
        identity(account),
        UUID(account["tenant_id"]),
        UUID(label["id"]),
        LabelUpdateInput.model_validate({"expected_version": version, **changes}),
    )


def test_topic_tree_http_version_omit_null_and_exact_filter(hierarchy_accounts):
    live, admin, (owner, other) = hierarchy_accounts
    root, child, leaf = [create(owner, name, "topic") for name in ("根", "子", "孙")]
    path = endpoint(owner) + "/labels/" + child["id"]
    body = {"parent_id": root["id"], "expected_version": 1}
    assert live.patch(path, json=body).status_code == 401
    assert live.patch(path, headers=auth(other), json=body).status_code == 404
    response = live.patch(path, headers=auth(owner), json=body)
    assert response.status_code == 200
    changed = response.json()["data"]
    assert changed["parent_id"] == root["id"] and changed["version"] == 2
    assert live.patch(path, headers=auth(owner), json=body).status_code == 409
    assert update(owner, child, 2, parent_id=root["id"])["version"] == 2
    assert update(owner, child, 2, name="子改名")["parent_id"] == root["id"]
    update(owner, leaf, parent_id=child["id"])
    set_label(owner, owner["notes"][0], child)
    notes = endpoint(owner) + "/notes"
    assert (
        live.get(notes, headers=auth(owner), params={"label_id": root["id"]}).json()["data"] == []
    )
    assert (
        len(live.get(notes, headers=auth(owner), params={"label_id": child["id"]}).json()["data"])
        == 1
    )
    with pytest.raises(HTTPException) as cycle:
        update(owner, root, parent_id=leaf["id"])
    assert cycle.value.detail["code"] == "TOPIC_PARENT_CYCLE"
    moved = update(owner, child, 3, parent_id=None)
    assert moved["parent_id"] is None and moved["version"] == 4
    with admin.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM audit_events WHERE resource_id=:id "
                    "AND action='organization.label_updated'"
                ),
                {"id": child["id"]},
            ).scalar_one()
            == 3
        )


def test_topic_archived_parent_preserved_and_cross_namespace_refused(hierarchy_accounts):
    _live, _admin, (owner, other) = hierarchy_accounts
    root, child = [create(owner, name, "topic") for name in ("归档父", "归档子")]
    tag = create(owner, "非主题")
    foreign = create(other, "外空间父", "topic")
    update(owner, child, parent_id=root["id"])
    update(owner, root, active=False)
    assert update(owner, child, 2, name="保留旧父")["parent_id"] == root["id"]
    fresh = create(owner, "新挂载", "topic")
    for parent, code in (
        (root, "TOPIC_PARENT_ARCHIVED"),
        (tag, "TOPIC_PARENT_NOT_FOUND"),
        (foreign, "TOPIC_PARENT_NOT_FOUND"),
    ):
        with pytest.raises(HTTPException) as denied:
            update(owner, fresh, parent_id=parent["id"])
        assert denied.value.detail["code"] == code
    for parent in (None, root["id"]):
        with pytest.raises(HTTPException) as denied:
            update(owner, tag, parent_id=parent)
        assert denied.value.detail["code"] == "TOPIC_PARENT_ONLY"
    assert update(owner, child, 3, parent_id=None)["parent_id"] is None


def test_topic_parent_and_name_roll_back_on_audit_failure(hierarchy_accounts, monkeypatch):
    _live, admin, (owner, _other) = hierarchy_accounts
    root, child = [create(owner, name, "topic") for name in ("事务父", "事务子")]

    def fail(*_args, **_kwargs):
        raise RuntimeError("controlled hierarchy audit failure")

    with monkeypatch.context() as patched:
        patched.setattr(service, "record", fail)
        with pytest.raises(RuntimeError, match="controlled hierarchy"):
            update(owner, child, name="必须回滚", parent_id=root["id"])
    with admin.connect() as connection:
        row = connection.execute(
            text("SELECT name,parent_id,version,updated_by FROM knowledge_labels WHERE id=:id"),
            {"id": child["id"]},
        ).one()
        assert tuple(row) == ("事务子", None, 1, None)


def test_topic_concurrent_inverse_moves_allow_only_one(hierarchy_accounts):
    _live, admin, (owner, _other) = hierarchy_accounts
    a, b = [create(owner, name, "topic") for name in ("竞争甲", "竞争乙")]
    start = Barrier(2)

    def move(pair):
        start.wait(timeout=10)
        try:
            update(owner, pair[0], parent_id=pair[1]["id"])
            return "saved"
        except HTTPException as error:
            return error.detail["code"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(move, [(a, b), (b, a)])) == ["TOPIC_PARENT_CYCLE", "saved"]
    with admin.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM knowledge_labels "
                    "WHERE id IN (:a,:b) AND parent_id IS NOT NULL"
                ),
                {"a": a["id"], "b": b["id"]},
            ).scalar_one()
            == 1
        )


def test_topic_app_role_direct_guard_and_rls(hierarchy_accounts):
    _live, _admin, (owner, other) = hierarchy_accounts
    root, child = [create(owner, name, "topic") for name in ("直写父", "直写子")]
    foreign = create(other, "直写外空间", "topic")
    tag = create(owner, "直写标签")
    update(owner, child, parent_id=root["id"])
    statement = text("UPDATE knowledge_labels SET parent_id=:parent,updated_by=:user WHERE id=:id")
    for label, parent, code in (
        (root, child, "23514"),
        (root, root, "23514"),
        (root, foreign, "23503"),
        (tag, root, "23514"),
    ):
        with pytest.raises(DBAPIError) as denied, get_engine().begin() as connection:
            context(connection, owner)
            connection.execute(
                statement, {"id": label["id"], "parent": parent["id"], "user": owner["user_id"]}
            )
        assert denied.value.orig.sqlstate == code
    with get_engine().begin() as connection:
        context(connection, other)
        assert (
            connection.execute(
                statement, {"id": root["id"], "parent": None, "user": other["user_id"]}
            ).rowcount
            == 0
        )


def test_topic_recovery_fingerprint_detects_disabled_guard_or_elevated_function(hierarchy_accounts):
    from scripts.backup_recovery import trigger_fingerprint

    _live, admin, _accounts = hierarchy_accounts
    with admin.connect() as connection:
        original = trigger_fingerprint(connection, "public", "knowledge_labels")
        assert len(original) == 1 and original[0][0] == "topic_parent_guard"
        assert original[0][6] is False
        connection.rollback()
        with connection.begin():
            connection.execute(
                text("ALTER TABLE knowledge_labels DISABLE TRIGGER topic_parent_guard")
            )
            assert trigger_fingerprint(connection, "public", "knowledge_labels") != original
            connection.rollback()
        with connection.begin():
            connection.execute(text("ALTER FUNCTION public.check_topic_parent() SECURITY DEFINER"))
            assert trigger_fingerprint(connection, "public", "knowledge_labels") != original
            connection.rollback()
        assert trigger_fingerprint(connection, "public", "knowledge_labels") == original


@pytest.mark.parametrize("isolation", ["READ COMMITTED", "REPEATABLE READ"])
def test_topic_direct_concurrent_guard_rejects_stale_inverse_move(hierarchy_accounts, isolation):
    _live, admin, (owner, _other) = hierarchy_accounts
    a, b = [create(owner, f"直接竞争{isolation}{name}", "topic") for name in ("甲", "乙")]
    start = Barrier(2)

    def move(pair):
        try:
            with (
                get_engine().connect().execution_options(isolation_level=isolation) as connection,
                connection.begin(),
            ):
                context(connection, owner)
                connection.execute(text("SET LOCAL statement_timeout='12s'"))
                connection.execute(
                    text("SELECT id FROM knowledge_labels WHERE id=:id"), {"id": pair[1]["id"]}
                )
                start.wait(timeout=10)
                connection.execute(
                    text(
                        "UPDATE knowledge_labels SET parent_id=:parent,updated_by=:user "
                        "WHERE id=:id"
                    ),
                    {"id": pair[0]["id"], "parent": pair[1]["id"], "user": owner["user_id"]},
                )
            return "saved"
        except DBAPIError as error:
            return error.orig.sqlstate

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(move, [(a, b), (b, a)]))
    assert result.count("saved") == 1 and set(result) <= {"saved", "23514", "40P01", "40001"}
    with admin.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM knowledge_labels "
                    "WHERE id IN (:a,:b) AND parent_id IS NOT NULL"
                ),
                {"a": a["id"], "b": b["id"]},
            ).scalar_one()
            == 1
        )
