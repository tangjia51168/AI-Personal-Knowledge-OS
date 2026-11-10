"""Real user imports through API, broker and the independent Worker.

The supplied prose/subtitles are labelled test content, never a platform fetch
or model result. Policy and interruption cases use separate undispatched jobs;
only the live API cases count as end-to-end Worker acceptance.
"""

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from app.core.config import get_settings
from app.db.models import ProcessingJob
from app.db.session import get_engine
from app.integrations.queue import get_task_queue
from app.modules.identity.access import authenticate_session, member_access
from app.modules.jobs.execution import execute_dispatch
from app.modules.jobs.models import JobDispatch, JobStep
from app.modules.sources.models import SourceSnapshot
from app.modules.sources.service import save_batch
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from scripts.account_e2e import cleanup_fixture

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]


def auth(account):
    return {"Authorization": "Bearer " + account["access_token"]}


def source_path(account, source_id=None):
    return f"/api/v1/spaces/{account['tenant_id']}/sources/{source_id or account['source_id']}"


def context(connection, account):
    connection.execute(
        text(
            "SELECT set_config('app.tenant_id',:tenant,true),set_config('app.user_id',:user,true)"
        ),
        {"tenant": account["tenant_id"], "user": account["user_id"]},
    )


def import_body(**changes):
    return {
        "idempotency_key": str(uuid4()),
        "consent": True,
        "title": "人工提供的导入验收文本，非平台采集",
        "text": "用户授权导入的测试正文，不是真实平台内容或模型生成。",
        "format": "text",
        "content_kind": "article",
        **changes,
    }


def snapshot_values(account, **changes):
    return {
        "id": uuid4(),
        "tenant_id": UUID(account["tenant_id"]),
        "source_id": UUID(account["policy_source_id"]),
        "job_id": UUID(account["policy_job_id"]),
        "created_by": UUID(account["user_id"]),
        "version": 1,
        "fetched_url": account["policy_url"],
        "platform": "user_import",
        "provenance": "user_import",
        "content_kind": "article",
        "title": "Database policy fixture, user supplied and not fetched",
        "author": "",
        "body_text": "User supplied database policy fixture. No platform or model request.",
        "raw_sha256": "a" * 64,
        "details": {"fixture": "database-policy-only", "import_format": "text"},
        "limitations": ["USER_PROVIDED_CONTENT", "SOURCE_CONTENT_NOT_VERIFIED"],
        **changes,
    }


@pytest.fixture(scope="module")
def import_accounts():
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
                        "password": "user-import-fixture-only-password-2026",
                    },
                )
                assert response.status_code == 201
                account = response.json()["data"]
                accounts.append(account)
                with Session(get_engine()) as session, session.begin():
                    actor = authenticate_session(session, account["access_token"])
                    access = member_access(UUID(account["tenant_id"]), actor)
                    batch = save_batch(
                        access,
                        f"https://example.com/user-import/{uuid4()}\n"
                        f"https://example.com/import-policy/{uuid4()}",
                        uuid4(),
                    )
                    account["source_id"] = batch["sources"][0]["id"]
                    account["source_url"] = batch["sources"][0]["canonical_url"]
                    account["policy_source_id"] = batch["sources"][1]["id"]
                    account["policy_url"] = batch["sources"][1]["canonical_url"]
                    # No dispatch: this row verifies SQL policy only. It must
                    # never be mistaken for the live Worker acceptance below.
                    job = ProcessingJob(
                        tenant_id=access.tenant.id,
                        requested_by=actor.user.id,
                        kind="source_import",
                        status="running",
                        idempotency_key=str(uuid4()),
                        payload={"source_id": account["policy_source_id"]},
                    )
                    session.add(job)
                    session.flush()
                    account["policy_job_id"] = str(job.id)
                    snapshot = SourceSnapshot(**snapshot_values(account))
                    session.add(snapshot)
                    session.flush()
                    account["policy_snapshot_id"] = str(snapshot.id)
            yield live, admin, accounts
        finally:
            with admin.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout='5s'"))
                for account in accounts:
                    cleanup_fixture(connection, account)
            admin.dispose()


def wait_job(live, account, job_id):
    path = f"/api/v1/spaces/{account['tenant_id']}/jobs/{job_id}"
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        response = live.get(path, headers=auth(account))
        assert response.status_code == 200
        job = response.json()["data"]
        if job["status"] in ("succeeded", "failed", "cancelled"):
            return job
        time.sleep(0.25)
    pytest.fail("User import was not completed by the independent Worker")


def job_snapshot(live, account, job_id):
    path = f"/api/v1/spaces/{account['tenant_id']}/jobs/{job_id}/result"
    response = live.get(path, headers=auth(account))
    assert response.status_code == 200
    result = response.json()["data"]
    response = live.get(
        source_path(account) + f"/snapshots/{result['snapshot_id']}", headers=auth(account)
    )
    assert response.status_code == 200
    return response.json()["data"]


def test_live_import_requires_identity_consent_and_owned_source(import_accounts):
    live, _admin, (owner, other) = import_accounts
    path, body = source_path(owner) + "/imports", import_body()
    assert live.post(path, json=body).status_code == 401
    assert live.post(path, headers=auth(other), json=body).status_code == 404
    assert (
        live.post(
            source_path(owner, other["source_id"]) + "/imports", headers=auth(owner), json=body
        ).status_code
        == 404
    )
    response = live.post(path, headers=auth(owner), json={**body, "consent": False})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IMPORT_CONSENT_REQUIRED"
    for changes in (
        {"text": "中" * 22000},  # Character count fits; UTF-8 byte count does not.
        {"format": "srt", "content_kind": "article"},
        {"text": "   "},
        {"provenance": "platform_collection"},
    ):
        response = live.post(path, headers=auth(owner), json={**body, **changes})
        assert response.status_code == 422


def test_live_article_import_worker_concurrent_idempotency_and_broker_replay(import_accounts):
    live, admin, (owner, other) = import_accounts
    # More than the ordinary 16KiB request ceiling proves the dedicated import
    # body boundary reaches the actual API, with no platform or provider I/O.
    supplied = "\n".join(["User supplied import acceptance prose; not a fetched page."] * 400)
    body = import_body(text=supplied)
    path = source_path(owner) + "/imports"
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(
            pool.map(lambda _: live.post(path, headers=auth(owner), json=body), range(3))
        )
    assert all(response.status_code == 202 for response in responses)
    ids = {response.json()["data"]["id"] for response in responses}
    assert len(ids) == 1
    job_id = ids.pop()
    completed = wait_job(live, owner, job_id)
    assert completed["kind"] == "source_import" and completed["status"] == "succeeded"
    assert completed["progress"] == 100 and completed["attempt_count"] == 1
    assert completed["steps"][0]["status"] == "succeeded"
    snapshot = job_snapshot(live, owner, job_id)
    assert snapshot["provenance"] == "user_import" and snapshot["platform"] == "user_import"
    assert snapshot["content_kind"] == "article" and snapshot["body_text"] == supplied
    assert snapshot["raw_sha256"] == hashlib.sha256(supplied.encode()).hexdigest()
    assert snapshot["fetched_url"] == owner["source_url"]
    assert snapshot["details"]["import_format"] == "text"
    assert snapshot["details"]["input_utf8_bytes"] == len(supplied.encode())
    assert "USER_PROVIDED_CONTENT" in snapshot["limitations"]
    assert "SOURCE_CONTENT_NOT_VERIFIED" in snapshot["limitations"]
    assert (
        live.get(
            f"/api/v1/spaces/{owner['tenant_id']}/jobs/{job_id}/result", headers=auth(other)
        ).status_code
        == 404
    )
    conflict = live.post(path, headers=auth(owner), json={**body, "text": "Changed user prose"})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    with admin.connect() as connection:
        params = {"tenant": owner["tenant_id"], "job": job_id, "key": body["idempotency_key"]}
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM processing_jobs "
                    "WHERE tenant_id=:tenant AND idempotency_key=:key"
                ),
                params,
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM source_snapshots WHERE tenant_id=:tenant AND job_id=:job"
                ),
                params,
            ).scalar_one()
            == 1
        )
        dispatch_id = connection.execute(
            text("SELECT id FROM job_dispatches WHERE job_id=:job"), params
        ).scalar_one()
    # Publish a real duplicate and also exercise the terminal guard directly.
    # Only the initial API submission above proves independent execution.
    get_task_queue().send_task("knowledge_os.execute", args=[str(dispatch_id)])
    execute_dispatch(str(dispatch_id))
    replay = live.post(path, headers=auth(owner), json=body)
    assert replay.status_code == 202 and replay.json()["data"]["id"] == job_id
    assert job_snapshot(live, owner, job_id)["id"] == snapshot["id"]
    assert wait_job(live, owner, job_id)["attempt_count"] == 1


def test_live_srt_and_vtt_imports_preserve_time_labels_and_append_versions(import_accounts):
    live, _admin, (_owner, other) = import_accounts
    inputs = (
        (
            "srt",
            "1\n00:00:01,000 --> 00:00:03,000\n人工提供的字幕验收内容。\n\n"
            "2\n00:00:04,000 --> 00:00:06,000\n第二段字幕，不是平台采集。\n",
        ),
        (
            "vtt",
            "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n人工提供的字幕验收内容。\n\n"
            "00:00:04.000 --> 00:00:06.000\n第二段字幕，不是平台采集。\n",
        ),
    )
    snapshots = []
    for format_name, supplied in inputs:
        response = live.post(
            source_path(other) + "/imports",
            headers=auth(other),
            json=import_body(format=format_name, content_kind="transcript", text=supplied),
        )
        assert response.status_code == 202
        job_id = response.json()["data"]["id"]
        assert wait_job(live, other, job_id)["status"] == "succeeded"
        snapshot = job_snapshot(live, other, job_id)
        snapshots.append(snapshot)
        assert snapshot["provenance"] == "user_import"
        assert snapshot["content_kind"] == "transcript"
        assert "人工提供的字幕验收内容。" in snapshot["body_text"]
        assert "00:00:01" in snapshot["body_text"] and "00:00:06" in snapshot["body_text"]
        assert snapshot["raw_sha256"] == hashlib.sha256(supplied.encode()).hexdigest()
        assert snapshot["details"]["import_format"] == format_name
        assert snapshot["details"]["cue_count"] == 2
        assert "SOURCE_CONTENT_NOT_VERIFIED" in snapshot["limitations"]
    assert snapshots[1]["version"] == snapshots[0]["version"] + 1
    response = live.get(source_path(other) + "/snapshots", headers=auth(other))
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["data"]] == [
        snapshots[1]["id"],
        snapshots[0]["id"],
    ]


def test_import_snapshot_sql_policy_prevents_actor_source_and_provenance_forgery(import_accounts):
    _live, _admin, (owner, other) = import_accounts
    with get_engine().begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM source_snapshots")).scalar_one() == 0
        context(connection, owner)
        assert (
            connection.execute(
                text("SELECT count(*) FROM source_snapshots WHERE id=:id"),
                {"id": other["policy_snapshot_id"]},
            ).scalar_one()
            == 0
        )
    for sql in ("UPDATE source_snapshots SET title=title", "DELETE FROM source_snapshots"):
        with pytest.raises(DBAPIError) as error, get_engine().begin() as connection:
            context(connection, owner)
            connection.execute(text(sql))
        assert error.value.orig.sqlstate == "42501"
    for changes in (
        {"provenance": "platform_collection"},
        {"source_id": UUID(owner["source_id"])},
        {"created_by": UUID(other["user_id"])},
        {"tenant_id": UUID(other["tenant_id"])},
    ):
        with pytest.raises(DBAPIError) as error, Session(get_engine()) as session, session.begin():
            context(session, owner)
            session.add(SourceSnapshot(**snapshot_values(owner, version=2, **changes)))
            session.flush()
        assert error.value.orig.sqlstate == "42501"


@pytest.mark.parametrize("interruption", ["cancel", "revoke"])
def test_import_revalidates_after_claim_before_processing(
    import_accounts, monkeypatch, interruption
):
    from app.modules.sources import imports

    live, admin, (owner, _other) = import_accounts
    job_id, dispatch_id = uuid4(), uuid4()
    body = import_body()
    payload = {
        key: value for key, value in body.items() if key not in ("consent", "idempotency_key")
    }
    payload["source_id"] = owner["source_id"]
    with Session(get_engine()) as session, session.begin():
        actor = authenticate_session(session, owner["access_token"])
        access = member_access(UUID(owner["tenant_id"]), actor)
        session.add(
            ProcessingJob(
                id=job_id,
                tenant_id=access.tenant.id,
                requested_by=actor.user.id,
                kind="source_import",
                idempotency_key=body["idempotency_key"],
                payload=payload,
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
                # Only the controlled safety regression invokes this dispatch.
                # It does not count as independent Worker acceptance.
                due_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
    execute_import = imports.execute_import

    def interrupt_after_claim(value, attempt):
        assert value == dispatch_id and attempt == 1
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
        execute_import(value, attempt)

    monkeypatch.setattr(imports, "execute_import", interrupt_after_claim)
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
            with admin.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT status FROM job_attempts WHERE job_id=:id"), {"id": job_id}
                    ).scalar_one()
                    == "cancelled"
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
