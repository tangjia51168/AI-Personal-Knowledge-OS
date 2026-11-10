"""Real manual classification isolation and transactions; no inference or collection."""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import httpx
import pytest
from app.core.config import get_settings
from app.db.models import ProcessingJob
from app.db.session import get_engine
from app.modules.identity.access import (
    ActorIdentity,
    authenticate_session,
    member_access,
    token_digest,
)
from app.modules.notes import edits
from app.modules.notes import service as notes
from app.modules.notes.edit_content import NoteEditInput
from app.modules.organization import service
from app.modules.organization.content import LabelInput, LabelUpdateInput
from app.modules.sources.models import SourceSnapshot
from app.modules.sources.service import digest, save_batch
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]


def identity(account):
    return ActorIdentity(UUID(account["user_id"]), token_digest(account["access_token"]))


def auth(account):
    return {"Authorization": "Bearer " + account["access_token"]}


def endpoint(account):
    return f"/api/v1/spaces/{account['tenant_id']}"


def context(connection, account):
    connection.execute(
        text(
            "SELECT set_config('app.tenant_id',:tenant,true),set_config('app.user_id',:user,true)"
        ),
        {"tenant": account["tenant_id"], "user": account["user_id"]},
    )


def create(account, name, kind="tag"):
    return service.create_label(
        identity(account),
        UUID(account["tenant_id"]),
        LabelInput.model_validate({"kind": kind, "name": name}),
    )


def set_label(account, note, label, *, active=True):
    return service.set_note_label(
        identity(account),
        UUID(account["tenant_id"]),
        UUID(note["note_id"]),
        UUID(label["id"]),
        active=active,
    )


def protocol_revision(account, snapshot_id, source_text, title):
    tenant = UUID(account["tenant_id"])
    run_id, fresh = notes.begin_run(identity(account), tenant, snapshot_id, uuid4())
    assert fresh
    return notes.save_document(
        identity(account),
        tenant,
        run_id,
        json.dumps(
            {
                "title": title,
                "overview": {"text": "准备材料后验证输出。", "evidence_keys": ["E001"]},
                "sections": [
                    {
                        "kind": "steps",
                        "items": [{"text": "先准备3个材料。", "evidence_keys": ["E001"]}],
                    }
                ],
                "limitations": ["人工分类数据库协议夹具，无远端采集或模型推理"],
            },
            ensure_ascii=False,
        ),
        source_text,
    )


def protocol_note(account, title):
    tenant = UUID(account["tenant_id"])
    with notes.authorized_transaction(identity(account), tenant) as (session, _role):
        actor = authenticate_session(session, account["access_token"])
        source = save_batch(
            member_access(tenant, actor),
            f"https://example.com/organization-protocol/{uuid4()}",
            uuid4(),
        )["sources"][0]
        job = ProcessingJob(
            tenant_id=tenant,
            requested_by=actor.user.id,
            kind="source_import",
            status="running",
            idempotency_key=str(uuid4()),
            payload={"source_id": source["id"]},
        )
        session.add(job)
        session.flush()
        snapshot = SourceSnapshot(
            tenant_id=tenant,
            source_id=UUID(source["id"]),
            job_id=job.id,
            created_by=actor.user.id,
            version=1,
            provenance="user_import",
            fetched_url=source["canonical_url"],
            platform="user_import",
            content_kind="article",
            title="分类协议测试资料，不是采集证据",
            author="",
            body_text="先准备3个材料，再验证输出。",
            raw_sha256=digest("organization-protocol"),
            details={"fixture": "organization-database-only"},
            limitations=["USER_PROVIDED_CONTENT", "SOURCE_CONTENT_NOT_VERIFIED"],
        )
        session.add(snapshot)
        session.flush()
        snapshot_id, source_text = snapshot.id, snapshot.body_text
    result = protocol_revision(account, snapshot_id, source_text, title)
    return {**result, "snapshot_id": snapshot_id, "source_text": source_text}


@pytest.fixture(scope="module")
def organization_accounts():
    settings = get_settings()
    assert settings.app_env == "test" and settings.migration_database_url
    admin = create_engine(settings.migration_database_url.get_secret_value(), hide_parameters=True)
    accounts = []
    with httpx.Client(base_url=os.environ["API_BASE_URL"], trust_env=False, timeout=30) as live:
        try:
            for index in range(2):
                response = live.post(
                    "/api/v1/auth/register",
                    json={
                        "email": f"{uuid4()}@example.invalid",
                        "password": "organization-fixture-only-2026",
                    },
                )
                assert response.status_code == 201
                account = response.json()["data"]
                accounts.append(account)
                account["notes"] = [protocol_note(account, f"分类协议{index}甲")]
                if index == 0:
                    account["notes"].append(protocol_note(account, "分类协议0乙"))
            yield live, admin, accounts
        finally:
            with admin.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout='5s'"))
                for account in accounts:
                    params = {"tenant": account["tenant_id"]}
                    connection.execute(
                        text("SELECT id FROM tenants WHERE id=:tenant FOR UPDATE"), params
                    )
                    for table in (
                        "collection_items",
                        "collections",
                        "notification_deliveries",
                        "notification_ticks",
                        "notification_preferences",
                        "semantic_chunks",
                        "knowledge_concepts",
                        "note_learning",
                        "note_links",
                        "note_annotations",
                        "note_labels",
                        "knowledge_labels",
                        "evidence_items",
                        "note_sections",
                        "note_revisions",
                        "notes",
                        "note_runs",
                        "source_snapshots",
                        "sources",
                        "source_batches",
                        "audit_events",
                        "ai_usage",
                        "ai_providers",
                        "user_settings",
                        "job_dispatches",
                        "job_steps",
                        "job_attempts",
                        "processing_jobs",
                        "tenant_members",
                    ):
                        connection.execute(
                            text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), params
                        )
                for account in accounts:
                    params = {"user": account["user_id"], "tenant": account["tenant_id"]}
                    for table in (
                        "auth_security_events",
                        "auth_action_tokens",
                        "auth_sessions",
                        "auth_identities",
                    ):
                        connection.execute(text(f"DELETE FROM {table} WHERE user_id=:user"), params)
                    connection.execute(text("DELETE FROM tenants WHERE id=:tenant"), params)
                    connection.execute(text("DELETE FROM users WHERE id=:user"), params)
            admin.dispose()


def test_live_labels_validate_identity_name_and_tenant_namespace(organization_accounts):
    live, _admin, (owner, other) = organization_accounts
    path = endpoint(owner) + "/labels"
    assert live.get(path).status_code == 401
    assert live.get(path, headers=auth(other)).status_code == 404
    assert (
        live.get(path, headers={**auth(other), "X-Tenant-ID": owner["tenant_id"]}).status_code
        == 404
    )
    for body in (
        {"kind": "concept", "name": "invalid"},
        {"kind": "tag", "name": " "},
        {"kind": "tag", "name": "a" * 61},
        {"kind": "tag", "name": "unsafe\u200bname"},
        {"kind": "tag", "name": "line\nbreak"},
        {"kind": "tag", "name": "value", "tenant_id": other["tenant_id"]},
    ):
        assert live.post(path, headers=auth(owner), json=body).status_code == 422
    first = live.post(
        path, headers=auth(owner), json={"kind": "tag", "name": "  Ｐｏｗｅｒ　 Electronics  "}
    )
    assert first.status_code == 200
    label = first.json()["data"]
    assert set(label) == {"id", "kind", "name", "created_at"}
    assert label["name"] == "Power Electronics"
    duplicate = live.post(
        path, headers=auth(owner), json={"kind": "tag", "name": "power electronics"}
    )
    assert duplicate.status_code == 200 and duplicate.json()["data"] == label
    assert create(owner, "POWER ELECTRONICS", "topic")["id"] != label["id"]
    assert create(other, "Power Electronics")["id"] != label["id"]
    assert live.get(path, headers=auth(owner)).status_code == 200
    assert live.get(path + "/not-a-uuid", headers=auth(owner)).status_code == 404
    note = owner["notes"][0]
    labels_path = endpoint(owner) + f"/notes/{note['note_id']}/labels"
    assert live.get(labels_path).status_code == 401
    assert live.get(labels_path, headers=auth(other)).status_code == 404
    assert live.put(labels_path + "/not-a-uuid", headers=auth(owner)).status_code == 422
    assert live.put(labels_path + f"/{uuid4()}", headers=auth(owner)).status_code == 404
    foreign = create(other, "foreign label")
    assert live.put(labels_path + "/" + foreign["id"], headers=auth(owner)).status_code == 404


def test_real_label_concurrency_idempotent_transitions_and_version_independence(
    organization_accounts,
):
    live, admin, (owner, _other) = organization_accounts
    variants = ["  Ｃｏｎｃｕｒｒｅｎｔ　 Classification  ", "concurrent classification"]
    with ThreadPoolExecutor(max_workers=2) as workers:
        labels = list(workers.map(lambda name: create(owner, name, "topic"), variants))
    assert labels[0]["id"] == labels[1]["id"]
    label, note = labels[0], owner["notes"][0]
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: set_label(owner, note, label), range(2)))
    assert results[0] == results[1]
    assert {row["id"] for row in results[0]} == {label["id"]}
    path = endpoint(owner) + f"/notes/{note['note_id']}"
    original = live.get(path, headers=auth(owner)).json()["data"]["revision"]
    body = NoteEditInput.model_validate(
        {
            "idempotency_key": str(uuid4()),
            "base_revision_id": original["id"],
            "title": "分类协议人工复核",
            "overview": {"text": "准备材料再验证。", "evidence_keys": ["E001"]},
            "sections": [
                {"kind": "steps", "items": [{"text": "准备3个材料。", "evidence_keys": ["E001"]}]}
            ],
            "personal_notes": "分类仅表示人工整理。",
        }
    )
    edits.save_edit(identity(owner), UUID(owner["tenant_id"]), UUID(note["note_id"]), body)
    assert live.get(path + "/labels", headers=auth(owner)).json()["data"] == results[0]
    regenerated = protocol_revision(
        owner, note["snapshot_id"], note["source_text"], "分类协议新版本"
    )
    latest = live.get(path, headers=auth(owner)).json()["data"]["revision"]
    assert latest["id"] == regenerated["revision_id"]
    assert latest["version"] == original["version"] + 2
    assert live.get(path + "/labels", headers=auth(owner)).json()["data"] == results[0]
    assert set_label(owner, note, label, active=False) == []
    assert set_label(owner, note, label, active=False) == []
    assert set_label(owner, note, label) == results[0]
    with admin.connect() as connection:
        params = {"tenant": owner["tenant_id"], "label": label["id"], "note": note["note_id"]}
        association = connection.execute(
            text(
                "SELECT id,active,created_by,updated_by FROM note_labels "
                "WHERE tenant_id=:tenant AND note_id=:note AND label_id=:label"
            ),
            params,
        ).one()
        assert association.active
        assert str(association.created_by) == str(association.updated_by) == owner["user_id"]
        creation_count = connection.execute(
            text(
                "SELECT count(*) FROM audit_events WHERE tenant_id=:tenant "
                "AND action='organization.label_created' AND resource_id=:label"
            ),
            params,
        ).scalar_one()
        assert creation_count == 1
        events = (
            connection.execute(
                text(
                    "SELECT action FROM audit_events WHERE tenant_id=:tenant "
                    "AND resource_id=:id AND action LIKE 'organization.%'"
                ),
                {"tenant": owner["tenant_id"], "id": association.id},
            )
            .scalars()
            .all()
        )
        assert events.count("organization.label_attached") == 2
        assert events.count("organization.label_removed") == 1
    old = live.get(path + "/revisions/" + original["id"], headers=auth(owner)).json()["data"]
    assert old == original


def test_real_active_member_classification_actor_and_revocation(organization_accounts):
    live, admin, (owner, other) = organization_accounts
    space = endpoint(owner)
    response = live.post(
        space + "/members",
        headers=auth(owner),
        json={"user_id": other["user_id"], "role": "member"},
    )
    assert response.status_code == 201
    member = {**other, "tenant_id": owner["tenant_id"]}
    note = owner["notes"][1]
    try:
        label = create(member, "成员创建分类", "topic")
        path = space + f"/notes/{note['note_id']}/labels/{label['id']}"
        applied = live.put(path, headers=auth(other))
        assert applied.status_code == 200 and applied.json()["data"] == [label]
        with admin.connect() as connection:
            actor = connection.execute(
                text(
                    "SELECT updated_by FROM note_labels WHERE tenant_id=:tenant "
                    "AND note_id=:note AND label_id=:label"
                ),
                {"tenant": owner["tenant_id"], "note": note["note_id"], "label": label["id"]},
            ).scalar_one()
            assert str(actor) == other["user_id"]
        assert live.delete(path, headers=auth(other)).status_code == 200
        assert (
            live.delete(space + f"/members/{other['user_id']}", headers=auth(owner)).status_code
            == 200
        )
        assert live.get(space + "/labels", headers=auth(other)).status_code == 404
        assert live.put(path, headers=auth(other)).status_code == 404
        with pytest.raises(HTTPException) as denied:
            create(member, "revoked write")
        assert denied.value.status_code == 404
        with pytest.raises(HTTPException) as denied_update:
            service.update_label(
                identity(member),
                UUID(owner["tenant_id"]),
                UUID(label["id"]),
                LabelUpdateInput(active=False, expected_version=1),
            )
        assert denied_update.value.status_code == 404
        with pytest.raises(HTTPException) as denied:
            set_label(member, note, label)
        assert denied.value.status_code == 404
    finally:
        # Revocation is idempotent; only the membership created above is touched.
        live.delete(space + f"/members/{other['user_id']}", headers=auth(owner))


def test_real_classification_rls_actor_grants_and_tenant_composite_fks(organization_accounts):
    _live, admin, (owner, other) = organization_accounts
    label, foreign = create(owner, "RLS test"), create(other, "RLS foreign")
    note = owner["notes"][1]
    set_label(owner, note, label)
    for table in ("knowledge_labels", "note_labels"):
        with get_engine().begin() as connection:
            assert connection.execute(text("SELECT current_user")).scalar_one() == "knowledge_app"
            assert connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
            context(connection, owner)
            assert (
                connection.execute(
                    text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                    {"tenant": other["tenant_id"]},
                ).scalar_one()
                == 0
            )
        with admin.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                    "WHERE relname=:name"
                ),
                {"name": table},
            ).scalar_one()
    params = {
        "id": uuid4(),
        "tenant": owner["tenant_id"],
        "note": note["note_id"],
        "label": label["id"],
        "user": owner["user_id"],
        "other": other["user_id"],
    }
    for statement in (
        "UPDATE knowledge_labels SET name='tampered' WHERE tenant_id=:tenant",
        "DELETE FROM knowledge_labels WHERE tenant_id=:tenant",
        "UPDATE note_labels SET note_id=note_id WHERE tenant_id=:tenant",
        "UPDATE note_labels SET created_by=created_by WHERE tenant_id=:tenant",
        "DELETE FROM note_labels WHERE tenant_id=:tenant",
        "UPDATE note_labels SET updated_by=:other,active=false WHERE tenant_id=:tenant",
        "INSERT INTO knowledge_labels(id,tenant_id,kind,name,name_key,created_by) "
        "VALUES(:id,:tenant,'tag','spoofed','spoofed',:other)",
        "INSERT INTO note_labels(id,tenant_id,note_id,label_id,created_by,updated_by,active) "
        "VALUES(:id,:tenant,:note,:label,:other,:other,true)",
    ):
        with pytest.raises(DBAPIError) as denied, get_engine().begin() as connection:
            context(connection, owner)
            connection.execute(text(statement), params)
        assert denied.value.orig.sqlstate == "42501"
    unused_label = create(owner, "foreign member FK")
    for changes in (
        {"label": foreign["id"]},
        {"note": other["notes"][0]["note_id"]},
        {"user": str(uuid4()), "label": unused_label["id"]},
    ):
        with pytest.raises(DBAPIError) as denied, admin.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO note_labels "
                    "(id,tenant_id,note_id,label_id,created_by,updated_by,active) "
                    "VALUES(:id,:tenant,:note,:label,:user,:user,true)"
                ),
                {**params, "id": uuid4(), **changes},
            )
        assert denied.value.orig.sqlstate == "23503"


def test_real_label_filter_composes_without_duplicates_and_validates_cursor(organization_accounts):
    live, _admin, (owner, other) = organization_accounts
    label, extra = create(owner, "组合分页主题", "topic"), create(owner, "额外标签")
    for note in owner["notes"]:
        set_label(owner, note, label)
        set_label(owner, note, extra)
    path = endpoint(owner) + "/notes"
    filters = {"label_id": label["id"], "platform": "web", "q": "分类协议", "limit": 1}
    first = live.get(path, headers=auth(owner), params=filters)
    assert first.status_code == 200
    page = first.json()
    assert len(page["data"]) == 1 and page["next_cursor"]
    second = live.get(path, headers=auth(owner), params={**filters, "cursor": page["next_cursor"]})
    assert second.status_code == 200
    combined = page["data"] + second.json()["data"]
    assert len(combined) == 2 and len({row["id"] for row in combined}) == 2
    assert second.json()["next_cursor"] is None
    assert (
        live.get(path, headers=auth(owner), params={**filters, "origin": "user_edit"}).json()[
            "data"
        ]
        == []
    )
    assert live.get(path, headers=auth(owner), params={"label_id": "bad"}).status_code == 422
    foreign = create(other, "组合分页主题", "topic")
    assert (
        live.get(path, headers=auth(owner), params={"label_id": foreign["id"]}).json()["data"] == []
    )
    anchor = next(note for note in owner["notes"] if note["note_id"] == page["next_cursor"])
    set_label(owner, anchor, label, active=False)
    missing = live.get(path, headers=auth(owner), params={**filters, "cursor": page["next_cursor"]})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOTE_CURSOR_NOT_FOUND"
    set_label(owner, anchor, label)


def test_real_classification_audit_failure_rolls_back_business_state(
    organization_accounts, monkeypatch
):
    _live, admin, (owner, _other) = organization_accounts
    label = create(owner, "atomic classification")
    note = owner["notes"][1]

    def fail_audit(*_args, **_kwargs):
        raise RuntimeError("controlled audit failure")

    with monkeypatch.context() as patched:
        patched.setattr(service, "record", fail_audit)
        with pytest.raises(RuntimeError, match="controlled audit failure"):
            create(owner, "must roll back")
        with pytest.raises(RuntimeError, match="controlled audit failure"):
            set_label(owner, note, label)
    with admin.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM knowledge_labels "
                    "WHERE tenant_id=:tenant AND name='must roll back'"
                ),
                {"tenant": owner["tenant_id"]},
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM note_labels WHERE tenant_id=:tenant "
                    "AND note_id=:note AND label_id=:label"
                ),
                {"tenant": owner["tenant_id"], "note": note["note_id"], "label": label["id"]},
            ).scalar_one()
            == 0
        )
    set_label(owner, note, label)
    with monkeypatch.context() as patched:
        patched.setattr(service, "record", fail_audit)
        with pytest.raises(RuntimeError, match="controlled audit failure"):
            set_label(owner, note, label, active=False)
    with admin.connect() as connection:
        assert connection.execute(
            text(
                "SELECT active FROM note_labels WHERE tenant_id=:tenant "
                "AND note_id=:note AND label_id=:label"
            ),
            {"tenant": owner["tenant_id"], "note": note["note_id"], "label": label["id"]},
        ).scalar_one()


@pytest.mark.parametrize("operation", ["create", "attach", "update", "parent"])
def test_real_label_write_rechecks_logout_after_waiting_for_space_lock(
    organization_accounts, operation
):
    live, admin, (owner, _other) = organization_accounts
    # Obtain a separate real login so revoking it preserves the fixture's owner
    # session. No session key is logged or passed into the test report.
    email = live.get("/api/v1/auth/me", headers=auth(owner)).json()["data"]["email"]
    login = live.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "organization-fixture-only-2026"},
    )
    assert login.status_code == 200
    suspended = {**owner, "access_token": login.json()["data"]["access_token"]}
    marker = f"logout while waiting {uuid4()}"
    label = (
        create(owner, marker, "topic" if operation == "parent" else "tag")
        if operation != "create"
        else None
    )
    parent = create(owner, "注销等待上级", "topic") if operation == "parent" else None
    note = owner["notes"][1]
    with ThreadPoolExecutor(max_workers=1) as workers:
        with admin.connect() as blocker:
            transaction = blocker.begin()
            try:
                blocker.execute(
                    text("SELECT id FROM tenants WHERE id=:tenant FOR NO KEY UPDATE"),
                    {"tenant": owner["tenant_id"]},
                )
                blocker_pid = blocker.execute(text("SELECT pg_backend_pid()")).scalar_one()
                pending = (
                    workers.submit(create, suspended, marker)
                    if label is None
                    else workers.submit(
                        service.update_label,
                        identity(suspended),
                        UUID(owner["tenant_id"]),
                        UUID(label["id"]),
                        LabelUpdateInput.model_validate(
                            {
                                "expected_version": 1,
                                **(
                                    {"parent_id": parent["id"]}
                                    if parent
                                    else {"name": "注销应拒绝"}
                                ),
                            }
                        ),
                    )
                    if operation in {"update", "parent"}
                    else workers.submit(set_label, suspended, note, label)
                )
                deadline = time.monotonic() + 10
                blocked = False
                with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
                    while time.monotonic() < deadline:
                        blocked = bool(
                            observer.execute(
                                text(
                                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                                    "WHERE usename='knowledge_app' "
                                    "AND :pid=ANY(pg_blocking_pids(pid)))"
                                ),
                                {"pid": blocker_pid},
                            ).scalar_one()
                        )
                        if blocked:
                            break
                        if pending.done():
                            pending.result()
                            pytest.fail("Classification writer did not wait for the tenant lock")
                        time.sleep(0.05)
                assert blocked, "Writer did not reach the real PostgreSQL tenant lock"
                assert live.post("/api/v1/auth/logout", headers=auth(suspended)).status_code == 200
            finally:
                # Always release before ThreadPoolExecutor waits for its worker.
                transaction.rollback()
        with pytest.raises(HTTPException) as denied:
            pending.result(timeout=10)
        assert denied.value.status_code == 401
        assert denied.value.detail["code"] == "UNAUTHORIZED"
    with admin.connect() as connection:
        if label is None:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM knowledge_labels "
                        "WHERE tenant_id=:tenant AND name=:name"
                    ),
                    {"tenant": owner["tenant_id"], "name": marker},
                ).scalar_one()
                == 0
            )
        else:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM note_labels "
                        "WHERE tenant_id=:tenant AND label_id=:label"
                    ),
                    {"tenant": owner["tenant_id"], "label": label["id"]},
                ).scalar_one()
                == 0
            )


def test_label_management_http_archive_restore_and_association_history(organization_accounts):
    live, admin, (owner, other) = organization_accounts
    label = create(owner, "维护原名称")
    note = owner["notes"][0]
    set_label(owner, note, label)
    root = endpoint(owner) + "/labels"
    path = root + "/" + label["id"]
    assert live.get(root + "/manage").status_code == 401
    assert live.get(root + "/manage", headers=auth(other)).status_code == 404
    assert live.patch(path, json={"name": "拒绝", "expected_version": 1}).status_code == 401
    assert (
        live.patch(
            path, headers=auth(other), json={"active": False, "expected_version": 1}
        ).status_code
        == 404
    )
    response = live.patch(
        path,
        headers=auth(owner),
        json={"name": "维护新名称", "active": False, "expected_version": 1},
    )
    assert response.status_code == 200
    updated = response.json()["data"]
    assert updated["version"] == 2 and updated["name"] == "维护新名称" and not updated["active"]
    assert any(
        row["id"] == label["id"] and not row["active"]
        for row in live.get(root + "/manage", headers=auth(owner)).json()["data"]
    )
    linked = live.get(
        endpoint(owner) + f"/notes/{note['note_id']}/labels", headers=auth(owner)
    ).json()["data"]
    assert any(row["id"] == label["id"] and row["name"] == "维护新名称" for row in linked)
    assert live.get(
        endpoint(owner) + "/notes", headers=auth(owner), params={"label_id": label["id"]}
    ).json()["data"]
    with pytest.raises(HTTPException) as archived:
        set_label(owner, owner["notes"][1], label)
    assert archived.value.detail["code"] == "LABEL_ARCHIVED"
    with pytest.raises(HTTPException) as archived_create:
        create(owner, "维护新名称")
    assert archived_create.value.detail["code"] == "LABEL_ARCHIVED"
    restored = live.patch(path, headers=auth(owner), json={"active": True, "expected_version": 2})
    assert restored.status_code == 200 and restored.json()["data"]["version"] == 3
    set_label(owner, owner["notes"][1], label)
    with admin.connect() as connection:
        assert connection.execute(
            text("SELECT updated_by FROM knowledge_labels WHERE id=:id"), {"id": label["id"]}
        ).scalar_one() == UUID(owner["user_id"])


def test_label_management_concurrent_conflict_noop_and_atomic_audit(
    organization_accounts, monkeypatch
):
    _live, admin, (owner, other) = organization_accounts
    label = create(owner, "维护竞争")
    tenant = UUID(owner["tenant_id"])

    def change(name, version=1):
        return service.update_label(
            identity(owner),
            tenant,
            UUID(label["id"]),
            LabelUpdateInput(name=name, expected_version=version),
        )

    def race(index):
        try:
            return change(f"维护竞争{index}")["version"]
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(race, range(2))) == [2, 409]
    with admin.connect() as connection:
        current = connection.execute(
            text("SELECT name FROM knowledge_labels WHERE id=:id"), {"id": label["id"]}
        ).scalar_one()
        count = connection.execute(
            text(
                "SELECT count(*) FROM audit_events WHERE resource_id=:id "
                "AND action='organization.label_updated'"
            ),
            {"id": label["id"]},
        ).scalar_one()
    assert change(current, 2)["version"] == 2
    duplicate = create(owner, "维护重名")
    with pytest.raises(HTTPException) as conflict:
        change(duplicate["name"], 2)
    assert conflict.value.detail["code"] == "LABEL_NAME_CONFLICT"
    with pytest.raises(HTTPException) as foreign:
        service.update_label(
            identity(other),
            UUID(other["tenant_id"]),
            UUID(label["id"]),
            LabelUpdateInput(active=False, expected_version=2),
        )
    assert foreign.value.status_code == 404

    def fail(*_args, **_kwargs):
        raise RuntimeError("controlled directory audit failure")

    with monkeypatch.context() as patched:
        patched.setattr(service, "record", fail)
        with pytest.raises(RuntimeError, match="controlled directory audit failure"):
            change("必须回滚", 2)
    with admin.connect() as connection:
        assert connection.execute(
            text("SELECT name,version FROM knowledge_labels WHERE id=:id"), {"id": label["id"]}
        ).one() == (current, 2)
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM audit_events WHERE resource_id=:id "
                    "AND action='organization.label_updated'"
                ),
                {"id": label["id"]},
            ).scalar_one()
            == count
        )


def test_real_classification_quotas_allow_existing_state_and_released_capacity(
    organization_accounts,
):
    _live, admin, (_owner, other) = organization_accounts
    note = other["notes"][0]
    labels = [create(other, f"quota {index}") for index in range(21)]
    for label in labels[:20]:
        set_label(other, note, label)
    assert len(set_label(other, note, labels[0])) == 20
    with pytest.raises(HTTPException) as denied:
        set_label(other, note, labels[20])
    assert denied.value.status_code == 429
    assert denied.value.detail["code"] == "NOTE_LABEL_QUOTA_EXCEEDED"
    assert len(set_label(other, note, labels[0], active=False)) == 19
    assert len(set_label(other, note, labels[20])) == 20
    with admin.connect() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM knowledge_labels WHERE tenant_id=:tenant"),
            {"tenant": other["tenant_id"]},
        ).scalar_one()
    for index in range(100 - count):
        create(other, f"space quota {index}")
    assert create(other, labels[0]["name"]) == labels[0]
    with pytest.raises(HTTPException) as denied:
        create(other, "quota exceeded new label")
    assert denied.value.status_code == 429
    assert denied.value.detail["code"] == "LABEL_QUOTA_EXCEEDED"
    with admin.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM knowledge_labels WHERE tenant_id=:tenant"),
                {"tenant": other["tenant_id"]},
            ).scalar_one()
            == 100
        )
