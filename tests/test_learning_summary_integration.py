"""Real aggregate counts and authorization on the isolated application-role stack."""

import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.db.session import get_engine
from app.modules.notes.edit_content import NoteEditInput
from app.modules.notes.edits import save_edit
from sqlalchemy import event, text
from test_organization_integration import auth, endpoint, identity
from test_organization_integration import organization_accounts as accounts_fixture

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]


@pytest.fixture(scope="module")
def summary_accounts():
    yield from accounts_fixture.__wrapped__()


@contextmanager
def count_application_aggregates():
    statements = []

    def observe(_conn, _cursor, statement, _params, _context, _many):
        if "LEFT OUTER JOIN note_learning" in statement:
            statements.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", observe)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", observe)


def test_summary_real_counts_include_unrecorded_and_exceed_list_cap(summary_accounts):
    live, admin, (owner, other) = summary_accounts
    url = endpoint(owner) + "/learning/summary"
    initial = live.get(url, headers=auth(owner))
    assert initial.status_code == 200
    data = initial.json()["data"]
    assert data["total_notes"] == 2 and data["unrecorded_notes"] == 2
    assert data["states"] == {"unseen": 2, "seen": 0, "skimmed": 0, "deep_read": 0}
    assert data["reminders"] == {"due": 0, "upcoming": 0}
    assert data["recorded_notes"] == 0
    assert datetime.fromisoformat(data["as_of"]).utcoffset() == timedelta(0)
    assert initial.headers["cache-control"] == "no-store"
    assert initial.headers["x-content-type-options"] == "nosniff"

    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    for note, state in zip(owner["notes"], ("seen", "deep_read"), strict=True):
        response = live.put(
            endpoint(owner) + f"/notes/{note['note_id']}/learning",
            headers=auth(owner),
            json={"status": state, "reminder_at": past},
        )
        assert response.status_code == 200
    # More than 100 rows in this disposable fixture, not a fake count response.
    # Use admin only for setup; all assertions read the real authorized API.
    with admin.begin() as connection:
        sources = (
            connection.execute(
                text(
                    "INSERT INTO sources(id,tenant_id,batch_id,original_url,canonical_url,"
                    "canonical_sha256,status) SELECT gen_random_uuid(),tenant_id,batch_id,"
                    "original_url||'/summary-'||g,canonical_url||'/summary-'||g,"
                    "encode(sha256(convert_to(canonical_url||'/summary-'||g,'UTF8')),'hex'),"
                    "'accepted' FROM sources CROSS JOIN generate_series(1,101) g "
                    "WHERE id=(SELECT source_id FROM notes WHERE id=:n) RETURNING id"
                ),
                {"n": owner["notes"][0]["note_id"]},
            )
            .scalars()
            .all()
        )
        for index, source in enumerate(sources):
            note_id = uuid4()
            connection.execute(
                text("INSERT INTO notes(id,tenant_id,source_id,created_by) VALUES(:n,:t,:s,:u)"),
                {"n": note_id, "t": owner["tenant_id"], "s": source, "u": owner["user_id"]},
            )
            if index == 100:
                continue
            connection.execute(
                text(
                    "INSERT INTO note_learning(id,tenant_id,note_id,user_id,status,reminder_at) "
                    "VALUES(:id,:t,:n,:u,:status,:reminder)"
                ),
                {
                    "id": uuid4(),
                    "t": owner["tenant_id"],
                    "n": note_id,
                    "u": owner["user_id"],
                    "status": "skimmed" if index == 0 else "unseen",
                    "reminder": datetime.now(UTC) + timedelta(days=1 if index == 0 else -1),
                },
            )

    # An additional immutable revision must not inflate the note denominator.
    note = owner["notes"][0]
    save_edit(
        identity(owner),
        UUID(owner["tenant_id"]),
        UUID(note["note_id"]),
        NoteEditInput(
            idempotency_key=uuid4(),
            base_revision_id=UUID(note["revision_id"]),
            title="学习总览协议修订，无模型调用",
            overview={"text": "人工概述", "evidence_keys": ["E001"]},
            sections=[
                {"kind": "steps", "items": [{"text": "准备材料", "evidence_keys": ["E001"]}]}
            ],
            personal_notes="仅数据库协议验证，不是学习成果",
        ),
    )

    def counts():
        with admin.connect() as connection:
            return [
                connection.execute(
                    text(f"SELECT count(*) FROM {table} WHERE tenant_id=:t"),
                    {"t": owner["tenant_id"]},
                ).scalar_one()
                for table in (
                    "note_learning",
                    "audit_events",
                    "note_revisions",
                    "processing_jobs",
                    "ai_usage",
                )
            ]

    before = counts()
    result = live.get(url, headers=auth(owner)).json()["data"]
    assert result["total_notes"] == 103
    assert result["recorded_notes"] == 102 and result["unrecorded_notes"] == 1
    assert result["states"] == {"unseen": 100, "seen": 1, "skimmed": 1, "deep_read": 1}
    assert result["reminders"] == {"due": 100, "upcoming": 1}
    assert counts() == before
    # Execute through the actual application role for a deterministic SQL bound.
    from app.modules.identity.access import authenticate_session, member_access
    from app.modules.learning.summary import summarize_learning
    from sqlalchemy.orm import Session

    with Session(get_engine()) as session, session.begin():
        actor = authenticate_session(session, owner["access_token"])
        access = member_access(UUID(owner["tenant_id"]), actor)
        with count_application_aggregates() as statements:
            assert summarize_learning(access).total_notes == 103
        assert len(statements) == 1
    assert (
        live.get(endpoint(other) + "/learning/summary", headers=auth(other)).json()["data"][
            "total_notes"
        ]
        == 1
    )


def test_summary_identity_membership_and_personal_isolation(summary_accounts):
    live, _admin, (owner, other) = summary_accounts
    url = endpoint(owner) + "/learning/summary"
    assert live.get(url).status_code == 401
    assert (
        live.get(url, headers={**auth(other), "X-Tenant-ID": owner["tenant_id"]}).status_code == 404
    )
    assert live.get(url + "?user_id=" + owner["user_id"], headers=auth(owner)).status_code == 422
    assert live.post(url, headers=auth(owner), json={}).status_code == 405
    members = endpoint(owner) + "/members"
    assert (
        live.post(
            members, headers=auth(owner), json={"user_id": other["user_id"], "role": "member"}
        ).status_code
        == 201
    )
    result = live.get(url, headers=auth(other)).json()["data"]
    assert result["total_notes"] == 103 and result["recorded_notes"] == 0
    assert result["unrecorded_notes"] == 103 and result["states"]["unseen"] == 103
    assert result["reminders"] == {"due": 0, "upcoming": 0}
    path = endpoint(owner) + f"/notes/{owner['notes'][0]['note_id']}/learning"
    assert live.put(path, headers=auth(other), json={"status": "skimmed"}).status_code == 200
    assert live.get(url, headers=auth(other)).json()["data"]["states"]["skimmed"] == 1
    assert live.get(url, headers=auth(owner)).json()["data"]["states"]["skimmed"] == 1
    assert live.delete(members + "/" + other["user_id"], headers=auth(owner)).status_code == 200
    assert live.get(url, headers=auth(other)).status_code == 404
    assert live.post("/api/v1/auth/logout", headers=auth(other)).status_code == 200
    assert live.get(url, headers=auth(other)).status_code == 401


def test_summary_empty_space_and_logout(summary_accounts):
    live, admin, (owner, _other) = summary_accounts
    # Reuse the real source fixture; an unrelated empty space is admin-created
    # only in this disposable tenant owner, then removed explicitly below.
    tenant = uuid4()
    with admin.begin() as connection:
        connection.execute(
            text("INSERT INTO tenants(id,name,status) VALUES(:t,'总览空态协议','active')"),
            {"t": tenant},
        )
        connection.execute(
            text(
                "INSERT INTO tenant_members(tenant_id,user_id,role,active) "
                "VALUES(:t,:u,'owner',true)"
            ),
            {"t": tenant, "u": owner["user_id"]},
        )
    try:
        response = live.get(f"/api/v1/spaces/{tenant}/learning/summary", headers=auth(owner))
        assert response.status_code == 200
        result = response.json()["data"]
        assert result["total_notes"] == result["recorded_notes"] == result["unrecorded_notes"] == 0
        assert sum(result["states"].values()) == sum(result["reminders"].values()) == 0
    finally:
        with admin.begin() as connection:
            connection.execute(text("DELETE FROM tenant_members WHERE tenant_id=:t"), {"t": tenant})
            connection.execute(text("DELETE FROM tenants WHERE id=:t"), {"t": tenant})
    assert live.post("/api/v1/auth/logout", headers=auth(owner)).status_code == 200
    assert live.get(endpoint(owner) + "/learning/summary", headers=auth(owner)).status_code == 401
