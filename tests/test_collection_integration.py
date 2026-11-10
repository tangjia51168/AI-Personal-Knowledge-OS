"""Real snapshot/API authorization; public network collection is accepted separately.

Fixtures contain explicitly labelled text and create no queue dispatch for their
snapshot rows. They verify database policy, never stand in for collected content.
"""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from app.core.config import get_settings
from app.db.models import ProcessingJob
from app.db.session import get_engine
from app.main import app
from app.modules.identity.access import authenticate_session, member_access
from app.modules.jobs.execution import execute_dispatch
from app.modules.jobs.models import JobDispatch, JobStep
from app.modules.sources.collection import queue_collection
from app.modules.sources.models import SourceSnapshot
from app.modules.sources.service import save_batch
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]


def auth(account):
    return {"Authorization": "Bearer " + account["access_token"]}


def source_path(account):
    return f"/api/v1/spaces/{account['tenant_id']}/sources/{account['source_id']}"


def context(connection, account):
    connection.execute(
        text(
            "SELECT set_config('app.tenant_id',:tenant,true),set_config('app.user_id',:user,true)"
        ),
        {"tenant": account["tenant_id"], "user": account["user_id"]},
    )


def snapshot_values(account, job_id=None, **changes):
    return {
        "id": uuid4(),
        "tenant_id": UUID(account["tenant_id"]),
        "source_id": UUID(account["source_id"]),
        "job_id": job_id or UUID(account["snapshot_job_id"]),
        "created_by": UUID(account["user_id"]),
        "version": 1,
        "fetched_url": "https://example.com/",
        "platform": "web",
        "content_kind": "article",
        "title": "Database policy fixture, not fetched content",
        "author": "",
        "body_text": "Explicit database policy fixture. No public page was fetched.",
        "raw_sha256": "a" * 64,
        "details": {"fixture": "database-policy-only"},
        "limitations": ["Not a public collection acceptance result."],
        **changes,
    }


@pytest.fixture(scope="module")
def collection_accounts():
    settings = get_settings()
    assert settings.app_env == "test" and settings.migration_database_url
    admin = create_engine(
        settings.migration_database_url.get_secret_value(),
        hide_parameters=True,
        connect_args={"connect_timeout": 3},
    )
    accounts = []
    with httpx.Client(base_url=os.environ["API_BASE_URL"], timeout=20, trust_env=False) as live:
        try:
            for _ in range(2):
                response = live.post(
                    "/api/v1/auth/register",
                    json={
                        "email": f"{uuid4()}@example.invalid",
                        "password": "collection-policy-fixture-password-2026",
                    },
                )
                assert response.status_code == 201
                account = response.json()["data"]
                accounts.append(account)
                with Session(get_engine()) as session, session.begin():
                    actor = authenticate_session(session, account["access_token"])
                    access = member_access(UUID(account["tenant_id"]), actor)
                    batch = save_batch(access, f"https://example.com/{uuid4()}", uuid4())
                    account["source_id"] = batch["sources"][0]["id"]
                    # No dispatch: the real Worker must never see or collect a
                    # synthetic policy fixture as though it were user content.
                    job = ProcessingJob(
                        tenant_id=UUID(account["tenant_id"]),
                        requested_by=UUID(account["user_id"]),
                        kind="source_collect",
                        status="running",
                        progress=10,
                        idempotency_key=str(uuid4()),
                        payload={"source_id": account["source_id"]},
                    )
                    session.add(job)
                    session.flush()
                    account["snapshot_job_id"] = str(job.id)
                    snapshot = SourceSnapshot(**snapshot_values(account))
                    session.add(snapshot)
                    session.flush()
                    account["snapshot_id"] = str(snapshot.id)
            yield live, admin, accounts
        finally:
            # Only rows belonging to these freshly registered fixture tenants.
            with admin.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout='5s'"))
                for account in accounts:
                    params = {"tenant": account["tenant_id"], "user": account["user_id"]}
                    connection.execute(
                        text("SELECT id FROM tenants WHERE id=:tenant FOR UPDATE"), params
                    )
                    for table in (
                        "evidence_items",
                        "note_sections",
                        "note_revisions",
                        "notes",
                        "note_runs",
                        "source_snapshots",
                        "sources",
                        "source_batches",
                        "audit_events",
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
                    params = {"tenant": account["tenant_id"], "user": account["user_id"]}
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


def test_collection_requires_identity_consent_and_operator_enablement(
    collection_accounts, monkeypatch
):
    _live, _admin, (owner, other) = collection_accounts
    monkeypatch.setenv("COLLECTOR_ENABLED", "false")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            path = source_path(owner) + "/collect"
            payload = {"idempotency_key": str(uuid4()), "consent": True}
            assert client.post(path, json=payload).status_code == 401
            assert client.post(path, headers=auth(other), json=payload).status_code == 404
            response = client.post(path, headers=auth(owner), json={**payload, "consent": False})
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "COLLECTION_CONSENT_REQUIRED"
            response = client.post(path, headers=auth(owner), json=payload)
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "COLLECTOR_DISABLED"
            response = client.get(
                f"/api/v1/spaces/{owner['tenant_id']}/sources/capabilities", headers=auth(owner)
            )
            assert response.status_code == 200 and response.json()["data"]["enabled"] is False
    finally:
        get_settings.cache_clear()


def test_snapshot_api_is_tenant_scoped_and_explicit_about_content_limits(collection_accounts):
    live, _admin, (owner, other) = collection_accounts
    path = source_path(owner) + "/snapshots"
    assert live.get(path).status_code == 401
    assert live.get(path, headers=auth(other)).status_code == 404
    response = live.get(path, headers=auth(owner))
    assert response.status_code == 200
    rows = response.json()["data"]
    assert [row["id"] for row in rows] == [owner["snapshot_id"]]
    assert "body_text" not in rows[0] and rows[0]["version"] == 1
    response = live.get(path + "/" + owner["snapshot_id"], headers=auth(owner))
    assert response.status_code == 200
    snapshot = response.json()["data"]
    assert snapshot["body_text"].startswith("Explicit database policy fixture.")
    assert snapshot["details"] == {"fixture": "database-policy-only"}
    assert snapshot["limitations"] == ["Not a public collection acceptance result."]
    assert live.get(path + "/" + other["snapshot_id"], headers=auth(owner)).status_code == 404


def test_snapshot_real_rls_actor_append_only_and_composite_relations(collection_accounts):
    _live, admin, (owner, other) = collection_accounts
    with get_engine().begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM source_snapshots")).scalar_one() == 0
        context(connection, owner)
        assert (
            connection.execute(
                text("SELECT count(*) FROM source_snapshots WHERE id=:id"),
                {"id": other["snapshot_id"]},
            ).scalar_one()
            == 0
        )
        assert connection.execute(
            text("SELECT id FROM source_snapshots WHERE id=:id"), {"id": owner["snapshot_id"]}
        ).scalar_one()
    for sql in ("UPDATE source_snapshots SET title=title", "DELETE FROM source_snapshots"):
        with pytest.raises(DBAPIError) as error, get_engine().begin() as connection:
            context(connection, owner)
            connection.execute(text(sql))
        assert error.value.orig.sqlstate == "42501"
    # Cross-tenant payload cannot enter a runtime INSERT even with a foreign ID.
    with pytest.raises(DBAPIError) as error, Session(get_engine()) as session, session.begin():
        context(session, owner)
        session.add(SourceSnapshot(**snapshot_values(other, version=2)))
        session.flush()
    assert error.value.orig.sqlstate == "42501"
    # An existing member is still not allowed to forge another actor's snapshot.
    with admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenant_members (tenant_id,user_id,role,active) VALUES "
                "(:tenant,:user,'member',true) ON CONFLICT DO NOTHING"
            ),
            {"tenant": owner["tenant_id"], "user": other["user_id"]},
        )
    with pytest.raises(DBAPIError) as error, Session(get_engine()) as session, session.begin():
        context(session, {**owner, "user_id": other["user_id"]})
        session.add(SourceSnapshot(**snapshot_values(owner, version=2)))
        session.flush()
    assert error.value.orig.sqlstate == "42501"
    # Admin bypass is used solely to isolate the composite FK from the RLS test.
    with pytest.raises(DBAPIError) as error, Session(admin) as session, session.begin():
        session.add(
            SourceSnapshot(
                **snapshot_values(owner, job_id=UUID(other["snapshot_job_id"]), version=2)
            )
        )
        session.flush()
    assert error.value.orig.sqlstate == "23503"


def test_collection_queue_idempotency_does_not_commit_or_publish_fixture_dispatches(
    collection_accounts, monkeypatch
):
    _live, _admin, (owner, _other) = collection_accounts
    monkeypatch.setenv("COLLECTOR_ENABLED", "true")
    get_settings.cache_clear()
    try:
        with Session(get_engine()) as session:
            transaction = session.begin()
            try:
                actor = authenticate_session(session, owner["access_token"])
                access = member_access(UUID(owner["tenant_id"]), actor)
                before = session.scalar(select(func.count()).select_from(ProcessingJob))
                key = uuid4()
                job = queue_collection(access, UUID(owner["source_id"]), key)
                assert job["kind"] == "source_collect" and job["status"] == "queued"
                assert queue_collection(access, UUID(owner["source_id"]), key) == job
                session.flush()
                assert session.scalar(select(func.count()).select_from(ProcessingJob)) == before + 1
                second = save_batch(access, f"https://example.com/{uuid4()}", uuid4())
                with pytest.raises(HTTPException) as error:
                    queue_collection(access, UUID(second["sources"][0]["id"]), key)
                assert error.value.status_code == 409
                assert error.value.detail["code"] == "IDEMPOTENCY_CONFLICT"
                with pytest.raises(HTTPException) as error:
                    queue_collection(access, uuid4(), uuid4())
                assert error.value.status_code == 404
            finally:
                transaction.rollback()
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("interruption", ["cancel", "revoke"])
def test_collection_drops_result_after_permission_or_cancellation_during_io(
    collection_accounts, monkeypatch, interruption
):
    from app.integrations.platforms.base import CollectedContent

    live, admin, (owner, _other) = collection_accounts
    monkeypatch.setenv("COLLECTOR_ENABLED", "true")
    get_settings.cache_clear()
    job_id, dispatch_id = uuid4(), uuid4()
    with Session(get_engine()) as session, session.begin():
        actor = authenticate_session(session, owner["access_token"])
        access = member_access(UUID(owner["tenant_id"]), actor)
        session.add(
            ProcessingJob(
                id=job_id,
                tenant_id=access.tenant.id,
                requested_by=actor.user.id,
                kind="source_collect",
                idempotency_key=str(uuid4()),
                payload={"source_id": owner["source_id"]},
            )
        )
        session.flush()
        session.add(JobStep(tenant_id=access.tenant.id, job_id=job_id, number=1, status="queued"))
        session.add(
            JobDispatch(
                id=dispatch_id,
                tenant_id=access.tenant.id,
                job_id=job_id,
                requested_by=actor.user.id,
                # Keep the independent live Worker from racing this controlled
                # safety regression; external platform acceptance is separate.
                due_at=datetime.now(UTC) + timedelta(days=1),
            )
        )

    def collect_after_interruption(url):
        if interruption == "cancel":
            response = live.post(
                f"/api/v1/spaces/{owner['tenant_id']}/jobs/{job_id}/cancel", headers=auth(owner)
            )
            assert response.status_code == 200
        else:
            with admin.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE tenant_members SET active=false "
                        "WHERE tenant_id=:tenant AND user_id=:user"
                    ),
                    {"tenant": owner["tenant_id"], "user": owner["user_id"]},
                )
        return CollectedContent(
            "web",
            url,
            url,
            "article",
            "Safety fixture only",
            None,
            "Synthetic connector result for cancellation policy, not collected content.",
            {"fixture": True},
            ("SAFETY_FIXTURE_ONLY",),
            "c" * 64,
        )

    monkeypatch.setattr("app.integrations.platforms.collect", collect_after_interruption)
    try:
        execute_dispatch(str(dispatch_id))
        with admin.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM source_snapshots WHERE job_id=:id"), {"id": job_id}
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text("SELECT done FROM job_dispatches WHERE id=:id"), {"id": dispatch_id}
                ).scalar_one()
                is True
            )
            if interruption == "cancel":
                assert (
                    connection.execute(
                        text("SELECT status FROM job_attempts WHERE job_id=:id"), {"id": job_id}
                    ).scalar_one()
                    == "cancelled"
                )
                assert (
                    connection.execute(
                        text("SELECT lease_until FROM processing_jobs WHERE id=:id"), {"id": job_id}
                    ).scalar_one()
                    is None
                )
    finally:
        if interruption == "revoke":
            with admin.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE tenant_members SET active=true "
                        "WHERE tenant_id=:tenant AND user_id=:user"
                    ),
                    {"tenant": owner["tenant_id"], "user": owner["user_id"]},
                )
        get_settings.cache_clear()


def test_refresh_only_reuses_latest_identical_snapshot_and_preserves_metadata_changes(
    collection_accounts, monkeypatch
):
    from dataclasses import replace

    from app.integrations.platforms.base import CollectedContent

    _live, _admin, (owner, _other) = collection_accounts
    monkeypatch.setenv("COLLECTOR_ENABLED", "true")
    get_settings.cache_clear()
    content = CollectedContent(
        "web",
        "https://example.com/",
        "https://example.com/",
        "article",
        "Refresh protocol fixture",
        None,
        "Protocol fixture, never fetched.",
        {"fixture": "refresh-only"},
        ("PROTOCOL_ONLY",),
        "d" * 64,
    )
    monkeypatch.setattr("app.integrations.platforms.collect", lambda _url: content)

    def refresh():
        job_id, dispatch_id = uuid4(), uuid4()
        with Session(get_engine()) as session, session.begin():
            actor = authenticate_session(session, owner["access_token"])
            access = member_access(UUID(owner["tenant_id"]), actor)
            session.add(
                ProcessingJob(
                    id=job_id,
                    tenant_id=access.tenant.id,
                    requested_by=actor.user.id,
                    kind="source_collect",
                    idempotency_key=str(uuid4()),
                    payload={"source_id": owner["source_id"]},
                )
            )
            session.flush()
            session.add(
                JobStep(tenant_id=access.tenant.id, job_id=job_id, number=1, status="queued")
            )
            session.add(
                JobDispatch(
                    id=dispatch_id,
                    tenant_id=access.tenant.id,
                    job_id=job_id,
                    requested_by=actor.user.id,
                    due_at=datetime.now(UTC) + timedelta(days=1),
                )
            )
        execute_dispatch(str(dispatch_id))
        with Session(get_engine()) as session, session.begin():
            actor = authenticate_session(session, owner["access_token"])
            member_access(UUID(owner["tenant_id"]), actor)
            job = session.get(ProcessingJob, job_id)
            assert job is not None and job.status == "succeeded"
            return job.result

    try:
        original = content
        first = refresh()
        repeated = refresh()
        assert repeated["snapshot_id"] == first["snapshot_id"] and repeated["unchanged"]
        for changes in (
            {"metadata": {"fixture": "changed"}},
            {"limitations": ("CHANGED_LIMIT",)},
            {"fetched_url": "https://example.com/changed"},
            {"text": "Changed protocol body", "raw_sha256": "e" * 64},
        ):
            content = replace(content, **changes)
            changed = refresh()
            assert changed["snapshot_id"] != repeated["snapshot_id"]
            assert changed["version"] == first["version"] + 1
            assert "unchanged" not in changed
            first, repeated = changed, changed
        content = original
        reverted = refresh()
        assert reverted["version"] == first["version"] + 1
        assert reverted["snapshot_id"] != first["snapshot_id"]
    finally:
        get_settings.cache_clear()
