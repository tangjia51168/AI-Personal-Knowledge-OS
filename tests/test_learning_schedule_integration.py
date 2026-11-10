"""Real personal agenda queries, calendar exports and tenant/member isolation."""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.db.session import get_engine
from app.modules.identity.access import authenticate_session, member_access
from app.modules.learning.content import LearningUpdate
from app.modules.learning.schedule import ScheduleQuery, read_schedule
from app.modules.learning.service import update
from sqlalchemy import event, text
from sqlalchemy.orm import Session
from test_organization_integration import auth, endpoint, identity
from test_organization_integration import organization_accounts as accounts_fixture

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]


@pytest.fixture(scope="module")
def schedule_accounts():
    yield from accounts_fixture.__wrapped__()


def save(account, note, status, when):
    update(
        identity(account),
        UUID(account["tenant_id"]),
        UUID(note["note_id"]),
        LearningUpdate(status=status, reminder_at=when),
    )


def test_schedule_orders_real_reminders_by_time_and_downloads_inert_calendar(schedule_accounts):
    live, admin, (owner, _other) = schedule_accounts
    root = endpoint(owner) + "/learning"
    assert live.get(root + "/schedule", headers=auth(owner)).json()["data"]["items"] == []
    now = datetime.now(UTC)
    first, second = owner["notes"]
    save(owner, first, "skimmed", now + timedelta(days=4))
    save(owner, second, "seen", now + timedelta(days=2))
    result = live.get(root + "/schedule?period=7", headers=auth(owner))
    assert result.status_code == 200
    data = result.json()["data"]
    assert data["period"] == "7" and data["limit"] == 100 and not data["truncated"]
    assert [row["note_id"] for row in data["items"]] == [second["note_id"], first["note_id"]]
    assert data["items"][0]["title"].endswith("分类协议0乙")
    assert datetime.fromisoformat(data["until"]) - datetime.fromisoformat(
        data["as_of"]
    ) == timedelta(days=7)
    assert result.headers["cache-control"] == "no-store"
    with admin.connect() as connection:
        before = [
            connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:t"), {"t": owner["tenant_id"]}
            ).scalar_one()
            for table in ("note_learning", "audit_events", "note_revisions", "ai_usage")
        ]
    calendar = live.get(root + "/calendar?period=7", headers=auth(owner))
    assert calendar.status_code == 200 and calendar.headers["content-type"].startswith(
        "text/calendar"
    )
    assert calendar.headers["content-disposition"] == 'attachment; filename="knowledge-review.ics"'
    assert calendar.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in calendar.headers["content-security-policy"]
    unfolded = calendar.text.replace("\r\n ", "")
    assert unfolded.count("BEGIN:VEVENT") == 2 and "分类协议0甲" in unfolded
    assert "BEGIN:VALARM" not in unfolded and "Authorization" not in unfolded
    with admin.connect() as connection:
        after = [
            connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:t"), {"t": owner["tenant_id"]}
            ).scalar_one()
            for table in ("note_learning", "audit_events", "note_revisions", "ai_usage")
        ]
    assert before == after
    save(owner, first, "deep_read", now + timedelta(days=4))
    save(owner, second, "seen", now - timedelta(days=1))
    assert live.get(root + "/schedule?period=90", headers=auth(owner)).json()["data"]["items"] == []
    due = live.get(root + "/schedule?period=due", headers=auth(owner)).json()["data"]["items"]
    assert [row["note_id"] for row in due] == [second["note_id"]]
    save(owner, second, "seen", now + timedelta(days=20))
    assert live.get(root + "/schedule?period=7", headers=auth(owner)).json()["data"]["items"] == []
    assert (
        len(live.get(root + "/schedule?period=30", headers=auth(owner)).json()["data"]["items"])
        == 1
    )
    save(owner, second, "seen", None)
    assert live.get(root + "/schedule?period=90", headers=auth(owner)).json()["data"]["items"] == []


def test_schedule_single_bounded_query_and_truncation_never_exports_partial_scope(
    schedule_accounts,
):
    live, admin, (owner, _other) = schedule_accounts
    root = endpoint(owner) + "/learning"
    # Bulk setup only in this disposable tenant, without generation/API quota changes.
    # Assertions below use the real application role, authorized HTTP and RLS.
    with admin.begin() as connection:
        for index in range(99):
            params = {
                "s": uuid4(),
                "j": uuid4(),
                "snap": uuid4(),
                "run": uuid4(),
                "note": uuid4(),
                "rev": uuid4(),
                "learning": uuid4(),
                "key": uuid4(),
                "t": owner["tenant_id"],
                "u": owner["user_id"],
                "template": owner["notes"][0]["note_id"],
                "suffix": f"/agenda-{index}",
                "when": datetime.now(UTC) + timedelta(days=1),
            }
            connection.execute(
                text(
                    "INSERT INTO sources(id,tenant_id,batch_id,original_url,canonical_url,"
                    "canonical_sha256,status) SELECT :s,tenant_id,batch_id,original_url||:suffix,"
                    "canonical_url||:suffix,encode(sha256(convert_to(canonical_url||:suffix,'UTF8')),"
                    "'hex'),'accepted' FROM sources WHERE id=(SELECT source_id FROM notes "
                    "WHERE id=:template AND tenant_id=:t)"
                ),
                params,
            )
            connection.execute(
                text(
                    "INSERT INTO processing_jobs(id,tenant_id,requested_by,kind,status,progress,"
                    "idempotency_key,payload) "
                    "VALUES(:j,:t,:u,'source_import','succeeded',100,:key,'{}')"
                ),
                params,
            )
            connection.execute(
                text(
                    "INSERT INTO source_snapshots(id,tenant_id,source_id,job_id,created_by,version,"
                    "fetched_url,platform,content_kind,title,author,body_text,raw_sha256,details,"
                    "limitations,provenance) SELECT :snap,tenant_id,:s,:j,:u,1,"
                    "fetched_url,platform,"
                    "content_kind,title,author,body_text,raw_sha256,details,limitations,provenance "
                    "FROM source_snapshots WHERE id=(SELECT snapshot_id FROM note_revisions "
                    "WHERE note_id=:template AND tenant_id=:t ORDER BY version DESC LIMIT 1)"
                ),
                params,
            )
            connection.execute(
                text(
                    "INSERT INTO note_runs(id,tenant_id,snapshot_id,created_by,"
                    "idempotency_key,status) "
                    "VALUES(:run,:t,:snap,:u,:key,'succeeded')"
                ),
                params,
            )
            connection.execute(
                text("INSERT INTO notes(id,tenant_id,source_id,created_by) VALUES(:note,:t,:s,:u)"),
                params,
            )
            connection.execute(
                text(
                    "INSERT INTO note_revisions(id,tenant_id,note_id,snapshot_id,run_id,created_by,"
                    "version,title,content_md,document) SELECT :rev,tenant_id,"
                    ":note,:snap,:run,:u,1,"
                    "title,content_md,document FROM note_revisions WHERE note_id=:template "
                    "AND tenant_id=:t ORDER BY version DESC LIMIT 1"
                ),
                params,
            )
            connection.execute(
                text(
                    "INSERT INTO note_learning(id,tenant_id,note_id,user_id,status,reminder_at) "
                    "VALUES(:learning,:t,:note,:u,'unseen',:when)"
                ),
                params,
            )
    for note in owner["notes"]:
        save(owner, note, "skimmed", datetime.now(UTC) + timedelta(days=1))
    statements = []

    def observe(_conn, _cursor, statement, _params, _context, _many):
        if "JOIN note_revisions" in statement:
            statements.append(statement)

    engine = get_engine()
    with Session(engine) as session, session.begin():
        access = member_access(
            UUID(owner["tenant_id"]), authenticate_session(session, owner["access_token"])
        )
        event.listen(engine, "before_cursor_execute", observe)
        try:
            result = read_schedule(access, ScheduleQuery())
        finally:
            event.remove(engine, "before_cursor_execute", observe)
    assert len(statements) == 1 and "LIMIT" in statements[0]
    assert len(result.items) == 100 and result.truncated
    response = live.get(root + "/calendar?period=7", headers=auth(owner))
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "LEARNING_CALENDAR_TRUNCATED"
    )
    assert live.get(root + "/calendar?period=due", headers=auth(owner)).status_code == 200


def test_schedule_identity_personal_records_revoke_and_logout(schedule_accounts):
    live, _admin, (owner, other) = schedule_accounts
    root = endpoint(owner) + "/learning"
    for suffix in ("schedule", "calendar"):
        path = root + "/" + suffix
        assert live.get(path).status_code == 401
        assert (
            live.get(path, headers={**auth(other), "X-Tenant-ID": owner["tenant_id"]}).status_code
            == 404
        )
        for query in (
            "period=365",
            "user_id=" + other["user_id"],
            "tenant_id=" + other["tenant_id"],
        ):
            assert live.get(path + "?" + query, headers=auth(owner)).status_code == 422
        assert live.post(path, headers=auth(owner), json={}).status_code == 405
    members = endpoint(owner) + "/members"
    assert (
        live.post(
            members, headers=auth(owner), json={"user_id": other["user_id"], "role": "member"}
        ).status_code
        == 201
    )
    assert live.get(root + "/schedule", headers=auth(other)).json()["data"]["items"] == []
    assert "BEGIN:VEVENT" not in live.get(root + "/calendar", headers=auth(other)).text
    save(
        {**other, "tenant_id": owner["tenant_id"]},
        owner["notes"][0],
        "seen",
        datetime.now(UTC) + timedelta(days=1),
    )
    assert len(live.get(root + "/schedule", headers=auth(other)).json()["data"]["items"]) == 1
    assert live.delete(members + "/" + other["user_id"], headers=auth(owner)).status_code == 200
    assert live.get(root + "/schedule", headers=auth(other)).status_code == 404
    assert live.get(root + "/calendar", headers=auth(other)).status_code == 404
    assert live.post("/api/v1/auth/logout", headers=auth(other)).status_code == 200
    assert live.get(root + "/calendar", headers=auth(other)).status_code == 401
