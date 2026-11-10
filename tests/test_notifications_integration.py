"""Actual PG/RLS, durable receipts and loopback SMTP; not external inbox delivery."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.db.session import get_engine
from app.integrations import mail
from app.modules.learning import notifications as n
from conftest import isolate_stack_ip_buckets
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_mail_transport import configure, sink
from test_organization_integration import auth, context, endpoint, identity, organization_accounts

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]


@pytest.fixture(scope="module")
def notification_accounts():
    yield from organization_accounts.__wrapped__()


@pytest.fixture(scope="module", autouse=True)
def isolated_notification_learning_fixture_bucket():
    # Full-stack earlier learning modules share the dedicated runner's IP.
    # Only its existing guarded fixture-write scope is isolated; no user bucket.
    yield from isolate_stack_ip_buckets(("learning_write",))


def preference(account, *, version=0, enabled=True, email=False):
    return n.save_preference(
        identity(account),
        UUID(account["tenant_id"]),
        n.PreferenceInput(expected_version=version, enabled=enabled, email=email, consent=enabled),
    )


def preference_id(admin, account):
    with admin.connect() as connection:
        return connection.execute(
            text("SELECT id FROM notification_preferences WHERE tenant_id=:t AND user_id=:u"),
            {"t": account["tenant_id"], "u": account["user_id"]},
        ).scalar_one()


def test_actual_inbox_occurrence_dedup_concurrency_and_member_rls(notification_accounts):
    live, admin, (owner, other) = notification_accounts
    path = endpoint(owner) + "/learning/notifications"
    assert live.get(path).status_code == 401
    assert live.get(path, headers=auth(other)).status_code == 404
    assert live.get(path + "?tenant_id=x", headers=auth(owner)).status_code == 422
    assert n.inbox(identity(owner), UUID(owner["tenant_id"]))["preference"]["version"] == 0
    assert preference(owner)["version"] == 1
    past = (datetime.now(UTC) - timedelta(minutes=3)).isoformat()
    for note in owner["notes"]:
        response = live.put(
            endpoint(owner) + f"/notes/{note['note_id']}/learning",
            headers=auth(owner),
            json={"status": "seen", "reminder_at": past},
        )
        assert response.status_code == 200
    pref = preference_id(admin, owner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(n.execute_due, [str(pref), str(pref)]))
    page = n.inbox(identity(owner), UUID(owner["tenant_id"]))
    assert len(page["data"]) == 2
    assert all(row["email_state"] == "not_requested" for row in page["data"])
    delivery = page["data"][0]
    read = live.post(path + f"/{delivery['id']}/read", headers=auth(owner), json={})
    assert read.status_code == 200 and read.json()["data"]["read_at"]
    assert (
        live.post(path + f"/{delivery['id']}/read", headers=auth(owner), json={}).json()
        == read.json()
    )
    assert (
        live.post(path + f"/{delivery['id']}/read", headers=auth(other), json={}).status_code == 404
    )
    # A new connection sees durable personal receipts, and no other member sees them.
    with get_engine().begin() as connection:
        context(connection, other)
        assert (
            connection.execute(
                text("SELECT count(*) FROM notification_deliveries WHERE tenant_id=:t"),
                {"t": owner["tenant_id"]},
            ).scalar_one()
            == 0
        )
    with get_engine().begin() as connection:
        context(connection, owner)
        for command in (
            "DELETE FROM notification_deliveries WHERE tenant_id=:t",
            "UPDATE notification_deliveries SET user_id=:u WHERE tenant_id=:t",
            "UPDATE notification_deliveries SET read_at=NULL "
            "WHERE tenant_id=:t AND read_at IS NOT NULL",
        ):
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(text(command), {"t": owner["tenant_id"], "u": other["user_id"]})
    with admin.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM audit_events "
                    "WHERE tenant_id=:t AND action='notification.created'"
                ),
                {"t": owner["tenant_id"]},
            ).scalar_one()
            == 2
        )


def test_actual_self_test_replay_quota_disable_and_atomic_audit(notification_accounts, monkeypatch):
    _live, admin, (_owner, other) = notification_accounts
    tenant, actor = UUID(other["tenant_id"]), identity(other)
    body = n.TestInput(idempotency_key=uuid4(), consent=True)
    with pytest.raises(HTTPException) as disabled:
        n.self_test(actor, tenant, body)
    assert disabled.value.status_code == 409
    preference(other)
    first = n.self_test(actor, tenant, body)
    assert n.self_test(actor, tenant, body) == first
    with monkeypatch.context() as patch:

        def reject(*args, **kwargs):
            raise RuntimeError("AUDIT_FIXTURE")

        patch.setattr(n, "record", reject)
        with pytest.raises(RuntimeError, match="AUDIT_FIXTURE"):
            n.self_test(actor, tenant, n.TestInput(idempotency_key=uuid4(), consent=True))
    assert len(n.inbox(actor, tenant)["data"]) == 1
    for _ in range(2):
        n.self_test(actor, tenant, n.TestInput(idempotency_key=uuid4(), consent=True))
    with pytest.raises(HTTPException) as quota:
        n.self_test(actor, tenant, n.TestInput(idempotency_key=uuid4(), consent=True))
    assert quota.value.status_code == 429
    assert n.self_test(actor, tenant, body)["id"] == first["id"]
    assert preference(other, version=1, enabled=False)["enabled"] is False
    n.execute_due(str(preference_id(admin, other)))
    assert len(n.inbox(actor, tenant)["data"]) == 3
    with pytest.raises(HTTPException) as conflict:
        preference(other, version=1)
    assert conflict.value.status_code == 409


def test_actual_smtp_durable_claim_unknown_no_retry_and_revalidation(monkeypatch):
    for live, admin, (owner, other) in organization_accounts.__wrapped__():
        tenant, actor = UUID(owner["tenant_id"]), identity(owner)
        with admin.begin() as connection:
            connection.execute(
                text("UPDATE users SET email_verified=true WHERE id=:u"), {"u": owner["user_id"]}
            )
        with sink() as server:
            configure(monkeypatch, server.server_address[1])
            # API business configuration remains real; only SMTP server selection
            # is a local sink in this test process, never an Internet recipient.
            original_settings = n.get_settings()
            local_settings = original_settings.model_copy(update={"smtp_host": "127.0.0.1"})
            monkeypatch.setattr(n, "get_settings", lambda settings=local_settings: settings)
            preference(owner, email=True)
            pref = preference_id(admin, owner)
            body = n.TestInput(idempotency_key=uuid4(), consent=True)
            receipt = n.self_test(actor, tenant, body)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(n.deliver_email, pref, UUID(receipt["id"])) for _ in range(2)
                ]
                for future in futures:
                    future.result()
            assert len(server.messages) == 1
            assert n.inbox(actor, tenant)["data"][0]["email_state"] == "smtp_accepted"
            assert n.self_test(actor, tenant, body)["email_state"] == "smtp_accepted"
            n.deliver_email(pref, UUID(receipt["id"]))
            assert len(server.messages) == 1
            # Failure after a durable SMTP claim stays uncertain, never retries.
            second = n.self_test(actor, tenant, n.TestInput(idempotency_key=uuid4(), consent=True))
            with monkeypatch.context() as patch:

                def unknown(*args, **kwargs):
                    raise mail.MailDeliveryError()

                patch.setattr(n, "send_learning_notification", unknown)
                n.deliver_email(pref, UUID(second["id"]))
            n.deliver_email(pref, UUID(second["id"]))
            assert len(server.messages) == 1
            assert n.inbox(actor, tenant)["data"][0]["email_state"] == "unknown"
            # Persisted dispatch left by a crash is finalized unknown without SMTP.
            third = n.self_test(actor, tenant, n.TestInput(idempotency_key=uuid4(), consent=True))
            with admin.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE notification_deliveries SET email_state='dispatched',"
                        "dispatched_at=now()-interval '3 minutes' WHERE id=:id"
                    ),
                    {"id": third["id"]},
                )
            n.execute_due(str(pref))
            assert len(server.messages) == 1
            assert n.inbox(actor, tenant)["data"][0]["email_state"] == "unknown"
            # An occurrence cancelled by a schedule edit is checked just before send.
            note = owner["notes"][0]
            past = datetime.now(UTC) - timedelta(minutes=5)
            response = live.put(
                endpoint(owner) + f"/notes/{note['note_id']}/learning",
                headers=auth(owner),
                json={"status": "seen", "reminder_at": past.isoformat()},
            )
            assert response.status_code == 200
            with n.authorized_transaction(actor, tenant) as (session, _role):
                learning = session.scalar(
                    n.select(n.NoteLearning).where(
                        n.NoteLearning.tenant_id == tenant,
                        n.NoteLearning.note_id == UUID(note["note_id"]),
                        n.NoteLearning.user_id == actor.user_id,
                    )
                )
                p = n.own(session, tenant, actor.user_id)
                cancelled = n.append(
                    session, p, "due:" + str(learning.id) + ":" + past.isoformat(), learning
                ).id
            response = live.put(
                endpoint(owner) + f"/notes/{note['note_id']}/learning",
                headers=auth(owner),
                json={"status": "deep_read"},
            )
            assert response.status_code == 200
            n.deliver_email(pref, cancelled)
            assert n.inbox(actor, tenant)["data"][0]["email_state"] == "cancelled"
            # Revoked persisted relationships never become authority via queue UUID.
            preference(other)
            other_pref = preference_id(admin, other)
            with admin.begin() as connection:
                connection.execute(
                    text("UPDATE tenant_members SET active=false WHERE tenant_id=:t"),
                    {"t": other["tenant_id"]},
                )
            n.execute_due(str(other_pref))
            with admin.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT enabled FROM notification_ticks WHERE preference_id=:p"),
                        {"p": other_pref},
                    ).scalar_one()
                    is False
                )
            assert len(server.messages) == 1


def test_independent_beat_worker_discovers_actual_due_and_disable_keeps_receipt():
    import time

    for live, admin, (owner, _other) in organization_accounts.__wrapped__():
        note = owner["notes"][0]
        past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        response = live.put(
            endpoint(owner) + f"/notes/{note['note_id']}/learning",
            headers=auth(owner),
            json={"status": "seen", "reminder_at": past},
        )
        assert response.status_code == 200
        preference(owner)
        deadline = time.monotonic() + 100
        while True:
            rows = live.get(
                endpoint(owner) + "/learning/notifications", headers=auth(owner)
            ).json()["data"]["data"]
            if rows:
                break
            assert time.monotonic() < deadline, (
                "Independent Beat/Worker did not deliver inbox receipt"
            )
            time.sleep(1)
        assert len(rows) == 1 and rows[0]["note_id"] == note["note_id"]
        assert rows[0]["email_state"] == "not_requested"
        preference(owner, version=1, enabled=False)
        n.execute_due(str(preference_id(admin, owner)))
        assert (
            live.get(endpoint(owner) + "/learning/notifications", headers=auth(owner)).json()[
                "data"
            ]["data"]
            == rows
        )
