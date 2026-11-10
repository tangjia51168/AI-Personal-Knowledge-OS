"""Real persistence, queue, isolation and pause; never enables outbound collection."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.core.config import get_settings
from app.db.session import get_engine
from app.modules.sources import schedules
from app.modules.sources.schedules import ScheduleInput, ScheduleUpdate
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_manual_first_note_integration import accounts
from test_notes_integration import context, identity

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Isolated stack required"),
]


def body(owner):
    return ScheduleInput(source_id=owner["source_id"], interval_minutes=1440, consent=True)


def force_due(admin, owner, plan):
    with admin.begin() as conn:
        conn.execute(
            text("SELECT id FROM tenants WHERE id=:t FOR NO KEY UPDATE"), {"t": owner["tenant_id"]}
        )
        conn.execute(
            text("UPDATE source_schedules SET next_run_at=:due WHERE id=:id"),
            {"id": plan["id"], "due": datetime.now(UTC) - timedelta(seconds=1)},
        )
        conn.execute(
            text("UPDATE source_schedule_ticks SET due_at=:due WHERE schedule_id=:id"),
            {"id": plan["id"], "due": datetime.now(UTC) - timedelta(seconds=1)},
        )


def test_schedule_api_idempotency_ownership_rls_pause_resume_and_conflict():
    with accounts() as (live, _admin, (owner, other)):
        root = f"/api/v1/spaces/{owner['tenant_id']}/sources/schedules"
        auth = {"Authorization": "Bearer " + owner["access_token"]}
        assert live.get(root).status_code == 401
        assert live.get(root + "?tenant=other", headers=auth).status_code == 422
        rejected = {**body(owner).model_dump(mode="json"), "consent": False}
        assert live.post(root, headers=auth, json=rejected).status_code == 400
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(
                workers.map(
                    lambda _: schedules.create(
                        identity(owner), UUID(owner["tenant_id"]), body(owner)
                    ),
                    range(2),
                )
            )
        assert results[0] == results[1]
        plan = results[0]
        pause = {"active": False, "expected_version": 1, "consent": False}
        updated = live.patch(root + "/" + plan["id"], headers=auth, json=pause)
        assert updated.status_code == 200 and updated.json()["data"]["version"] == 2
        assert live.patch(root + "/" + plan["id"], headers=auth, json=pause).status_code == 409
        resumed = live.patch(
            root + "/" + plan["id"],
            headers=auth,
            json={"active": True, "expected_version": 2, "consent": True},
        )
        assert resumed.status_code == 200 and resumed.json()["data"]["active"]
        assert (
            live.patch(
                root + "/" + plan["id"],
                headers={"Authorization": "Bearer " + other["access_token"]},
                json=pause,
            ).status_code
            == 404
        )
        with get_engine().begin() as conn:
            context(conn, other)
            assert conn.execute(text("SELECT count(*) FROM source_schedules")).scalar_one() == 0
        with pytest.raises(DBAPIError), get_engine().begin() as conn:
            context(conn, owner)
            conn.execute(
                text("UPDATE source_schedules SET source_id=:id"), {"id": other["source_id"]}
            )


def test_real_beat_worker_reports_disabled_without_network_and_no_model_usage():
    assert not get_settings().collector_enabled
    with accounts() as (_live, admin, (owner, _other)):
        plan = schedules.create(identity(owner), UUID(owner["tenant_id"]), body(owner))
        force_due(admin, owner, plan)
        # Publish only our opaque schedule UUID through the actual broker and Worker.
        from app.integrations.queue import get_task_queue

        get_task_queue().send_task("knowledge_os.source_schedule", args=[plan["id"]])
        deadline = time.monotonic() + 15
        while True:
            result = schedules.list_own(identity(owner), UUID(owner["tenant_id"]))["schedules"][0]
            if result["last_error"] == "COLLECTOR_DISABLED":
                break
            assert time.monotonic() < deadline
            time.sleep(0.15)
        with get_engine().begin() as conn:
            context(conn, owner)
            assert conn.execute(text("SELECT count(*) FROM source_schedule_jobs")).scalar_one() == 0
            assert conn.execute(text("SELECT count(*) FROM ai_usage")).scalar_one() == 0


def test_due_occurrence_parallel_replay_pause_cancels_and_revocation_stops(monkeypatch):
    with accounts() as (_live, admin, (owner, _other)):
        tenant = UUID(owner["tenant_id"])
        plan = schedules.create(identity(owner), tenant, body(owner))
        force_due(admin, owner, plan)
        # Only the in-process enqueue gate is enabled. Real Workers remain disabled;
        # no platform fixture or fabricated content can be mistaken for collection.
        monkeypatch.setattr(
            schedules,
            "get_settings",
            lambda: get_settings().model_copy(update={"collector_enabled": True}),
        )
        from app.modules.sources import collection

        monkeypatch.setattr(collection, "get_settings", schedules.get_settings)
        with ThreadPoolExecutor(max_workers=2) as workers:
            list(workers.map(schedules.execute_due, [plan["id"], plan["id"]]))
        with get_engine().begin() as conn:
            context(conn, owner)
            assert conn.execute(text("SELECT count(*) FROM source_schedule_jobs")).scalar_one() == 1
        paused = schedules.update(
            identity(owner),
            tenant,
            UUID(plan["id"]),
            ScheduleUpdate(expected_version=1, active=False, consent=False),
        )
        assert not paused["active"]
        with get_engine().begin() as conn:
            context(conn, owner)
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM processing_jobs j JOIN source_schedule_jobs s "
                        "ON s.job_id=j.id AND s.tenant_id=j.tenant_id "
                        "WHERE j.status IN ('queued','running')"
                    )
                ).scalar_one()
                == 0
            )
        schedules.update(
            identity(owner),
            tenant,
            UUID(plan["id"]),
            ScheduleUpdate(expected_version=2, active=True, consent=True),
        )
        force_due(admin, owner, plan)
        with admin.begin() as conn:
            conn.execute(
                text("UPDATE tenant_members SET active=false WHERE tenant_id=:t"), {"t": tenant}
            )
        schedules.execute_due(plan["id"])
        with admin.begin() as conn:
            assert (
                conn.execute(
                    text("SELECT enabled FROM source_schedule_ticks WHERE schedule_id=:id"),
                    {"id": plan["id"]},
                ).scalar_one()
                is False
            )
        with pytest.raises(HTTPException):
            schedules.list_own(identity(owner), tenant)
