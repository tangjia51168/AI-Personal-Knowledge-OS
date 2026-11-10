"""Actual collection RLS, composite references, version concurrency and atomic audit."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from app.db.session import get_engine
from app.modules.organization import collections as c
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_organization_integration import auth, context, endpoint, identity, organization_accounts

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]


@pytest.fixture(scope="module")
def collection_accounts():
    yield from organization_accounts.__wrapped__()


def sources(admin, account):
    with admin.connect() as connection:
        return list(
            connection.execute(
                text("SELECT id FROM sources WHERE tenant_id=:t AND status='accepted' ORDER BY id"),
                {"t": account["tenant_id"]},
            ).scalars()
        )


def create(account, name):
    return c.create(
        identity(account), UUID(account["tenant_id"]), c.CollectionInput(kind="folder", name=name)
    )


def attach(account, group, source, *, active=True, position=0):
    return c.set_item(
        identity(account),
        UUID(account["tenant_id"]),
        UUID(group["id"]),
        source,
        c.ItemInput(expected_version=group["version"], active=active, position=position),
    )


def test_real_create_attach_read_order_archive_and_source_preservation(collection_accounts):
    live, admin, (owner, other) = collection_accounts
    path = endpoint(owner) + "/collections"
    assert live.get(path).status_code == 401
    assert live.get(path, headers=auth(other)).status_code == 404
    assert live.get(path + "?tenant=x", headers=auth(owner)).status_code == 422
    response = live.post(
        path, headers=auth(owner), json={"kind": "collection", "name": " 课程合集 "}
    )
    assert response.status_code == 200
    group = response.json()["data"]
    source_ids = sources(admin, owner)
    group = attach(owner, group, source_ids[0], position=8)
    assert attach(owner, group, source_ids[0], position=8) == group
    page = live.get(path + f"/{group['id']}/items", headers=auth(owner)).json()["data"]
    assert page["items"] == [
        {"source_id": str(source_ids[0]), "position": 8, "url": page["items"][0]["url"]}
    ]
    assert page["collection"] == group
    group = c.update(
        identity(owner),
        UUID(owner["tenant_id"]),
        UUID(group["id"]),
        c.CollectionUpdate(expected_version=group["version"], name="已归档课程", active=False),
    )
    with pytest.raises(HTTPException) as error:
        attach(owner, group, source_ids[1])
    assert error.value.detail["code"] == "COLLECTION_ARCHIVED"
    group = attach(owner, group, source_ids[0], active=False, position=8)
    assert sources(admin, owner) == source_ids
    assert (
        live.get(path + f"/{group['id']}/items", headers=auth(owner)).json()["data"]["items"] == []
    )


def test_actual_concurrent_version_only_one_writer_and_cross_tenant_reference(collection_accounts):
    _live, admin, (owner, other) = collection_accounts
    group = create(owner, "并发组")
    ids = sources(admin, owner)

    def attempt(source):
        try:
            return attach(owner, group, source)
        except HTTPException as error:
            return error.detail["code"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ids))
    assert sum(isinstance(value, dict) for value in results) == 1
    assert "COLLECTION_VERSION_CONFLICT" in results
    current = next(value for value in results if isinstance(value, dict))
    with pytest.raises(HTTPException) as error:
        attach(owner, current, sources(admin, other)[0])
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        create(owner, "并发组")
    assert error.value.detail["code"] == "COLLECTION_NAME_CONFLICT"


def test_actual_database_rls_composite_fk_and_immutable_subject_columns(collection_accounts):
    _live, admin, (owner, other) = collection_accounts
    group = create(owner, "数据库边界")
    with get_engine().begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM collections")).scalar_one() == 0
        context(connection, other)
        assert (
            connection.execute(
                text("SELECT count(*) FROM collections WHERE id=:id"), {"id": group["id"]}
            ).scalar_one()
            == 0
        )
    commands = [
        ("UPDATE collections SET tenant_id=:foreign WHERE id=:id", "42501"),
        ("DELETE FROM collections WHERE id=:id", "42501"),
        (
            "INSERT INTO collection_items(tenant_id,collection_id,source_id,position,"
            "created_by,updated_by) VALUES(:tenant,:id,:source,0,:user,:user)",
            "23503",
        ),
    ]
    for command, code in commands:
        with get_engine().connect() as connection:
            transaction = connection.begin()
            context(connection, owner)
            with pytest.raises(DBAPIError) as error:
                connection.execute(
                    text(command),
                    {
                        "id": group["id"],
                        "tenant": owner["tenant_id"],
                        "user": owner["user_id"],
                        "foreign": other["tenant_id"],
                        "source": sources(admin, other)[0],
                    },
                )
            assert error.value.orig.sqlstate == code
            transaction.rollback()


def test_actual_audit_failure_rolls_back_group_and_items(collection_accounts, monkeypatch):
    _live, admin, (owner, _other) = collection_accounts
    group = create(owner, "原子审计")

    def reject(*args, **kwargs):
        raise RuntimeError("AUDIT_FIXTURE")

    monkeypatch.setattr(c, "record", reject)
    with pytest.raises(RuntimeError, match="AUDIT_FIXTURE"):
        attach(owner, group, sources(admin, owner)[0])
    with pytest.raises(RuntimeError, match="AUDIT_FIXTURE"):
        create(owner, "未提交" + uuid4().hex)
    with get_engine().begin() as connection:
        context(connection, owner)
        assert (
            connection.execute(
                text("SELECT version FROM collections WHERE id=:id"), {"id": group["id"]}
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM collection_items WHERE collection_id=:id"),
                {"id": group["id"]},
            ).scalar_one()
            == 0
        )


def test_actual_member_can_organize_then_revocation_blocks_writes(collection_accounts):
    live, _admin, (owner, other) = collection_accounts
    path = endpoint(owner)
    response = live.post(
        path + "/members", headers=auth(owner), json={"user_id": other["user_id"], "role": "member"}
    )
    assert response.status_code == 201
    response = live.post(
        path + "/collections", headers=auth(other), json={"kind": "folder", "name": "成员整理"}
    )
    assert response.status_code == 200
    assert (
        live.delete(path + "/members/" + other["user_id"], headers=auth(owner)).status_code == 200
    )
    assert (
        live.post(
            path + "/collections", headers=auth(other), json={"kind": "folder", "name": "撤权后"}
        ).status_code
        == 404
    )
