"""Real knowledge DB/API boundaries; no provider network or paid inference."""

import json
import os
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
from app.modules.notes import edits, service
from app.modules.notes.edit_content import NoteEditInput
from app.modules.sources.models import SourceSnapshot
from app.modules.sources.service import digest, save_batch
from fastapi import HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]


@pytest.fixture(scope="module")
def knowledge_accounts():
    settings = get_settings()
    assert settings.app_env == "test" and settings.migration_database_url
    admin = create_engine(settings.migration_database_url.get_secret_value(), hide_parameters=True)
    accounts = []
    with httpx.Client(base_url=os.environ["API_BASE_URL"], trust_env=False, timeout=30) as live:
        try:
            for _ in range(2):
                response = live.post(
                    "/api/v1/auth/register",
                    json={
                        "email": f"{uuid4()}@example.invalid",
                        "password": "knowledge-fixture-only-2026",
                    },
                )
                assert response.status_code == 201
                account = response.json()["data"]
                accounts.append(account)
                with Session(get_engine()) as session, session.begin():
                    actor = authenticate_session(session, account["access_token"])
                    access = member_access(UUID(account["tenant_id"]), actor)
                    batch = save_batch(access, f"https://example.com/note/{uuid4()}", uuid4())
                    source_id = UUID(batch["sources"][0]["id"])
                    job = ProcessingJob(
                        tenant_id=access.tenant.id,
                        requested_by=actor.user.id,
                        kind="source_collect",
                        status="running",
                        idempotency_key=str(uuid4()),
                        payload={"source_id": str(source_id)},
                    )
                    session.add(job)
                    session.flush()
                    snapshot = SourceSnapshot(
                        tenant_id=access.tenant.id,
                        source_id=source_id,
                        job_id=job.id,
                        created_by=actor.user.id,
                        version=1,
                        fetched_url="https://example.com/",
                        platform="web",
                        content_kind="article",
                        title="真实数据库测试快照",
                        author="",
                        body_text="先准备3个材料，再验证输出。",
                        raw_sha256=digest("fixture"),
                        details={},
                        limitations=["仅用于数据库测试，不是真实平台采集证据"],
                    )
                    session.add(snapshot)
                    session.flush()
                    account["snapshot_id"], account["source_id"] = str(snapshot.id), str(source_id)
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
                        "summary_checkpoints",
                        "semantic_chunks",
                        "source_schedule_jobs",
                        "source_schedule_ticks",
                        "source_schedules",
                        "research_citations",
                        "research_runs",
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


def identity(account):
    return ActorIdentity(UUID(account["user_id"]), token_digest(account["access_token"]))


def context(connection, account):
    connection.execute(
        text(
            "SELECT set_config('app.tenant_id',:tenant,true),set_config('app.user_id',:user,true)"
        ),
        {"tenant": account["tenant_id"], "user": account["user_id"]},
    )


def start(account, key=None, snapshot=None):
    return service.begin_run(
        identity(account),
        UUID(account["tenant_id"]),
        UUID(snapshot or account["snapshot_id"]),
        key or uuid4(),
    )


def test_real_summary_idempotency_unknown_and_cross_tenant(knowledge_accounts):
    _live, _admin, (owner, other) = knowledge_accounts
    key = uuid4()
    run_id, fresh = start(owner, key)
    assert fresh and start(owner, key) == (run_id, False)
    with pytest.raises(HTTPException) as error:
        start(owner)
    assert error.value.detail["code"] == "SUMMARY_OUTCOME_UNRESOLVED"
    closed = service.close_run(
        identity(owner), UUID(owner["tenant_id"]), run_id, "unknown", "MODEL_OUTCOME_UNKNOWN"
    )
    assert closed["status"] == "unknown"
    assert start(owner, key) == (run_id, False)
    with pytest.raises(HTTPException) as error:
        start(owner)
    assert error.value.detail["code"] == "SUMMARY_OUTCOME_UNRESOLVED"
    with pytest.raises(HTTPException) as error:
        start(other, snapshot=owner["snapshot_id"])
    assert error.value.status_code == 404


def test_real_note_run_input_binding_and_terminal_state_are_immutable(knowledge_accounts):
    _live, _admin, (_owner, other) = knowledge_accounts
    run_id, fresh = start(other)
    assert fresh
    for column, value in (
        ("snapshot_id", uuid4()),
        ("idempotency_key", uuid4()),
        ("created_by", uuid4()),
    ):
        with pytest.raises(DBAPIError) as error, get_engine().begin() as connection:
            context(connection, other)
            connection.execute(
                text(f"UPDATE note_runs SET {column}=:value WHERE id=:id"),
                {"value": value, "id": run_id},
            )
        assert error.value.orig.sqlstate == "42501"
    service.close_run(identity(other), UUID(other["tenant_id"]), run_id, "failed", "TEST_NO_MODEL")
    with get_engine().begin() as connection:
        context(connection, other)
        result = connection.execute(
            text("UPDATE note_runs SET status='pending' WHERE id=:id"), {"id": run_id}
        )
        assert result.rowcount == 0
    with pytest.raises(DBAPIError) as error, get_engine().begin() as connection:
        context(connection, other)
        connection.execute(text("DELETE FROM note_runs WHERE id=:id"), {"id": run_id})
    assert error.value.orig.sqlstate == "42501"


def test_real_note_document_sections_evidence_and_api_are_consistent(knowledge_accounts):
    live, admin, (_owner, other) = knowledge_accounts
    run_id, _fresh = start(other)
    _title, source_text = service.load_input(identity(other), UUID(other["tenant_id"]), run_id)
    # This fixed protocol document exercises validation/persistence only. It is
    # never counted as a generated model answer or platform acceptance.
    raw = json.dumps(
        {
            "title": "材料验证",
            "overview": {"text": "先准备材料再验证。", "evidence_keys": ["E001"]},
            "sections": [
                {"kind": "steps", "items": [{"text": "准备3个材料。", "evidence_keys": ["E001"]}]}
            ],
            "limitations": ["协议测试，无模型调用"],
        },
        ensure_ascii=False,
    )
    result = service.save_document(
        identity(other), UUID(other["tenant_id"]), run_id, raw, source_text
    )
    assert result["status"] == "succeeded"
    path = f"/api/v1/spaces/{other['tenant_id']}/notes/{result['note_id']}"
    auth = {"Authorization": "Bearer " + other["access_token"]}
    response = live.get(path, headers=auth)
    assert response.status_code == 200
    revision = response.json()["data"]["revision"]
    assert revision["document"]["sections"][0]["items"][0]["text"] in revision["content_md"]
    with admin.connect() as connection:
        sections = connection.execute(
            text("SELECT kind,items FROM note_sections WHERE revision_id=:id"),
            {"id": result["revision_id"]},
        ).all()
        assert {row.kind for row in sections} == {"overview", "steps"}
        quote = connection.execute(
            text("SELECT quote FROM evidence_items WHERE revision_id=:id"),
            {"id": result["revision_id"]},
        ).scalar_one()
        stored_text = connection.execute(
            text("SELECT body_text FROM source_snapshots WHERE id=:id"),
            {"id": other["snapshot_id"]},
        ).scalar_one()
        assert quote in stored_text and "### E001" in revision["content_md"]
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM audit_events WHERE tenant_id=:tenant "
                    "AND action='notes.generated' AND resource_id=:id"
                ),
                {"tenant": other["tenant_id"], "id": result["revision_id"]},
            ).scalar_one()
            == 1
        )
    owner = knowledge_accounts[2][0]
    assert (
        live.get(path, headers={"Authorization": "Bearer " + owner["access_token"]}).status_code
        == 404
    )
    assert live.get(path).status_code == 401
    # A valid revision/snapshot pair still cannot store an invented quotation.
    with pytest.raises(DBAPIError) as error, get_engine().begin() as connection:
        context(connection, other)
        connection.execute(
            text(
                "INSERT INTO evidence_items "
                "(id,tenant_id,revision_id,snapshot_id,evidence_key,quote) "
                "VALUES (:id,:tenant,:revision,:snapshot,'E099','invented-quotation')"
            ),
            {
                "id": uuid4(),
                "tenant": other["tenant_id"],
                "revision": result["revision_id"],
                "snapshot": other["snapshot_id"],
            },
        )
    assert error.value.orig.sqlstate == "42501"


def test_real_knowledge_tables_rls_append_only_and_composite_fk(knowledge_accounts):
    _live, admin, (owner, other) = knowledge_accounts
    for table in ("note_runs", "notes", "note_revisions", "note_sections", "evidence_items"):
        with get_engine().begin() as connection:
            assert connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
            context(connection, owner)
            assert (
                connection.execute(
                    text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                    {"tenant": other["tenant_id"]},
                ).scalar_one()
                == 0
            )
        if table == "note_runs":
            continue
        for operation in (f"UPDATE {table} SET id=id", f"DELETE FROM {table}"):
            with pytest.raises(DBAPIError) as error, get_engine().begin() as connection:
                context(connection, owner)
                connection.execute(text(operation))
            assert error.value.orig.sqlstate == "42501"
    with pytest.raises(DBAPIError) as error, admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO note_runs "
                "(id,tenant_id,snapshot_id,created_by,idempotency_key,status) "
                "VALUES (:id,:tenant,:snapshot,:user,:key,'pending')"
            ),
            {
                "id": uuid4(),
                "tenant": owner["tenant_id"],
                "snapshot": other["snapshot_id"],
                "user": owner["user_id"],
                "key": uuid4(),
            },
        )
    assert error.value.orig.sqlstate == "23503"
    with pytest.raises(DBAPIError) as error, get_engine().begin() as connection:
        context(connection, owner)
        connection.execute(
            text(
                "INSERT INTO note_runs "
                "(id,tenant_id,snapshot_id,created_by,idempotency_key,status) "
                "VALUES (:id,:tenant,:snapshot,:user,:key,'pending')"
            ),
            {
                "id": uuid4(),
                "tenant": owner["tenant_id"],
                "snapshot": owner["snapshot_id"],
                "user": other["user_id"],
                "key": uuid4(),
            },
        )
    assert error.value.orig.sqlstate == "42501"


def test_live_summary_requires_consent_and_real_provider_configuration(knowledge_accounts):
    live, _admin, (_owner, other) = knowledge_accounts
    path = f"/api/v1/spaces/{other['tenant_id']}/snapshots/{other['snapshot_id']}/summarize"
    auth = {"Authorization": "Bearer " + other["access_token"]}
    body = {"idempotency_key": str(uuid4()), "consent": False}
    assert live.post(path, headers=auth, json=body).status_code == 400
    body["consent"] = True
    response = live.post(path, headers=auth, json=body)
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["status"] == "failed" and result["note_id"] is None
    replay = live.post(path, headers=auth, json=body)
    assert replay.status_code == 200 and replay.json()["data"] == result
    run = live.get(
        f"/api/v1/spaces/{other['tenant_id']}/note-runs/{result['run_id']}", headers=auth
    )
    assert run.status_code == 200 and run.json()["data"] == result


def test_real_metadata_revision_persists_server_scope_in_both_formats(knowledge_accounts):
    live, _admin, (_owner, other) = knowledge_accounts
    tenant_id = UUID(other["tenant_id"])
    with service.authorized_transaction(identity(other), tenant_id) as (session, _role):
        job = ProcessingJob(
            tenant_id=tenant_id,
            requested_by=UUID(other["user_id"]),
            kind="source_collect",
            status="running",
            idempotency_key=str(uuid4()),
            payload={"source_id": other["source_id"]},
        )
        session.add(job)
        session.flush()
        snapshot = SourceSnapshot(
            tenant_id=tenant_id,
            source_id=UUID(other["source_id"]),
            job_id=job.id,
            created_by=UUID(other["user_id"]),
            version=2,
            fetched_url="https://example.com/metadata-protocol",
            platform="bilibili",
            content_kind="metadata",
            title="协议元数据测试，不代表平台采集",
            author="协议作者",
            body_text="标题：协议元数据测试\n作者：协议作者",
            raw_sha256=digest("metadata-protocol"),
            details={},
            limitations=["METADATA_ONLY", "NO_TRANSCRIPT_COLLECTED"],
        )
        session.add(snapshot)
        session.flush()
        snapshot_id = snapshot.id
    run_id, fresh = service.begin_run(identity(other), tenant_id, snapshot_id, uuid4())
    assert fresh
    _title, source = service.load_input(identity(other), tenant_id, run_id)
    raw = json.dumps(
        {
            "title": "协议元数据",
            "overview": {"text": "标题与作者已提供。", "evidence_keys": ["E001"]},
            "sections": [
                {
                    "kind": "core_claims",
                    "items": [{"text": "作者为协议作者。", "evidence_keys": ["E001"]}],
                }
            ],
            "limitations": [],
        },
        ensure_ascii=False,
    )
    result = service.save_document(identity(other), tenant_id, run_id, raw, source)
    response = live.get(
        f"/api/v1/spaces/{tenant_id}/notes/{result['note_id']}",
        headers={"Authorization": "Bearer " + other["access_token"]},
    )
    assert response.status_code == 200
    revision = response.json()["data"]["revision"]
    assert revision["title"].startswith("元数据笔记：")
    assert "不是视频内容总结" in revision["document"]["limitations"][0]
    assert "不是视频内容总结" in revision["content_md"]


def test_real_imported_revision_persists_server_provenance_scope_without_model(knowledge_accounts):
    live, _admin, (_owner, other) = knowledge_accounts
    tenant_id = UUID(other["tenant_id"])
    with service.authorized_transaction(identity(other), tenant_id) as (session, _role):
        actor = authenticate_session(session, other["access_token"])
        access = member_access(tenant_id, actor)
        batch = save_batch(access, f"https://example.com/import-protocol/{uuid4()}", uuid4())
        source = batch["sources"][0]
        job = ProcessingJob(
            tenant_id=tenant_id,
            requested_by=UUID(other["user_id"]),
            kind="source_import",
            status="running",
            idempotency_key=str(uuid4()),
            payload={"source_id": source["id"]},
        )
        session.add(job)
        session.flush()
        snapshot = SourceSnapshot(
            tenant_id=tenant_id,
            source_id=UUID(source["id"]),
            job_id=job.id,
            created_by=UUID(other["user_id"]),
            version=1,
            provenance="user_import",
            fetched_url=source["canonical_url"],
            platform="user_import",
            content_kind="transcript",
            title="协议导入资料，不代表平台获取或真实模型输出",
            author="",
            body_text="[00:00:01.000 --> 00:00:03.000]\n用户提供协议资料。",
            raw_sha256=digest("import-protocol-fixture"),
            details={"fixture": "database-provenance-only"},
            limitations=["USER_PROVIDED_CONTENT", "SOURCE_CONTENT_NOT_VERIFIED"],
        )
        session.add(snapshot)
        session.flush()
        snapshot_id = snapshot.id
    run_id, fresh = service.begin_run(identity(other), tenant_id, snapshot_id, uuid4())
    assert fresh
    title, source_text = service.load_input(identity(other), tenant_id, run_id)
    assert title.startswith("用户提供的导入内容，未与关联平台核验：")
    raw = json.dumps(
        {
            "title": "协议资料笔记",
            "overview": {"text": "用户提供了一份协议资料。", "evidence_keys": ["E001"]},
            "sections": [
                {
                    "kind": "takeaways",
                    "items": [{"text": "资料尚待核验。", "evidence_keys": ["E001"]}],
                }
            ],
            "limitations": [],
        },
        ensure_ascii=False,
    )
    # A labelled protocol document exercises persistence without generating an
    # AI answer, configuring a provider, or submitting any paid model request.
    result = service.save_document(identity(other), tenant_id, run_id, raw, source_text)
    response = live.get(
        f"/api/v1/spaces/{tenant_id}/notes/{result['note_id']}",
        headers={"Authorization": "Bearer " + other["access_token"]},
    )
    assert response.status_code == 200
    revision = response.json()["data"]["revision"]
    assert revision["title"].startswith("用户导入笔记：")
    assert "未验证其与关联平台或视频一致" in revision["document"]["limitations"][0]
    assert "时间亦未对照音视频核验" in revision["content_md"]
    assert "用户提供协议资料" in revision["content_md"]


def edit_protocol_note(
    account, *, summary_title="材料验证协议笔记", source_url=None, long_mode=False
):
    """Persist labelled source/summary protocol data without any network inference."""
    tenant_id = UUID(account["tenant_id"])
    with service.authorized_transaction(identity(account), tenant_id) as (session, _role):
        actor = authenticate_session(session, account["access_token"])
        access = member_access(tenant_id, actor)
        source = save_batch(
            access, source_url or f"https://example.com/edit-protocol/{uuid4()}", uuid4()
        )["sources"][0]
        job = ProcessingJob(
            tenant_id=tenant_id,
            requested_by=UUID(account["user_id"]),
            kind="source_import",
            status="running",
            idempotency_key=str(uuid4()),
            payload={"source_id": source["id"]},
        )
        session.add(job)
        session.flush()
        snapshot = SourceSnapshot(
            tenant_id=tenant_id,
            source_id=UUID(source["id"]),
            job_id=job.id,
            created_by=UUID(account["user_id"]),
            version=1,
            provenance="user_import",
            fetched_url=source["canonical_url"],
            platform="user_import",
            content_kind="transcript",
            title="人工修订数据库协议输入，不代表远端采集或模型输出",
            author="",
            body_text=(
                "材料条件需要核对。" * 3000
                if long_mode
                else "[00:00:01.000 --> 00:00:03.000]\n先准备3个材料，再验证输出。"
            ),
            raw_sha256=digest("edit-protocol-fixture"),
            details={"fixture": "database-edit-only"},
            limitations=["USER_PROVIDED_CONTENT", "SOURCE_CONTENT_NOT_VERIFIED"],
        )
        session.add(snapshot)
        session.flush()
        snapshot_id, source_text = snapshot.id, snapshot.body_text
    run_id, fresh = service.begin_run(identity(account), tenant_id, snapshot_id, uuid4())
    assert fresh
    result = service.save_document(
        identity(account),
        tenant_id,
        run_id,
        json.dumps(
            {
                "title": summary_title,
                "overview": {"text": "先准备材料再验证。", "evidence_keys": ["E001"]},
                "sections": [
                    {
                        "kind": "steps",
                        "items": [
                            {
                                "text": "准备材料。" if long_mode else "准备3个材料。",
                                "evidence_keys": ["E001"],
                            }
                        ],
                    }
                ],
                "limitations": ["协议测试，不是模型推理结果"],
            },
            ensure_ascii=False,
        ),
        source_text,
        stages=long_stages(source_text) if long_mode else None,
    )
    return {
        **result,
        "snapshot_id": str(snapshot_id),
        "body": {
            "idempotency_key": str(uuid4()),
            "base_revision_id": result["revision_id"],
            "title": "人工复核后的材料笔记",
            "overview": {"text": "应先准备材料，再验证。", "evidence_keys": ["E001"]},
            "sections": [
                {
                    "kind": "steps",
                    "items": [{"text": "先准备3个材料。", "evidence_keys": ["E001"]}],
                }
            ],
            "personal_notes": "我准备复现实验；这是个人理解，尚待验证。",
        },
    }


def perform_edit(account, note, **changes):
    return edits.save_edit(
        identity(account),
        UUID(account["tenant_id"]),
        UUID(note["note_id"]),
        NoteEditInput.model_validate({**note["body"], **changes}),
    )


def test_real_manual_edit_preserves_history_evidence_and_non_ai_provenance(knowledge_accounts):
    live, admin, (owner, _other) = knowledge_accounts
    note = edit_protocol_note(owner)
    root = f"/api/v1/spaces/{owner['tenant_id']}/notes/{note['note_id']}"
    headers = {"Authorization": "Bearer " + owner["access_token"]}
    original = live.get(root, headers=headers).json()["data"]["revision"]
    response = live.post(root + "/revisions", headers=headers, json=note["body"])
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["version"] == 2
    assert (
        live.post(root + "/revisions", headers=headers, json=note["body"]).json()["data"] == result
    )
    detail = live.get(root, headers=headers).json()["data"]
    revision = detail["revision"]
    assert revision["origin"] == "user_edit"
    assert revision["base_revision_id"] == original["id"]
    assert revision["created_by"] == owner["user_id"]
    assert revision["document"]["editor"] == "user"
    assert revision["document"]["personal_notes"] == note["body"]["personal_notes"]
    assert "本次没有调用模型" in revision["content_md"]
    assert "本次向模型提供" not in revision["content_md"]
    assert "时间亦未对照音视频核验" in revision["content_md"]
    assert "个人理解未作来源或事实核验" in revision["content_md"]
    assert "协议测试，不是模型推理结果" in revision["content_md"]
    assert (
        live.get(root + f"/revisions/{original['id']}", headers=headers).json()["data"] == original
    )
    notes = live.get(f"/api/v1/spaces/{owner['tenant_id']}/notes", headers=headers).json()["data"]
    assert next(item for item in notes if item["id"] == note["note_id"])["origin"] == "user_edit"
    with admin.connect() as connection:
        row = connection.execute(
            text("SELECT run_id,document FROM note_revisions WHERE id=:id"),
            {"id": result["revision_id"]},
        ).one()
        assert row.run_id is None
        assert (
            connection.execute(
                text("SELECT count(*) FROM note_runs WHERE tenant_id=:tenant"),
                {"tenant": owner["tenant_id"]},
            ).scalar_one()
            >= 1
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM audit_events WHERE action='notes.edited' "
                    "AND resource_id=:id"
                ),
                {"id": result["revision_id"]},
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT quote FROM evidence_items WHERE revision_id=:id"),
                {"id": result["revision_id"]},
            ).scalar_one()
            == "[00:00:01.000 --> 00:00:03.000]\n先准备3个材料，再验证输出。"
        )


def test_real_manual_edit_serializes_duplicate_keys_and_stale_base(knowledge_accounts):
    _live, _admin, (owner, _other) = knowledge_accounts
    note = edit_protocol_note(owner)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: perform_edit(owner, note), range(3)))
    assert results[0] == results[1] == results[2]
    with pytest.raises(HTTPException) as error:
        perform_edit(owner, note, title="同键不同文本")
    assert error.value.detail["code"] == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(HTTPException) as error:
        perform_edit(owner, note, idempotency_key=str(uuid4()))
    assert error.value.detail["code"] == "NOTE_REVISION_CONFLICT"
    note["body"]["base_revision_id"] = results[0]["revision_id"]

    def compete(_):
        try:
            return perform_edit(owner, note, idempotency_key=str(uuid4()))
        except HTTPException as error:
            return error.detail["code"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, range(2)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert "NOTE_REVISION_CONFLICT" in results


def test_real_manual_edit_member_permissions_revocation_and_tenant_binding(knowledge_accounts):
    live, _admin, (owner, other) = knowledge_accounts
    note = edit_protocol_note(owner)
    root = f"/api/v1/spaces/{owner['tenant_id']}"
    path = root + f"/notes/{note['note_id']}/revisions"
    headers = {"Authorization": "Bearer " + owner["access_token"]}
    other_headers = {"Authorization": "Bearer " + other["access_token"]}
    assert live.post(path, json=note["body"]).status_code == 401
    assert live.post(path, headers=other_headers, json=note["body"]).status_code == 404
    assert (
        live.post(
            f"/api/v1/spaces/{other['tenant_id']}/notes/{note['note_id']}/revisions",
            headers=other_headers,
            json=note["body"],
        ).status_code
        == 404
    )
    response = live.post(
        root + "/members", headers=headers, json={"user_id": other["user_id"], "role": "member"}
    )
    assert response.status_code == 201
    try:
        response = live.post(path, headers=other_headers, json=note["body"])
        assert response.status_code == 200
        result = response.json()["data"]
        # Same tenant/key and payload cannot be replayed by a different actor.
        replay = live.post(path, headers=headers, json=note["body"])
        assert replay.status_code == 409
        assert replay.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        response = live.delete(root + f"/members/{other['user_id']}", headers=headers)
        assert response.status_code == 200
        assert live.post(path, headers=other_headers, json=note["body"]).status_code == 404
        with pytest.raises(HTTPException) as error:
            edits.save_edit(
                identity(other),
                UUID(owner["tenant_id"]),
                UUID(note["note_id"]),
                NoteEditInput.model_validate(
                    {**note["body"], "base_revision_id": result["revision_id"]}
                ),
            )
        assert error.value.status_code == 404
    finally:
        live.delete(root + f"/members/{other['user_id']}", headers=headers)


def test_real_manual_edit_db_rls_binding_and_history_grants(knowledge_accounts):
    _live, admin, (owner, other) = knowledge_accounts
    note = edit_protocol_note(owner)
    foreign_note = edit_protocol_note(other)
    result = perform_edit(owner, note)
    for operation in ("UPDATE note_revisions SET title=title", "DELETE FROM note_revisions"):
        with pytest.raises(DBAPIError) as error, get_engine().begin() as connection:
            context(connection, owner)
            connection.execute(text(operation + " WHERE id=:id"), {"id": result["revision_id"]})
        assert error.value.orig.sqlstate == "42501"
    for params in (
        {
            "actor": other["user_id"],
            "base": note["revision_id"],
            "origin": "user_edit",
            "run": None,
        },
        {
            "actor": owner["user_id"],
            "base": foreign_note["revision_id"],
            "origin": "user_edit",
            "run": None,
        },
        {"actor": owner["user_id"], "base": None, "origin": "ai_generated", "run": note["run_id"]},
    ):
        with pytest.raises(DBAPIError) as error, get_engine().begin() as connection:
            context(connection, owner)
            connection.execute(
                text(
                    "INSERT INTO note_revisions "
                    "(id,tenant_id,note_id,snapshot_id,run_id,created_by,version,title,content_md,document,"
                    "origin,base_revision_id,edit_key,request_sha256) "
                    "SELECT :new,tenant_id,note_id,snapshot_id,:run,:actor,version+1,"
                    "title,content_md,"
                    "document,:origin,:base,:key,:digest FROM note_revisions WHERE id=:id"
                ),
                {
                    **params,
                    "new": uuid4(),
                    "key": uuid4(),
                    "digest": "a" * 64,
                    "id": result["revision_id"],
                },
            )
        assert error.value.orig.sqlstate == "42501"
    # Elevated test-only insertion bypasses RLS to prove the composite FK itself.
    with pytest.raises(DBAPIError) as error, admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO note_revisions "
                "(id,tenant_id,note_id,snapshot_id,run_id,created_by,version,title,content_md,document,"
                "origin,base_revision_id,edit_key,request_sha256) "
                "SELECT :new,tenant_id,note_id,snapshot_id,NULL,created_by,version+1,"
                "title,content_md,"
                "document,'user_edit',:base,:key,:digest FROM note_revisions WHERE id=:id"
            ),
            {
                "new": uuid4(),
                "base": foreign_note["revision_id"],
                "key": uuid4(),
                "digest": "a" * 64,
                "id": result["revision_id"],
            },
        )
    assert error.value.orig.sqlstate == "23503"


def test_real_manual_edit_invalid_evidence_and_audit_failure_roll_back(
    knowledge_accounts, monkeypatch
):
    _live, admin, (owner, _other) = knowledge_accounts
    note = edit_protocol_note(owner)
    with pytest.raises(HTTPException) as error:
        perform_edit(owner, note, overview={"text": "伪造引用", "evidence_keys": ["E999"]})
    assert error.value.detail["code"] == "NOTE_EDIT_EVIDENCE_INVALID"

    def failed_audit(*args, **kwargs):
        raise RuntimeError("TEST_EDIT_AUDIT_ROLLBACK")

    with monkeypatch.context() as patch:
        patch.setattr(edits, "record", failed_audit)
        with pytest.raises(RuntimeError, match="TEST_EDIT_AUDIT_ROLLBACK"):
            perform_edit(owner, note)
    with admin.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM note_revisions WHERE note_id=:id"),
                {"id": note["note_id"]},
            ).scalar_one()
            == 1
        )
    # The rolled-back key remains available and produces exactly one real edit.
    assert perform_edit(owner, note)["version"] == 2


def test_real_note_filter_keyset_pages_latest_titles_and_bounded_queries(knowledge_accounts):
    from app.modules.notes.listing import NoteListQuery, list_page

    live, admin, (owner, other) = knowledge_accounts
    marker = "note-pages-" + uuid4().hex
    notes = [
        edit_protocol_note(
            owner,
            summary_title=f"{marker} old-{index}",
            source_url=f"https://www.bilibili.com/video/{uuid4()}" if index == 0 else None,
        )
        for index in range(7)
    ]
    # Fixture-only equal timestamps exercise the UUID tie-breaker. No production
    # row is touched and no model or platform request has generated this data.
    with admin.begin() as connection:
        for note in notes:
            connection.execute(
                text("UPDATE notes SET created_at='2026-01-01T00:00:00Z' WHERE id=:id"),
                {"id": note["note_id"]},
            )
    expected = sorted((note["note_id"] for note in notes), reverse=True)
    root = f"/api/v1/spaces/{owner['tenant_id']}/notes"
    headers = {"Authorization": "Bearer " + owner["access_token"]}
    params = {"q": "  " + marker.upper() + "  ", "limit": 2}
    received = []
    for page_number in range(4):
        response = live.get(root, headers=headers, params=params)
        assert response.status_code == 200
        result = response.json()
        received.extend(row["id"] for row in result["data"])
        assert len(result["data"]) == (1 if page_number == 3 else 2)
        if page_number < 3:
            assert result["next_cursor"] == result["data"][-1]["id"]
            params["cursor"] = result["next_cursor"]
        else:
            assert result["next_cursor"] is None
    assert received == expected
    platform = live.get(root, headers=headers, params={"q": marker, "platform": "bilibili"})
    assert [row["id"] for row in platform.json()["data"]] == [notes[0]["note_id"]]
    for path, auth in (
        (root, {}),
        (root, {"Authorization": "Bearer " + other["access_token"]}),
    ):
        assert live.get(path, headers=auth).status_code == (401 if not auth else 404)
    for path, auth, query in (
        (root, headers, {"q": "does-not-match", "cursor": expected[0]}),
        (root, headers, {"cursor": str(uuid4())}),
        (
            f"/api/v1/spaces/{other['tenant_id']}/notes",
            {"Authorization": "Bearer " + other["access_token"]},
            {"cursor": expected[0]},
        ),
    ):
        response = live.get(path, headers=auth, params=query)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOTE_CURSOR_NOT_FOUND"
    # Observe the real application connection after identity/member validation.
    # One page is one bounded SELECT; adding a cursor adds only its anchor SELECT.
    with service.authorized_transaction(identity(owner), UUID(owner["tenant_id"])) as (session, _):
        actor = authenticate_session(session, owner["access_token"])
        access = member_access(UUID(owner["tenant_id"]), actor)
        statements = []
        connection = session.connection()

        def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement)

        event.listen(connection, "before_cursor_execute", capture)
        try:
            page = list_page(access, NoteListQuery(q=marker, limit=3))
            assert len(page["data"]) == 3 and len(statements) == 1
            assert "LIMIT" in statements[0] and "content_md" not in statements[0].split("FROM")[0]
            statements.clear()
            page = list_page(access, NoteListQuery(q=marker, cursor=page["next_cursor"], limit=3))
            assert len(page["data"]) == 3 and len(statements) == 2
        finally:
            event.remove(connection, "before_cursor_execute", capture)


def test_real_note_filters_match_only_latest_revision_and_literal_body(knowledge_accounts):
    live, _admin, (owner, _other) = knowledge_accounts
    marker = "note-latest-" + uuid4().hex
    body_marker = "仅正文检索" + uuid4().hex
    note = edit_protocol_note(owner, summary_title=marker + " outdated-title")
    root = f"/api/v1/spaces/{owner['tenant_id']}/notes"
    headers = {"Authorization": "Bearer " + owner["access_token"]}
    edit = perform_edit(
        owner,
        note,
        title="当前人工版本 %_\\tail! " + marker,
        personal_notes=body_marker,
    )
    assert (
        live.get(root, headers=headers, params={"q": marker + " outdated-title"}).json()["data"]
        == []
    )
    result = live.get(
        root, headers=headers, params={"q": "%_\\tail!", "origin": "user_edit", "platform": "web"}
    ).json()["data"]
    assert len(result) == 1 and result[0]["id"] == note["note_id"]
    assert (
        result[0]["version"] == edit["version"]
        and result[0]["title"] == "用户导入笔记：当前人工版本 %_\\tail! " + marker
    )
    body_result = live.get(root, headers=headers, params={"q": body_marker}).json()["data"]
    assert [row["id"] for row in body_result] == [note["note_id"]]
    assert (
        live.get(root, headers=headers, params={"q": marker, "origin": "ai_generated"}).json()[
            "data"
        ]
        == []
    )
    response = live.get(
        root,
        headers=headers,
        params={"q": marker, "origin": "ai_generated", "cursor": note["note_id"]},
    )
    assert (
        response.status_code == 404 and response.json()["error"]["code"] == "NOTE_CURSOR_NOT_FOUND"
    )


def test_live_note_filter_bounds_and_enums(knowledge_accounts):
    live, _admin, (owner, _other) = knowledge_accounts
    root = f"/api/v1/spaces/{owner['tenant_id']}/notes"
    headers = {"Authorization": "Bearer " + owner["access_token"]}
    for params in (
        {"q": "x" * 121},
        {"q": "😀" * 121},
        {"limit": 0},
        {"limit": 51},
        {"limit": "NaN"},
        {"cursor": "invalid"},
        {"platform": "unknown"},
        {"origin": "all"},
    ):
        assert live.get(root, headers=headers, params=params).status_code == 422
    response = live.get(root, headers=headers, params={"q": "😀" * 120, "limit": 50})
    assert response.status_code == 200 and response.json()["data"] == []


def long_stages(source_text):
    from app.modules.notes.content import build_evidence
    from app.modules.notes.long_content import batches

    return [
        {
            "title": "协议分段",
            "overview": {"text": "材料条件需要核对。", "evidence_keys": [group[0].key]},
            "sections": [
                {
                    "kind": "steps",
                    "items": [{"text": "核对材料条件。", "evidence_keys": [group[-1].key]}],
                }
            ],
            "limitations": ["协议数据，不是模型实测"],
        }
        for group in batches(build_evidence(source_text))
    ]


def test_real_long_analysis_stage_quotes_export_and_human_revision(knowledge_accounts):
    live, _admin, (_owner, other) = knowledge_accounts
    note = edit_protocol_note(other, long_mode=True)
    headers = {"Authorization": "Bearer " + other["access_token"]}
    root = f"/api/v1/spaces/{other['tenant_id']}/notes/{note['note_id']}"
    result = live.get(
        root + f"/revisions/{note['revision_id']}/export?format=json", headers=headers
    )
    assert result.status_code == 200
    exported = result.json()
    document = exported["revision"]["document"]
    assert document["prompt_version"] == "evidence-summary-v2"
    assert document["coverage"]["partial"] is False
    assert len(document["analysis"]["stages"]) == 2
    assert "分段分析记录" in exported["revision"]["content_md"]
    assert len(exported["evidence"]) >= 3
    edited = perform_edit(other, note)
    response = live.get(root + f"/revisions/{edited['revision_id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["document"]["prompt_version"] == "evidence-summary-v2"
    assert response.json()["data"]["document"]["analysis"] is None
    assert (
        live.get(root + f"/revisions/{note['revision_id']}", headers=headers).json()["data"][
            "document"
        ]
        == document
    )


def test_real_checkpoint_claim_usage_atomicity_restart_and_unknown_block(
    knowledge_accounts, monkeypatch
):
    from app.integrations.ai import Generation
    from app.integrations.credentials import encrypt
    from app.modules.ai.models import AIProvider
    from app.modules.identity.access import authorized_transaction
    from app.modules.notes import checkpoints
    from app.modules.notes.models import SummaryCheckpoint
    from sqlalchemy.orm.exc import StaleDataError

    live, admin, (owner, other) = knowledge_accounts
    tenant, actor = UUID(owner["tenant_id"]), identity(owner)
    # Only transaction code is exercised; no provider request or real key.
    monkeypatch.setenv("DEEPSEEK_ENABLED", "true")
    monkeypatch.setenv(
        "CREDENTIAL_KEYS", '{"protocol":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}'
    )
    monkeypatch.setenv("CREDENTIAL_ACTIVE_KEY", "protocol")
    get_settings.cache_clear()
    provider = uuid4()
    version, encrypted = encrypt("protocol-checkpoint-only", tenant, provider)
    try:
        with authorized_transaction(actor, tenant) as (session, _):
            session.add(
                AIProvider(
                    id=provider,
                    tenant_id=tenant,
                    kind="deepseek",
                    model="deepseek-flash",
                    priority=10,
                    enabled=True,
                    consent=True,
                    key_version=version,
                    encrypted_key=encrypted,
                )
            )
        note = edit_protocol_note(owner, long_mode=True)
        with admin.connect() as conn:
            snapshot_id = conn.execute(
                text("SELECT snapshot_id FROM note_revisions WHERE id=:id"),
                {"id": note["revision_id"]},
            ).scalar_one()
        run, _ = service.begin_run(actor, tenant, snapshot_id, uuid4())

        # Real tenant serialization: concurrent executors cannot both reserve.
        def claim():
            try:
                return checkpoints.claim(actor, tenant, run, 0, "prompt", provider)[0]
            except HTTPException as error:
                assert error.detail == {"code": "SUMMARY_STAGE_ALREADY_CLAIMED"}
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: claim(), range(2)))
        assert sum(result is not None for result in results) == 1
        assert service.read_run(actor, tenant, run)["can_resume"] is False
        with pytest.raises(HTTPException):
            checkpoints.load(actor, tenant, run)
        payload = {
            "title": "协议",
            "overview": {"text": "核对材料。", "evidence_keys": ["E001"]},
            "sections": [
                {"kind": "steps", "items": [{"text": "验证输出。", "evidence_keys": ["E001"]}]}
            ],
            "limitations": ["不是模型推理。"],
        }
        checkpoints.complete(
            actor, tenant, run, 0, Generation(json.dumps(payload), 10, 5), payload, None
        )
        # New session/engine connection reads the persisted checkpoint.
        get_engine().dispose()
        recovered = checkpoints.load(actor, tenant, run)
        assert len(recovered) == 1 and recovered[0].output == payload
        assert service.read_run(actor, tenant, run)["can_resume"] is True
        # No cross-tenant or different requester access even with raw IDs.
        with pytest.raises(HTTPException):
            checkpoints.load(identity(other), tenant, run)
        with admin.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM ai_usage WHERE tenant_id=:t AND id=:u"),
                    {"t": tenant, "u": results[0] or results[1]},
                ).scalar_one()
                == 1
            )
        usage, _ = checkpoints.claim(
            actor, tenant, run, 1, "next prompt", provider, "deepseek-flash"
        )
        checkpoints.complete(actor, tenant, run, 1, None, None, "PROVIDER_OUTCOME_UNKNOWN", True)
        assert service.read_run(actor, tenant, run)["can_resume"] is False
        with pytest.raises(HTTPException):
            checkpoints.load(actor, tenant, run)
        with authorized_transaction(actor, tenant) as (session, _):
            row = session.get(SummaryCheckpoint, (tenant, run, 0))
            row.output = {"tampered": True}
            with pytest.raises((DBAPIError, StaleDataError)):
                session.flush()
            session.rollback()
        root = f"/api/v1/spaces/{tenant}/note-runs/{run}/resume"
        headers = {"Authorization": "Bearer " + owner["access_token"]}
        assert live.post(root, headers=headers, json={"consent": False}).status_code == 400
        assert live.post(root, headers=headers, json={"consent": True}).status_code == 409
        service.close_run(actor, tenant, run, "unknown", "MODEL_OUTCOME_UNKNOWN")
    finally:
        get_settings.cache_clear()


def test_real_manual_edit_daily_quota_counts_only_committed_edits_and_replay(knowledge_accounts):
    _live, admin, (owner, _other) = knowledge_accounts
    note = edit_protocol_note(owner)
    with admin.connect() as connection:
        count = connection.execute(
            text(
                "SELECT count(*) FROM note_revisions WHERE tenant_id=:tenant "
                "AND origin='user_edit' "
                "AND created_at >= date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"
            ),
            {"tenant": owner["tenant_id"]},
        ).scalar_one()
    result = None
    replay_body = None
    for _ in range(20 - count):
        note["body"]["idempotency_key"] = str(uuid4())
        replay_body = dict(note["body"])
        result = perform_edit(owner, note)
        note["body"]["base_revision_id"] = result["revision_id"]
    assert result is not None
    with pytest.raises(HTTPException) as error:
        perform_edit(owner, note, idempotency_key=str(uuid4()))
    assert error.value.detail["code"] == "NOTE_EDIT_DAILY_QUOTA"
    assert replay_body is not None
    assert (
        edits.save_edit(
            identity(owner),
            UUID(owner["tenant_id"]),
            UUID(note["note_id"]),
            NoteEditInput.model_validate(replay_body),
        )
        == result
    )
