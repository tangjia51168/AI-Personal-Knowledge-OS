"""Real audit transactions, administrator access and non-bypass PostgreSQL privileges."""

import os
from uuid import UUID, uuid4

import httpx
import pytest
from app.core.config import get_settings
from app.db.models import Tenant
from app.db.session import get_engine
from app.modules.audit.models import AuditAction, AuditEvent
from app.modules.audit.service import record
from app.modules.identity.access import set_tenant, set_user
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated test stack required"
    ),
]


def test_live_audit_atomicity_access_and_immutability():
    settings = get_settings()
    assert settings.app_env == "test" and settings.migration_database_url
    admin = create_engine(settings.migration_database_url.get_secret_value(), hide_parameters=True)
    accounts = []
    with httpx.Client(base_url=os.environ["API_BASE_URL"], timeout=20, trust_env=False) as client:
        try:
            for _ in range(2):
                response = client.post(
                    "/api/v1/auth/register",
                    json={
                        "email": f"{uuid4()}@example.invalid",
                        "password": "audit-acceptance-password-2026",
                    },
                )
                assert response.status_code == 201, response.text
                accounts.append(response.json()["data"])
            a, b = accounts
            tenant, actor = UUID(a["tenant_id"]), UUID(a["user_id"])
            auth = {"Authorization": f"Bearer {a['access_token']}"}
            other = {"Authorization": f"Bearer {b['access_token']}"}
            space = f"/api/v1/spaces/{tenant}"
            path = space + "/audit-events"
            assert client.get(path).status_code == 401
            assert client.get(path, headers=other).status_code == 404
            assert (
                client.get(path, headers={**other, "X-Tenant-ID": str(tenant)}).status_code == 404
            )
            assert client.get(path + "?limit=51", headers=auth).status_code == 422
            assert (
                client.post(
                    space + "/settings",
                    headers=auth,
                    json={"theme": "dark", "allow_cloud_fallback": False},
                ).status_code
                == 200
            )
            assert (
                client.post(
                    space + "/providers",
                    headers=auth,
                    json={
                        "kind": "openai",
                        "model": "unverified",
                        "api_key": "audit-secret-never-sent",
                    },
                ).status_code
                == 201
            )
            assert (
                client.post(
                    space + "/members",
                    headers=auth,
                    json={"user_id": b["user_id"], "role": "member"},
                ).status_code
                == 201
            )
            assert client.get(path, headers=other).status_code == 403
            first = client.get(path + "?limit=2", headers=auth)
            assert first.status_code == 200, first.text
            page = first.json()
            assert len(page["data"]) == 2 and page["next_cursor"]
            second = client.get(
                path, headers=auth, params={"limit": 2, "cursor": page["next_cursor"]}
            )
            assert second.status_code == 200, second.text
            rows = page["data"] + second.json()["data"]
            assert len({row["id"] for row in rows}) == 4
            assert {row["action"] for row in rows} == {
                "space.created",
                "settings.updated",
                "provider.created",
                "member.added",
            }
            for row in rows:
                assert set(row) == {"id", "actor_id", "action", "resource_id", "created_at"}
                assert row["actor_id"] == str(actor)
            assert "audit-secret" not in first.text + second.text
            foreign = client.get(
                f"/api/v1/spaces/{b['tenant_id']}/audit-events", headers=other
            ).json()["data"][0]["id"]
            assert client.get(path, headers=auth, params={"cursor": foreign}).status_code == 404
            assert (
                client.get(path, headers=auth, params={"cursor": "not-a-uuid"}).status_code == 422
            )

            # A business mutation and its audit insertion roll back together.
            marker = uuid4()
            with Session(get_engine()) as session:
                transaction = session.begin()
                set_user(session, actor)
                set_tenant(session, tenant)
                current = session.get(Tenant, tenant)
                original_name = current.name
                current.name = "must-roll-back"
                record(session, tenant, actor, AuditAction.SETTINGS_UPDATED, marker)
                session.flush()
                transaction.rollback()
            with admin.begin() as connection:
                assert (
                    connection.execute(
                        text("SELECT name FROM tenants WHERE id=:id"), {"id": tenant}
                    ).scalar_one()
                    == original_name
                )
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM audit_events WHERE resource_id=:id"),
                        {"id": marker},
                    ).scalar_one()
                    == 0
                )

            # Exercise RLS/ACL under the actual runtime role, including invalid
            # actor impersonation within the same tenant and forbidden modification.
            with Session(get_engine()) as session, session.begin():
                assert session.execute(text("SELECT current_user")).scalar_one() == "knowledge_app"
                assert list(session.scalars(select(AuditEvent))) == []
                set_user(session, actor)
                set_tenant(session, UUID(b["tenant_id"]))
                assert session.get(AuditEvent, UUID(rows[0]["id"])) is None
                set_tenant(session, tenant)
                for statement in (
                    "UPDATE audit_events SET action='space.created' WHERE tenant_id=:tenant",
                    "DELETE FROM audit_events WHERE tenant_id=:tenant",
                ):
                    with pytest.raises(DBAPIError) as denied, session.begin_nested():
                        session.execute(text(statement), {"tenant": tenant})
                    assert denied.value.orig.sqlstate == "42501"
                for event_tenant, event_actor in (
                    (UUID(b["tenant_id"]), UUID(b["user_id"])),
                    (tenant, UUID(b["user_id"])),
                ):
                    with pytest.raises(DBAPIError) as denied, session.begin_nested():
                        record(
                            session,
                            event_tenant,
                            event_actor,
                            AuditAction.SETTINGS_UPDATED,
                            uuid4(),
                        )
                        session.flush()
                    assert denied.value.orig.sqlstate == "42501"
                with pytest.raises(DBAPIError) as invalid, session.begin_nested():
                    session.execute(
                        text(
                            "INSERT INTO audit_events (id,tenant_id,actor_id,action,resource_id) "
                            "VALUES (:id,:tenant,:actor,'arbitrary-private-text',:resource)"
                        ),
                        {"id": uuid4(), "tenant": tenant, "actor": actor, "resource": uuid4()},
                    )
                assert invalid.value.orig.sqlstate == "23514"
            assert (
                client.delete(space + f"/members/{b['user_id']}", headers=auth).status_code == 200
            )
            assert client.get(path, headers=other).status_code == 404
        finally:
            with admin.begin() as connection:
                for account in accounts:
                    params = {"tenant": account["tenant_id"]}
                    for table in (
                        "audit_events",
                        "ai_usage",
                        "ai_providers",
                        "user_settings",
                        "tenant_members",
                    ):
                        connection.execute(
                            text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), params
                        )
                for account in accounts:
                    params = {"tenant": account["tenant_id"], "user": account["user_id"]}
                    for table in ("auth_security_events", "auth_sessions", "auth_identities"):
                        connection.execute(text(f"DELETE FROM {table} WHERE user_id=:user"), params)
                    connection.execute(text("DELETE FROM tenants WHERE id=:tenant"), params)
                    connection.execute(text("DELETE FROM users WHERE id=:user"), params)
            admin.dispose()
