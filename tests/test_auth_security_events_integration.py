"""Real account history, durable rejection observations and PostgreSQL RLS."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import httpx
import pytest
from app.core.config import get_settings
from app.db.models import User
from app.db.session import get_engine
from app.modules.identity.access import set_user
from app.modules.identity.recovery import consume_action, create_action, locked_user
from app.modules.identity.security_events import (
    SecurityAction,
    record_security_event,
)
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]
PASSWORD = "security-history-fixture-password-2026"
WINDOW_SQL = "SELECT date_bin(interval '15 minutes',now(),timestamptz '2000-01-01 00:00:00+00')"


@pytest.fixture
def accounts():
    settings = get_settings()
    assert settings.app_env == "test" and settings.migration_database_url
    engine = create_engine(settings.migration_database_url.get_secret_value(), hide_parameters=True)
    fixtures = []
    with httpx.Client(base_url=os.environ["API_BASE_URL"], timeout=20, trust_env=False) as client:
        try:
            for _ in range(2):
                email = f"{uuid4()}@example.invalid"
                response = client.post(
                    "/api/v1/auth/register", json={"email": email, "password": PASSWORD}
                )
                assert response.status_code == 201
                fixtures.append({**response.json()["data"], "email": email})
            yield client, engine, fixtures
        finally:
            with engine.begin() as connection:
                for account in fixtures:
                    params = {"user": account["user_id"], "tenant": account["tenant_id"]}
                    connection.execute(
                        text("SELECT id FROM tenants WHERE id=:tenant FOR UPDATE"), params
                    )
                    connection.execute(
                        text("DELETE FROM audit_events WHERE tenant_id=:tenant"), params
                    )
                    connection.execute(
                        text("DELETE FROM tenant_members WHERE tenant_id=:tenant"), params
                    )
                    for table in (
                        "auth_security_events",
                        "auth_action_tokens",
                        "auth_sessions",
                        "auth_identities",
                    ):
                        connection.execute(text(f"DELETE FROM {table} WHERE user_id=:user"), params)
                    connection.execute(text("DELETE FROM tenants WHERE id=:tenant"), params)
                    connection.execute(text("DELETE FROM users WHERE id=:user"), params)
            engine.dispose()


def headers(account):
    return {"Authorization": "Bearer " + account["access_token"]}


def test_live_security_history_lifecycle_and_single_use_recovery(accounts):
    client, admin, (account, _) = accounts
    endpoint = "/api/v1/auth/security-events"
    assert client.get(endpoint).status_code == 401
    result = client.get(endpoint, headers=headers(account))
    assert result.status_code == 200
    assert [row["action"] for row in result.json()["data"]] == ["account.registered"]
    assert set(result.json()["data"][0]) == {"id", "action", "created_at"}
    login = client.post(
        "/api/v1/auth/login", json={"email": account["email"], "password": PASSWORD}
    )
    assert login.status_code == 200
    new_auth = {"Authorization": "Bearer " + login.json()["data"]["access_token"]}
    assert client.post("/api/v1/auth/logout", headers=new_auth).status_code == 200
    assert client.get(endpoint, headers=new_auth).status_code == 401
    user_id = UUID(account["user_id"])
    with Session(get_engine()) as session, session.begin():
        user = locked_user(session, user_id)
        verification = create_action(session, user, "verify_email")
    consume_action(verification, "verify_email")
    with Session(get_engine()) as session, session.begin():
        user = locked_user(session, user_id)
        reset = create_action(session, user, "reset_password")
    replacement = "security-history-replaced-password-2026"
    consume_action(reset, "reset_password", replacement)
    assert client.get(endpoint, headers=headers(account)).status_code == 401
    login = client.post(
        "/api/v1/auth/login", json={"email": account["email"], "password": replacement}
    )
    assert login.status_code == 200
    final_auth = {"Authorization": "Bearer " + login.json()["data"]["access_token"]}
    rows = client.get(endpoint, headers=final_auth).json()["data"]
    assert {row["action"] for row in rows} >= {
        "account.registered",
        "login.succeeded",
        "session.logged_out",
        "email.verified",
        "password.reset",
    }
    assert client.post("/api/v1/auth/logout-all", headers=final_auth).status_code == 200
    with admin.begin() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM auth_security_events WHERE user_id=:user "
                    "AND action='session.all_revoked'"
                ),
                {"user": user_id},
            ).scalar_one()
            == 1
        )


def test_live_login_rejection_survives_response_and_is_bounded(accounts):
    client, admin, (account, other) = accounts
    endpoint = "/api/v1/auth/security-events"
    with admin.begin() as connection:
        first_window = connection.execute(text(WINDOW_SQL)).scalar_one()

    def rejected(_):
        return client.post(
            "/api/v1/auth/login",
            json={"email": account["email"], "password": "incorrect-password-2026"},
        ).status_code

    with ThreadPoolExecutor(max_workers=3) as pool:
        assert list(pool.map(rejected, range(3))) == [401, 401, 401]
    with admin.begin() as connection:
        last_window = connection.execute(text(WINDOW_SQL)).scalar_one()
        failures = connection.execute(
            text(
                "SELECT id,bucket_start FROM auth_security_events "
                "WHERE user_id=:user AND action='login.failed'"
            ),
            {"user": account["user_id"]},
        ).all()
        # Real requests can straddle a database window boundary. Every observed
        # bucket must be unique and belong to the before/after database windows.
        buckets = [row.bucket_start for row in failures]
        assert buckets and len(buckets) == len(set(buckets))
        assert set(buckets) <= {first_window, last_window}
    first = client.get(endpoint, headers=headers(account), params={"limit": 1}).json()
    assert len(first["data"]) == 1 and first["next_cursor"]
    second = client.get(
        endpoint, headers=headers(account), params={"cursor": first["next_cursor"], "limit": 1}
    ).json()
    assert first["data"][0]["id"] != second["data"][0]["id"]
    assert client.get(endpoint, headers=headers(account), params={"limit": 51}).status_code == 422
    assert (
        client.get(
            endpoint, headers=headers(other), params={"cursor": str(failures[0].id)}
        ).status_code
        == 404
    )
    with admin.begin() as connection:
        first_window = connection.execute(text(WINDOW_SQL)).scalar_one()
    for _ in range(2):
        rejected_unknown = client.post(
            "/api/v1/auth/login",
            json={"email": f"{uuid4()}@example.invalid", "password": PASSWORD},
        )
        assert rejected_unknown.status_code == 401
        assert rejected_unknown.json()["error"]["code"] == "INVALID_CREDENTIALS"
    with admin.begin() as connection:
        last_window = connection.execute(text(WINDOW_SQL)).scalar_one()
        buckets = (
            connection.execute(
                text(
                    "SELECT bucket_start FROM auth_security_events WHERE user_id IS NULL "
                    "AND action='login.failed' AND bucket_start BETWEEN :first AND :last"
                ),
                {"first": first_window, "last": last_window},
            )
            .scalars()
            .all()
        )
        assert buckets and len(buckets) == len(set(buckets))
        assert set(buckets) <= {first_window, last_window}
    result = client.get(endpoint, headers=headers(account)).text
    assert account["email"] not in result and PASSWORD not in result


def test_live_security_event_rls_immutability_and_rollback(accounts):
    _, admin, (account, other) = accounts
    user_id, other_id = UUID(account["user_id"]), UUID(other["user_id"])
    with Session(get_engine()) as session:
        with pytest.raises(RuntimeError, match="rollback fixture"), session.begin():
            set_user(session, user_id)
            user = session.get(User, user_id)
            user.email_verified = True
            record_security_event(session, user_id, SecurityAction.EMAIL_VERIFIED)
            session.flush()
            raise RuntimeError("rollback fixture")
    with admin.begin() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM auth_security_events WHERE user_id=:user "
                    "AND action='email.verified'"
                ),
                {"user": user_id},
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text("SELECT email_verified FROM users WHERE id=:user"), {"user": user_id}
            ).scalar_one()
            is False
        )
    with Session(get_engine()) as session, session.begin():
        assert not list(session.execute(text("SELECT id FROM auth_security_events")))
        set_user(session, user_id)
        own = session.execute(text("SELECT user_id FROM auth_security_events")).scalars().all()
        assert own and set(own) == {user_id}
        for statement in (
            "UPDATE auth_security_events SET action='login.succeeded' WHERE user_id=:user",
            "DELETE FROM auth_security_events WHERE user_id=:user",
        ):
            with pytest.raises(DBAPIError) as denied, session.begin_nested():
                session.execute(text(statement), {"user": user_id})
            assert denied.value.orig.sqlstate == "42501"
        with pytest.raises(DBAPIError) as forged, session.begin_nested():
            record_security_event(session, other_id, SecurityAction.LOGIN_SUCCEEDED)
            session.flush()
        assert forged.value.orig.sqlstate == "42501"
        assert session.scalars(select(User.id)).all() == [user_id]
