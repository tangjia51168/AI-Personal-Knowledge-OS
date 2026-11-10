"""Real read-only audit snapshots preserve account, tenant and role boundaries."""

import os
from uuid import UUID, uuid4

import httpx
import pytest
from app.core.config import get_settings
from app.db.session import get_engine
from app.modules.audit import export as audit_export
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]
PASSWORD = "audit-export-fixture-password-2026"


@pytest.fixture
def export_accounts():
    settings = get_settings()
    assert settings.app_env == "test" and settings.migration_database_url
    admin = create_engine(settings.migration_database_url.get_secret_value(), hide_parameters=True)
    accounts = []
    with httpx.Client(base_url=os.environ["API_BASE_URL"], timeout=20, trust_env=False) as client:
        try:
            for _ in range(2):
                email = f"{uuid4()}@example.invalid"
                response = client.post(
                    "/api/v1/auth/register", json={"email": email, "password": PASSWORD}
                )
                assert response.status_code == 201
                accounts.append({**response.json()["data"], "email": email})
            yield client, admin, accounts
        finally:
            with admin.begin() as connection:
                for account in accounts:
                    params = {"tenant": account["tenant_id"], "user": account["user_id"]}
                    connection.execute(
                        text("SELECT id FROM tenants WHERE id=:tenant FOR UPDATE"), params
                    )
                    for table in ("audit_events", "user_settings", "tenant_members"):
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


def snapshot(account, tenant_id=None):
    with Session(get_engine()) as session, session.begin():
        return audit_export.audit_snapshot(
            session, account["access_token"], UUID(tenant_id or account["tenant_id"])
        )


def expect_http_rejection(account, tenant_id, expected):
    with pytest.raises(HTTPException) as rejected:
        snapshot(account, tenant_id)
    assert rejected.value.status_code == expected


def auth(account):
    return {"Authorization": "Bearer " + account["access_token"]}


def test_live_audit_export_uses_one_snapshot_and_rechecks_session_and_membership(export_accounts):
    client, _, (owner, other) = export_accounts
    foreign = snapshot(other)
    with Session(get_engine()) as session, session.begin():
        exported = audit_export.audit_snapshot(
            session, owner["access_token"], UUID(owner["tenant_id"])
        )
        assert exported["format"] == 1
        assert exported["scope"] == "space-business-and-own-account"
        assert exported["consistency"] == "postgresql-repeatable-read"
        assert exported["tenant_id"] == owner["tenant_id"]
        assert exported["user_id"] == owner["user_id"]
        assert exported["snapshot_at"].endswith("+00:00")
        assert len(exported["business_events"]) == len(exported["account_events"]) == 1
        business, account = exported["business_events"][0], exported["account_events"][0]
        assert set(business) == {"id", "actor_id", "action", "resource_id", "created_at"}
        assert set(account) == {"id", "action", "created_at"}
        assert business["action"] == "space.created"
        assert business["actor_id"] == owner["user_id"]
        assert business["resource_id"] == owner["tenant_id"]
        assert account["action"] == "account.registered"
        assert business["id"] != foreign["business_events"][0]["id"]
        assert account["id"] != foreign["account_events"][0]["id"]
        assert owner["email"] not in str(exported) and PASSWORD not in str(exported)
        assert session.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        assert session.execute(text("SHOW transaction_isolation")).scalar_one() == "repeatable read"
        # A committed event in a separate real API transaction must stay outside
        # this snapshot; later exports must observe it.
        response = client.post(
            f"/api/v1/spaces/{owner['tenant_id']}/settings",
            headers=auth(owner),
            json={"theme": "dark", "allow_cloud_fallback": False},
        )
        assert response.status_code == 200
        assert session.execute(text("SELECT count(*) FROM audit_events")).scalar_one() == 1
    assert len(snapshot(owner)["business_events"]) == 2
    expect_http_rejection(other, owner["tenant_id"], 404)
    members = f"/api/v1/spaces/{owner['tenant_id']}/members"
    assert (
        client.post(
            members, headers=auth(owner), json={"user_id": other["user_id"], "role": "member"}
        ).status_code
        == 201
    )
    expect_http_rejection(other, owner["tenant_id"], 403)
    assert client.delete(members + "/" + other["user_id"], headers=auth(owner)).status_code == 200
    expect_http_rejection(other, owner["tenant_id"], 404)
    assert client.post("/api/v1/auth/logout", headers=auth(owner)).status_code == 200
    expect_http_rejection(owner, owner["tenant_id"], 401)


def test_live_audit_export_rejects_writes_privileged_login_and_overflow(
    export_accounts, monkeypatch
):
    _, admin, (owner, _) = export_accounts
    tenant = UUID(owner["tenant_id"])
    with Session(get_engine()) as session:
        session.begin()
        try:
            audit_export.audit_snapshot(session, owner["access_token"], tenant)
            with pytest.raises(DBAPIError) as readonly:
                session.execute(
                    text("UPDATE tenants SET name='forbidden-export-write' WHERE id=:id"),
                    {"id": tenant},
                )
            assert readonly.value.orig.sqlstate == "25006"
        finally:
            session.rollback()
    with Session(admin) as session, session.begin():
        with pytest.raises(RuntimeError, match="unprivileged database login"):
            audit_export.audit_snapshot(session, owner["access_token"], tenant)
    monkeypatch.setattr(audit_export, "MAX_EXPORT_ROWS", 1)
    with pytest.raises(RuntimeError, match="row limit exceeded"):
        snapshot(owner)
    monkeypatch.setattr(audit_export, "MAX_EXPORT_ROWS", 10_000)
    unchanged = snapshot(owner)
    assert len(unchanged["business_events"]) == len(unchanged["account_events"]) == 1
    with admin.begin() as connection:
        assert (
            connection.execute(
                text("SELECT name FROM tenants WHERE id=:id"), {"id": tenant}
            ).scalar_one()
            == "个人空间"
        )
