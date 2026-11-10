"""Personal review filtering through real app-role queries and isolated HTTP APIs."""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.db.session import get_engine
from app.modules.identity.access import authenticate_session, member_access
from app.modules.learning.content import LearningUpdate
from app.modules.learning.service import update
from app.modules.notes.listing import NoteListQuery, list_page
from sqlalchemy import event, text
from sqlalchemy.orm import Session
from test_organization_integration import auth, create, endpoint, identity, set_label
from test_organization_integration import organization_accounts as accounts_fixture

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]


@pytest.fixture(scope="module")
def review_accounts():
    yield from accounts_fixture.__wrapped__()


def test_personal_filters_combine_latest_note_labels_and_live_learning(review_accounts):
    live, _admin, (owner, _other) = review_accounts
    path = endpoint(owner) + "/notes"
    first, second = owner["notes"]

    def rows(**params):
        response = live.get(path, headers=auth(owner), params=params)
        assert response.status_code == 200
        return response.json()["data"]

    assert len(rows(reading_status="unseen")) == len(rows(reading_status="unrecorded")) == 2
    assert rows(review="due") == rows(review="upcoming") == []
    assert all(not row["learning_recorded"] and row["reminder_at"] is None for row in rows())
    label = create(owner, "复习清单协议分类")
    set_label(owner, first, label)

    def save(note, status, days):
        update(
            identity(owner),
            UUID(owner["tenant_id"]),
            UUID(note["note_id"]),
            LearningUpdate(status=status, reminder_at=datetime.now(UTC) + timedelta(days=days)),
        )

    save(first, "skimmed", -1)
    save(second, "unseen", 1)
    assert [row["id"] for row in rows(review="due")] == [first["note_id"]]
    assert [row["id"] for row in rows(review="upcoming")] == [second["note_id"]]
    assert rows(reading_status="unrecorded") == []
    assert rows(reading_status="seen") == []
    assert rows(reading_status="unrecorded", review="due") == []
    combined = rows(
        reading_status="skimmed",
        review="due",
        label_id=label["id"],
        q="分类协议0甲",
        platform="web",
        origin="ai_generated",
    )
    assert len(combined) == 1 and combined[0]["id"] == first["note_id"]
    assert combined[0]["reading_status"] == "skimmed" and combined[0]["learning_recorded"]
    assert combined[0]["reminder_at"]
    save(first, "deep_read", -1)
    assert rows(review="due") == []
    assert [row["id"] for row in rows(reading_status="deep_read")] == [first["note_id"]]
    save(first, "seen", 1)
    assert len(rows(review="upcoming")) == 2


def test_review_keyset_pages_and_invalidated_anchor_are_bounded_and_readonly(review_accounts):
    live, admin, (owner, _other) = review_accounts
    path = endpoint(owner) + "/notes"

    def counts():
        with admin.connect() as connection:
            return [
                connection.execute(
                    text(f"SELECT count(*) FROM {table} WHERE tenant_id=:t"),
                    {"t": owner["tenant_id"]},
                ).scalar_one()
                for table in ("note_learning", "audit_events", "note_revisions", "ai_usage")
            ]

    before = counts()
    first = live.get(path, headers=auth(owner), params={"review": "upcoming", "limit": 1}).json()
    assert len(first["data"]) == 1 and first["next_cursor"]
    anchor = first["next_cursor"]
    statements = []

    def observe(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().startswith("SELECT"):
            statements.append(statement)

    engine = get_engine()
    with Session(engine) as session, session.begin():
        access = member_access(
            UUID(owner["tenant_id"]), authenticate_session(session, owner["access_token"])
        )
        event.listen(engine, "before_cursor_execute", observe)
        try:
            second = list_page(
                access, NoteListQuery(review="upcoming", cursor=UUID(anchor), limit=1)
            )
        finally:
            event.remove(engine, "before_cursor_execute", observe)
    assert len(statements) == 2
    assert all("LIMIT" in statement for statement in statements[1:])
    assert len(second["data"]) == 1 and second["data"][0]["id"] != anchor
    assert second["next_cursor"] is None and counts() == before
    update(
        identity(owner),
        UUID(owner["tenant_id"]),
        UUID(anchor),
        LearningUpdate(status="deep_read"),
    )
    stale = live.get(path, headers=auth(owner), params={"review": "upcoming", "cursor": anchor})
    assert stale.status_code == 404 and stale.json()["error"]["code"] == "NOTE_CURSOR_NOT_FOUND"


def test_review_filters_reject_forgery_and_keep_members_personal(review_accounts):
    live, _admin, (owner, other) = review_accounts
    path = endpoint(owner) + "/notes"
    assert live.get(path + "?review=due").status_code == 401
    assert (
        live.get(path, headers={**auth(other), "X-Tenant-ID": owner["tenant_id"]}).status_code
        == 404
    )
    for params in (
        {"reading_status": "invalid"},
        {"review": "true"},
        {"user_id": other["user_id"]},
        {"tenant_id": other["tenant_id"]},
        {"limit": 51},
        {"cursor": "bad"},
    ):
        assert live.get(path, headers=auth(owner), params=params).status_code == 422
    members = endpoint(owner) + "/members"
    assert (
        live.post(
            members, headers=auth(owner), json={"user_id": other["user_id"], "role": "member"}
        ).status_code
        == 201
    )
    personal = live.get(path, headers=auth(other), params={"reading_status": "unrecorded"}).json()[
        "data"
    ]
    assert len(personal) == 2 and all(not row["learning_recorded"] for row in personal)
    assert live.get(path, headers=auth(other), params={"review": "upcoming"}).json()["data"] == []
    note = owner["notes"][0]["note_id"]
    assert (
        live.put(
            endpoint(owner) + f"/notes/{note}/learning",
            headers=auth(other),
            json={"status": "skimmed"},
        ).status_code
        == 200
    )
    assert (
        len(
            live.get(path, headers=auth(other), params={"reading_status": "skimmed"}).json()["data"]
        )
        == 1
    )
    assert (
        live.get(path, headers=auth(owner), params={"reading_status": "skimmed"}).json()["data"]
        == []
    )
    assert live.get(path, headers=auth(owner), params={"cursor": str(uuid4())}).status_code == 404
    assert live.delete(members + "/" + other["user_id"], headers=auth(owner)).status_code == 200
    assert live.get(path, headers=auth(other), params={"review": "due"}).status_code == 404
    assert live.post("/api/v1/auth/logout", headers=auth(other)).status_code == 200
    assert live.get(path, headers=auth(other)).status_code == 401
