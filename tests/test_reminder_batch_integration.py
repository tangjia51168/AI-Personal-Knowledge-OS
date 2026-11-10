"""Real atomic reminder edits, conflicts, audit and per-member authorization."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.modules.learning import reminders
from app.modules.learning.content import LearningUpdate, ReviewUpdate
from app.modules.learning.reminders import (
    ReminderBatch,
    ReminderPlan,
    ReminderSelection,
    apply_plan,
    apply_reminders,
    reminder_selection,
)
from app.modules.learning.service import update
from conftest import isolate_stack_ip_buckets
from fastapi import HTTPException
from sqlalchemy import text
from test_organization_integration import auth, endpoint, identity
from test_organization_integration import organization_accounts as accounts_fixture

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]


@pytest.fixture(scope="module")
def reminder_accounts():
    isolation = isolate_stack_ip_buckets(("learning_write",))
    next(isolation)
    try:
        yield from accounts_fixture.__wrapped__()
    finally:
        next(isolation, None)


def preview(account, ids):
    return reminder_selection(
        identity(account), UUID(account["tenant_id"]), ReminderSelection(note_ids=ids)
    )["items"]


def payload(rows, when):
    return ReminderBatch(
        items=[{"note_id": row["note_id"], "expected": row["expected"]} for row in rows],
        reminder_at=when,
    )


def apply(account, rows, when):
    return apply_reminders(identity(account), UUID(account["tenant_id"]), payload(rows, when))


def test_batch_preserves_status_completion_and_noops_without_audit(reminder_accounts):
    live, admin, (owner, _other) = reminder_accounts
    ids = [note["note_id"] for note in owner["notes"]]
    initial = preview(owner, ids)
    assert len(initial) == 2 and all(row["reminder_at"] is None for row in initial)
    assert apply(owner, initial, None) == {"selected": 2, "changed": 0}
    saved = update(
        identity(owner), UUID(owner["tenant_id"]), UUID(ids[0]), LearningUpdate(status="skimmed")
    )
    initial = preview(owner, ids)
    when = datetime.now(UTC) + timedelta(days=3)
    result = live.put(
        endpoint(owner) + "/learning/reminders/batch",
        headers=auth(owner),
        json=payload(initial, when).model_dump(mode="json"),
    )
    assert result.status_code == 200 and result.json()["data"] == {"selected": 2, "changed": 2}
    rows = live.get(endpoint(owner) + "/learning", headers=auth(owner)).json()["data"]
    by_id = {row["note_id"]: row for row in rows}
    assert by_id[ids[0]]["status"] == "skimmed"
    assert by_id[ids[0]]["completed_at"] == saved["completed_at"]
    assert by_id[ids[1]]["status"] == "unseen" and by_id[ids[1]]["completed_at"] is None
    refreshed = preview(owner, ids)
    assert all(datetime.fromisoformat(row["reminder_at"]) == when for row in refreshed)

    def count():
        with admin.connect() as connection:
            return connection.execute(
                text(
                    "SELECT count(*) FROM audit_events WHERE tenant_id=:t "
                    "AND action='learning.updated'"
                ),
                {"t": owner["tenant_id"]},
            ).scalar_one()

    before = count()
    assert apply(owner, refreshed, when) == {"selected": 2, "changed": 0}
    assert count() == before
    assert apply(owner, refreshed, None) == {"selected": 2, "changed": 2}
    assert count() == before + 2


def test_batch_conflict_foreign_note_and_audit_failure_are_atomic(reminder_accounts, monkeypatch):
    live, _admin, (owner, other) = reminder_accounts
    ids = [note["note_id"] for note in owner["notes"]]
    rows = preview(owner, ids)
    update(
        identity(owner), UUID(owner["tenant_id"]), UUID(ids[1]), LearningUpdate(status="deep_read")
    )
    with pytest.raises(HTTPException) as stale:
        apply(owner, rows, datetime.now(UTC) + timedelta(days=1))
    assert stale.value.status_code == 409
    assert all(row["reminder_at"] is None for row in preview(owner, ids))
    with pytest.raises(HTTPException) as foreign:
        preview(owner, [ids[0], other["notes"][0]["note_id"]])
    assert foreign.value.status_code == 404
    rows = preview(owner, ids)

    def fail_audit(*_args):
        raise RuntimeError("protocol audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(reminders, "record", fail_audit)
        with pytest.raises(RuntimeError, match="protocol audit failure"):
            apply(owner, rows, datetime.now(UTC) + timedelta(days=1))
    assert preview(owner, ids) == rows
    assert apply(owner, rows, datetime.now(UTC) + timedelta(days=2))["changed"] == 2
    with pytest.raises(HTTPException) as replay:
        apply(owner, rows, datetime.now(UTC) + timedelta(days=2))
    assert replay.value.status_code == 409
    # Writers use the same tenant lock as single-note learning edits.
    rows = preview(owner, ids)
    next_time = datetime.now(UTC) + timedelta(days=8)

    def race():
        try:
            return apply(owner, rows, next_time)["changed"]
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _index: race(), range(2))) == [2, 409]
    before = preview(owner, ids)
    foreign_rows = [before[0], {"note_id": other["notes"][0]["note_id"], "expected": "a" * 64}]
    with pytest.raises(HTTPException) as mixed:
        apply(owner, foreign_rows, None)
    assert mixed.value.status_code == 404 and preview(owner, ids) == before


def test_batch_membership_personal_isolation_revoke_logout_and_body_validation(reminder_accounts):
    live, _admin, (owner, other) = reminder_accounts
    root = endpoint(owner) + "/learning/reminders"
    ids = [note["note_id"] for note in owner["notes"]]
    for headers, expected in (({}, 401), (auth(other), 404)):
        assert (
            live.post(root + "/preview", headers=headers, json={"note_ids": ids}).status_code
            == expected
        )
    for data in (
        {"note_ids": []},
        {"note_ids": [ids[0], ids[0]]},
        {"note_ids": ids, "user_id": other["user_id"]},
    ):
        assert live.post(root + "/preview", headers=auth(owner), json=data).status_code == 422
    members = endpoint(owner) + "/members"
    assert (
        live.post(
            members, headers=auth(owner), json={"user_id": other["user_id"], "role": "member"}
        ).status_code
        == 201
    )
    shared = {**other, "tenant_id": owner["tenant_id"]}
    fresh = preview(shared, ids)
    assert all(row["status"] == "unseen" and row["reminder_at"] is None for row in fresh)
    assert apply(shared, fresh, datetime.now(UTC) + timedelta(days=7))["changed"] == 2
    assert preview(owner, ids)[0]["reminder_at"] != preview(shared, ids)[0]["reminder_at"]
    assert live.delete(members + "/" + other["user_id"], headers=auth(owner)).status_code == 200
    assert (
        live.post(root + "/preview", headers=auth(other), json={"note_ids": ids}).status_code == 404
    )
    with pytest.raises(HTTPException) as revoked:
        apply(shared, preview(owner, ids), None)
    assert revoked.value.status_code == 404
    with pytest.raises(HTTPException) as revoked_plan:
        apply_plan(
            identity(shared),
            UUID(shared["tenant_id"]),
            plan_body(preview(owner, ids), datetime.now(UTC)),
        )
    assert revoked_plan.value.status_code == 404
    review = ReviewUpdate(
        status="seen", reminder_at=None, expected=preview(owner, ids)[0]["expected"]
    )
    with pytest.raises(HTTPException) as revoked_review:
        update(
            identity(shared),
            UUID(shared["tenant_id"]),
            UUID(ids[0]),
            review,
            expected=review.expected,
        )
    assert revoked_review.value.status_code == 404
    assert live.post("/api/v1/auth/logout", headers=auth(other)).status_code == 200
    assert (
        live.post(root + "/preview", headers=auth(other), json={"note_ids": ids}).status_code == 401
    )
    assert (
        live.put(
            root + "/plan",
            headers=auth(other),
            json=plan_body(preview(owner, ids), datetime.now(UTC)).model_dump(mode="json"),
        ).status_code
        == 401
    )
    assert (
        live.put(
            endpoint(owner) + f"/notes/{ids[0]}/review",
            headers=auth(other),
            json=review.model_dump(mode="json"),
        ).status_code
        == 401
    )


def plan_body(rows, start):
    return ReminderPlan(
        items=[
            {
                "note_id": row["note_id"],
                "expected": row["expected"],
                "reminder_at": start + timedelta(days=index * 2),
            }
            for index, row in enumerate(rows)
        ]
    )


def test_plan_http_saves_distinct_times_preserves_status_and_reads_through_schedule(
    reminder_accounts,
):
    live, _admin, (owner, other) = reminder_accounts
    ids = [note["note_id"] for note in owner["notes"]]
    rows = preview(owner, ids)
    when = datetime.now(UTC) + timedelta(days=3)
    body = plan_body(rows, when)
    root = endpoint(owner) + "/learning/reminders/plan"
    assert live.put(root, json=body.model_dump(mode="json")).status_code == 401
    assert (
        live.put(
            endpoint(other) + "/learning/reminders/plan",
            headers=auth(owner),
            json=body.model_dump(mode="json"),
        ).status_code
        == 404
    )
    result = live.put(root, headers=auth(owner), json=body.model_dump(mode="json"))
    assert result.status_code == 200 and result.json()["data"] == {"selected": 2, "changed": 2}
    after = preview(owner, ids)
    assert [row["status"] for row in after] == [row["status"] for row in rows]
    assert [datetime.fromisoformat(row["reminder_at"]) for row in after] == [
        item.reminder_at for item in body.items
    ]
    assert (
        apply_plan(identity(owner), UUID(owner["tenant_id"]), plan_body(after, when))["changed"]
        == 0
    )
    schedule = live.get(
        endpoint(owner) + "/learning/schedule?period=7", headers=auth(owner)
    ).json()["data"]
    eligible = {row["note_id"] for row in after if row["status"] != "deep_read"}
    assert {row["note_id"] for row in schedule["items"]} == eligible
    assert all(
        datetime.fromisoformat(row["reminder_at"])
        == when + timedelta(days=ids.index(row["note_id"]) * 2)
        for row in schedule["items"]
    )


def test_plan_conflicts_rollback_foreign_selection_and_races_with_batch(
    reminder_accounts, monkeypatch
):
    _live, _admin, (owner, other) = reminder_accounts
    ids = [note["note_id"] for note in owner["notes"]]
    rows = preview(owner, ids)
    start = datetime.now(UTC) + timedelta(days=12)
    body = plan_body(rows, start)
    actor, tenant = identity(owner), UUID(owner["tenant_id"])

    def fail_audit(*_args):
        raise RuntimeError("plan audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(reminders, "record", fail_audit)
        with pytest.raises(RuntimeError, match="plan audit failure"):
            apply_plan(actor, tenant, body)
    assert preview(owner, ids) == rows
    foreign = plan_body(
        [rows[0], {"note_id": other["notes"][0]["note_id"], "expected": "a" * 64}], start
    )
    with pytest.raises(HTTPException) as mixed:
        apply_plan(actor, tenant, foreign)
    assert mixed.value.status_code == 404 and preview(owner, ids) == rows

    def race(index):
        try:
            result = (
                apply_plan(actor, tenant, body)
                if index == 0
                else apply(owner, rows, start + timedelta(days=4))
            )
            return result["changed"]
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(race, range(2))) == [2, 409]
    with pytest.raises(HTTPException) as stale:
        apply_plan(actor, tenant, body)
    assert stale.value.status_code == 409


def test_review_http_records_completion_clears_due_and_rejects_old_preview(reminder_accounts):
    live, _admin, (owner, _other) = reminder_accounts
    note_id = owner["notes"][0]["note_id"]
    update(
        identity(owner),
        UUID(owner["tenant_id"]),
        UUID(note_id),
        LearningUpdate(status="skimmed", reminder_at=datetime(2020, 1, 1, tzinfo=UTC)),
    )
    row = preview(owner, [note_id])[0]
    body = {"status": "seen", "reminder_at": None, "expected": row["expected"]}
    root = endpoint(owner) + f"/notes/{note_id}/review"
    assert live.put(root, json=body).status_code == 401
    result = live.put(root, headers=auth(owner), json=body)
    assert result.status_code == 200
    saved = result.json()["data"]
    assert saved["status"] == "seen" and saved["reminder_at"] is None
    assert saved["completed_at"] is not None
    assert note_id not in {
        row["note_id"]
        for row in live.get(
            endpoint(owner) + "/learning/schedule?period=due", headers=auth(owner)
        ).json()["data"]["items"]
    }
    with pytest.raises(HTTPException) as stale:
        update(
            identity(owner),
            UUID(owner["tenant_id"]),
            UUID(note_id),
            ReviewUpdate(**body),
            expected=body["expected"],
        )
    assert stale.value.status_code == 409 and stale.value.detail["code"] == "REVIEW_CONFLICT"


def test_review_audit_rollback_and_concurrent_reminder_conflict(reminder_accounts, monkeypatch):
    from app.modules.learning import service

    _live, _admin, (owner, other) = reminder_accounts
    note_id = owner["notes"][0]["note_id"]
    row = preview(owner, [note_id])[0]
    when = datetime.now(UTC) + timedelta(days=5)
    body = ReviewUpdate(status="skimmed", reminder_at=when, expected=row["expected"])
    actor, tenant = identity(owner), UUID(owner["tenant_id"])

    def fail_audit(*_args):
        raise RuntimeError("review audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(service, "record", fail_audit)
        with pytest.raises(RuntimeError, match="review audit failure"):
            update(actor, tenant, UUID(note_id), body, expected=body.expected)
    assert preview(owner, [note_id])[0] == row
    with pytest.raises(HTTPException) as foreign:
        update(actor, tenant, UUID(other["notes"][0]["note_id"]), body, expected=body.expected)
    assert foreign.value.status_code == 404

    def race(index):
        try:
            if index == 0:
                update(actor, tenant, UUID(note_id), body, expected=body.expected)
            else:
                apply(owner, [row], when + timedelta(days=1))
            return 200
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(race, range(2))) == [200, 409]
