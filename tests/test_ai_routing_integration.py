"""Real PostgreSQL authorization/quota phases; no inference or provider emulation."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import httpx
import pytest
from app.core.config import get_settings
from app.db.models import User
from app.db.session import get_engine
from app.integrations.ai import Generation
from app.modules.ai.models import AIProvider, AIUsage, UserSettings
from app.modules.ai.service import finish_attempt, provider_choices, reserve_attempt
from app.modules.identity.access import (
    Actor,
    ActorIdentity,
    authorized_transaction,
    lock_access,
    member_access,
    require_admin,
    set_user,
    token_digest,
)
from app.modules.jobs.routes import JobInput, create_job
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated stack required"
    ),
]


def test_real_ai_reservation_quota_and_role_revalidation(monkeypatch):
    settings = get_settings()
    assert settings.app_env == "test" and settings.migration_database_url
    engine = create_engine(settings.migration_database_url.get_secret_value(), hide_parameters=True)
    accounts = []
    with httpx.Client(base_url=os.environ["API_BASE_URL"], timeout=20, trust_env=False) as client:
        try:
            for _ in range(2):
                response = client.post(
                    "/api/v1/auth/register",
                    json={
                        "email": f"{uuid4()}@example.invalid",
                        "password": "acceptance-password-only-2026",
                    },
                )
                assert response.status_code == 201, response.text
                accounts.append(response.json()["data"])
            owner, invited = accounts
            tenant = UUID(owner["tenant_id"])
            path = f"/api/v1/spaces/{tenant}"
            auth = {"Authorization": f"Bearer {owner['access_token']}"}
            assert (
                client.post(
                    path + "/members",
                    headers=auth,
                    json={
                        "user_id": invited["user_id"],
                        "role": "admin",
                    },
                ).status_code
                == 201
            )
            identity = ActorIdentity(
                UUID(invited["user_id"]), token_digest(invited["access_token"])
            )
            provider_id = uuid4()
            with authorized_transaction(identity, tenant) as (session, role):
                assert role == "admin"
                session.add(
                    AIProvider(
                        id=provider_id,
                        tenant_id=tenant,
                        kind="ollama",
                        model="not-contacted",
                        endpoint_name="not-contacted",
                        priority=10,
                        enabled=True,
                        consent=True,
                    )
                )
            assert provider_choices(identity, tenant) == [(provider_id, "ollama")]
            # Real PostgreSQL also verifies the 0008 CHECK constraint and cloud
            # routing. No provider is contacted by these authorization phases.
            cloud_id = uuid4()
            with authorized_transaction(identity, tenant) as (session, _):
                session.add(
                    AIProvider(
                        id=cloud_id,
                        tenant_id=tenant,
                        kind="deepseek",
                        model="not-contacted",
                        priority=20,
                        enabled=True,
                        consent=True,
                    )
                )
            with monkeypatch.context() as patch:
                try:
                    patch.setenv("DEEPSEEK_ENABLED", "false")
                    get_settings.cache_clear()
                    assert provider_choices(identity, tenant) == [(provider_id, "ollama")]
                    assert reserve_attempt(identity, tenant, cloud_id, uuid4(), "deepseek") is None
                    patch.setenv("DEEPSEEK_ENABLED", "true")
                    get_settings.cache_clear()
                    assert provider_choices(identity, tenant) == [(provider_id, "ollama")]
                    assert reserve_attempt(identity, tenant, cloud_id, uuid4(), "ollama") is None
                    with authorized_transaction(identity, tenant) as (session, _):
                        session.add(
                            UserSettings(
                                tenant_id=tenant,
                                user_id=identity.user_id,
                                preferences={"allow_cloud_fallback": True},
                            )
                        )
                    assert provider_choices(identity, tenant) == [
                        (provider_id, "ollama"),
                        (cloud_id, "deepseek"),
                    ]
                    # Permission must still be checked again after selection.
                    with authorized_transaction(identity, tenant) as (session, _):
                        preference = session.get(UserSettings, (tenant, identity.user_id))
                        preference.preferences = {"allow_cloud_fallback": False}
                    assert reserve_attempt(identity, tenant, cloud_id, uuid4(), "ollama") is None
                finally:
                    get_settings.cache_clear()
            get_settings.cache_clear()
            # Keep a real application Session with the OLD role/membership loaded.
            # A later API mutation must refresh these cached ORM rows under locks.
            with Session(get_engine()) as stale, stale.begin():
                set_user(stale, identity.user_id)
                actor = Actor(stale, stale.get(User, identity.user_id), identity.token_hash)
                access = member_access(tenant, actor)
                assert access.role == "admin"
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE tenant_members SET role='member' "
                            "WHERE tenant_id=:tenant AND user_id=:user"
                        ),
                        {"tenant": tenant, "user": identity.user_id},
                    )
                lock_access(access)
                with pytest.raises(HTTPException) as stale_role:
                    require_admin(access)
                assert stale_role.value.status_code == 403
            # Permission changes after the initial configuration read must stop
            # the durable phase before quota reservation or any external I/O.
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE tenant_members SET role='member' "
                        "WHERE tenant_id=:tenant AND user_id=:user"
                    ),
                    {"tenant": tenant, "user": identity.user_id},
                )
            with pytest.raises(HTTPException) as denied:
                reserve_attempt(identity, tenant, provider_id, uuid4(), "ollama")
            assert denied.value.status_code == 403
            with engine.begin() as connection:
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM ai_usage WHERE tenant_id=:id"), {"id": tenant}
                    ).scalar_one()
                    == 0
                )
                connection.execute(
                    text(
                        "UPDATE tenant_members SET role='admin' "
                        "WHERE tenant_id=:tenant AND user_id=:user"
                    ),
                    {"tenant": tenant, "user": identity.user_id},
                )
            for _ in range(19):
                reserve_attempt(identity, tenant, provider_id, uuid4(), "ollama")

            def attempt():
                usage_id = uuid4()
                try:
                    reserve_attempt(identity, tenant, provider_id, usage_id, "ollama")
                    return usage_id
                except HTTPException as exc:
                    return exc.status_code

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: attempt(), range(2)))
            assert results.count(429) == 1
            usage_id = next(result for result in results if isinstance(result, UUID))
            finish_attempt(
                identity, tenant, usage_id, Generation("not-persisted-text", 2, 3), None, False
            )
            with authorized_transaction(identity, tenant) as (session, _):
                record = session.get(AIUsage, usage_id)
                assert record.status == "succeeded" and record.input_tokens == 2
                assert record.output_tokens == 3 and record.cost_usd is None
            with Session(get_engine()) as stale, stale.begin():
                set_user(stale, identity.user_id)
                actor = Actor(stale, stale.get(User, identity.user_id), identity.token_hash)
                access = member_access(tenant, actor)
                assert (
                    client.delete(path + f"/members/{identity.user_id}", headers=auth).status_code
                    == 200
                )
                with pytest.raises(HTTPException) as stale_write:
                    create_job(JobInput(idempotency_key=uuid4().hex), access)
                assert stale_write.value.status_code == 404
            with pytest.raises(HTTPException) as revoked:
                finish_attempt(identity, tenant, uuid4(), None, "not-written", False)
            assert revoked.value.status_code == 404
        finally:
            with engine.begin() as connection:
                for account in accounts:
                    params = {"tenant": account["tenant_id"], "user": account["user_id"]}
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
            engine.dispose()
