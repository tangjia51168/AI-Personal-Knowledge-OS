"""Live durable jobs, real broker/Worker and isolated fixture-only failure injection."""

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
from app.modules.identity.access import set_tenant
from app.modules.jobs.execution import dispatch_pending, execute_dispatch, worker_job
from celery import Celery
from kombu.exceptions import OperationalError
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated stack required"
    ),
]


def test_live_durable_jobs_replay_recovery_and_authorization(monkeypatch):
    settings = get_settings()
    assert settings.app_env == "test" and settings.migration_database_url
    admin = create_engine(settings.migration_database_url.get_secret_value())
    accounts = []
    with httpx.Client(base_url=os.environ["API_BASE_URL"], timeout=20, trust_env=False) as client:
        try:
            for _ in range(2):
                response = client.post(
                    "/api/v1/auth/register",
                    json={
                        "email": f"{uuid4()}@example.invalid",
                        "password": "acceptance-only-password-2026",
                    },
                )
                assert response.status_code == 201, response.text
                accounts.append(response.json()["data"])
            a, b = accounts
            auth = {"Authorization": f"Bearer {a['access_token']}"}
            other = {"Authorization": f"Bearer {b['access_token']}"}
            path = f"/api/v1/spaces/{a['tenant_id']}/jobs"
            assert client.post(path, json={"idempotency_key": str(uuid4())}).status_code == 401
            assert client.get(path, headers=other).status_code == 404
            key = str(uuid4())
            response = client.post(path, headers=auth, json={"idempotency_key": key})
            assert response.status_code == 202, response.text
            job_id = response.json()["data"]["id"]
            assert (
                client.post(path, headers=auth, json={"idempotency_key": key}).json()["data"]["id"]
                == job_id
            )
            with admin.begin() as connection:
                assert (
                    connection.execute(
                        text(
                            "SELECT count(*) FROM audit_events WHERE tenant_id=:tenant "
                            "AND resource_id=:job AND action='job.created'"
                        ),
                        {"tenant": a["tenant_id"], "job": job_id},
                    ).scalar_one()
                    == 1
                )

            def wait_job(value):
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    response = client.get(f"{path}/{value}", headers=auth)
                    assert response.status_code == 200, response.text
                    data = response.json()["data"]
                    if data["status"] in ("succeeded", "failed", "cancelled"):
                        return data
                    time.sleep(0.3)
                pytest.fail("Durable job not completed by independent Worker")

            completed = wait_job(job_id)
            assert completed["status"] == "succeeded", completed
            assert completed["progress"] == 100 and completed["attempt_count"] == 1
            assert completed["steps"][0]["status"] == "succeeded"
            result = client.get(f"{path}/{job_id}/result", headers=auth).json()["data"]
            assert result["space"]["id"] == a["tenant_id"] and result["assets"] == []
            assert client.get(f"{path}/{job_id}/result", headers=other).status_code == 404
            with admin.begin() as connection:
                dispatch_id = connection.execute(
                    text("SELECT id FROM job_dispatches WHERE job_id=:id"), {"id": job_id}
                ).scalar_one()
            # Replay real broker messages: terminal result/attempts must remain unchanged.
            for _ in range(3):
                get_task_queue().send_task("knowledge_os.execute", args=[str(dispatch_id)])
            execute_dispatch(str(dispatch_id))
            assert client.get(f"{path}/{job_id}", headers=auth).json()["data"]["attempt_count"] == 1

            # FK validation on a concurrent INSERT takes KEY SHARE locks on its
            # member/tenant parents. A Worker must not block on those locks: it
            # changes status only, never their referenced keys. This previously
            # formed tenant -> member / member -> tenant deadlocks.
            def claim_existing():
                with Session(get_engine()) as session, session.begin():
                    session.execute(text("SET LOCAL lock_timeout='2s'"))
                    assert worker_job(session, dispatch_id) is not None

            with ThreadPoolExecutor(max_workers=1) as pool, admin.connect() as connection:
                transaction = connection.begin()
                try:
                    connection.execute(
                        text(
                            "INSERT INTO processing_jobs "
                            "(id,tenant_id,requested_by,kind,status,progress,"
                            "idempotency_key,payload) "
                            "VALUES (:id,:tenant,:user,'space_export','queued',0,:key,'{}')"
                        ),
                        {
                            "id": uuid4(),
                            "tenant": a["tenant_id"],
                            "user": a["user_id"],
                            "key": uuid4().hex,
                        },
                    )
                    pool.submit(claim_existing).result(timeout=8)
                finally:
                    transaction.rollback()

            # No publish until deliberately made due: models DB/broker outage recovery
            # without stopping shared services or introducing a production failure hook.
            def held_job(kind="space_export", status="queued", attempt=0):
                ident, dispatch = uuid4(), uuid4()
                with admin.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO processing_jobs "
                            "(id,tenant_id,requested_by,kind,status,progress,"
                            "idempotency_key,payload,attempt_count,lease_until) "
                            "VALUES (:id,:tenant,:user,:kind,:status,0,:key,'{}',:attempt,:lease)"
                        ),
                        {
                            "id": ident,
                            "tenant": a["tenant_id"],
                            "user": a["user_id"],
                            "kind": kind,
                            "status": status,
                            "key": str(uuid4()),
                            "attempt": attempt,
                            "lease": datetime.now(UTC) - timedelta(seconds=1) if attempt else None,
                        },
                    )
                    connection.execute(
                        text(
                            "INSERT INTO job_steps (id,tenant_id,job_id,number,status) "
                            "VALUES (:step,:tenant,:id,1,'queued')"
                        ),
                        {"step": uuid4(), "tenant": a["tenant_id"], "id": ident},
                    )
                    connection.execute(
                        text(
                            "INSERT INTO job_dispatches (id,tenant_id,job_id,requested_by,due_at) "
                            "VALUES (:dispatch,:tenant,:id,:user,now()+interval '1 day')"
                        ),
                        {
                            "dispatch": dispatch,
                            "tenant": a["tenant_id"],
                            "id": ident,
                            "user": a["user_id"],
                        },
                    )
                    if attempt:
                        connection.execute(
                            text(
                                "INSERT INTO job_attempts (id,tenant_id,job_id,number,status) "
                                "VALUES (:attempt,:tenant,:id,1,'running')"
                            ),
                            {"attempt": uuid4(), "tenant": a["tenant_id"], "id": ident},
                        )
                return str(ident), str(dispatch)

            cancelled, cancel_dispatch = held_job()
            assert (
                client.post(f"{path}/{cancelled}/cancel", headers=auth).json()["data"]["status"]
                == "cancelled"
            )
            execute_dispatch(cancel_dispatch)
            assert client.get(f"{path}/{cancelled}/result", headers=auth).status_code == 409

            broken, broken_dispatch = held_job("unsupported-fixture-only")
            execute_dispatch(broken_dispatch)
            assert wait_job(broken)["error_code"] == "EXECUTION_FAILED"
            with admin.begin() as connection:
                connection.execute(
                    text("UPDATE processing_jobs SET kind='space_export' WHERE id=:id"),
                    {"id": broken},
                )
            assert client.post(f"{path}/{broken}/retry", headers=auth).status_code == 202
            repaired = wait_job(broken)
            assert repaired["status"] == "succeeded" and repaired["attempt_count"] == 2

            interrupted, interrupted_dispatch = held_job(status="running", attempt=1)
            execute_dispatch(interrupted_dispatch)
            recovered = wait_job(interrupted)
            assert recovered["status"] == "succeeded" and recovered["attempt_count"] == 2
            assert recovered["attempts"][0]["error_code"] == "WORKER_INTERRUPTED"

            durable, _ = held_job()
            with admin.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE job_dispatches SET due_at=now()-interval '1 second' "
                        "WHERE job_id=:id"
                    ),
                    {"id": durable},
                )
            # A real refused broker connection, not eager mode or a mocked publish.
            unavailable = Celery("acceptance-broker-outage", broker="redis://127.0.0.1:1/0")
            unavailable.conf.update(
                broker_connection_timeout=1,
                task_publish_retry=False,
                broker_transport_options={"socket_connect_timeout": 1},
            )
            try:
                with monkeypatch.context() as outage:
                    outage.setattr("app.modules.jobs.execution.get_task_queue", lambda: unavailable)
                    with pytest.raises(OperationalError):
                        dispatch_pending()
                with admin.begin() as connection:
                    assert connection.execute(
                        text(
                            "SELECT due_at < now() AND NOT done FROM job_dispatches "
                            "WHERE job_id=:id"
                        ),
                        {"id": durable},
                    ).scalar_one()
            finally:
                unavailable.close()
            dispatch_pending()
            assert wait_job(durable)["status"] == "succeeded"

            # Worker never trusts a valid dispatch UUID if requester membership is revoked.
            revoked, revoked_dispatch = held_job()
            with admin.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE tenant_members SET active=false "
                        "WHERE tenant_id=:tenant AND user_id=:user"
                    ),
                    {"tenant": a["tenant_id"], "user": a["user_id"]},
                )
            execute_dispatch(revoked_dispatch)
            with admin.begin() as connection:
                assert (
                    connection.execute(
                        text("SELECT result FROM processing_jobs WHERE id=:id"), {"id": revoked}
                    ).scalar()
                    is None
                )
                connection.execute(
                    text(
                        "UPDATE tenant_members SET active=true "
                        "WHERE tenant_id=:tenant AND user_id=:user"
                    ),
                    {"tenant": a["tenant_id"], "user": a["user_id"]},
                )
            assert client.post(f"{path}/{revoked}/cancel", headers=auth).status_code == 200

            # Application-role RLS hides all other tenant jobs even with a known ID.
            with Session(get_engine()) as session, session.begin():
                set_tenant(session, UUID(b["tenant_id"]))
                assert (
                    session.scalar(select(ProcessingJob).where(ProcessingJob.id == UUID(job_id)))
                    is None
                )

            held = [held_job()[0] for _ in range(10)]
            assert (
                client.post(path, headers=auth, json={"idempotency_key": str(uuid4())}).status_code
                == 429
            )
            for ident in held:
                assert client.post(f"{path}/{ident}/cancel", headers=auth).status_code == 200
        finally:
            # Only this isolated test's fixture rows; never truncate or drop user data.
            with admin.begin() as connection:
                for account in accounts:
                    params = {"tenant": account["tenant_id"], "user": account["user_id"]}
                    # Match the consumer's tenant -> job -> dispatch lock order.
                    connection.execute(
                        text("SELECT id FROM tenants WHERE id=:tenant FOR UPDATE"), params
                    )
                    for table in (
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
                    for table in ("auth_security_events", "auth_sessions", "auth_identities"):
                        connection.execute(text(f"DELETE FROM {table} WHERE user_id=:user"), params)
                    connection.execute(text("DELETE FROM tenants WHERE id=:tenant"), params)
                    connection.execute(text("DELETE FROM users WHERE id=:user"), params)
            admin.dispose()
