"""Real PostgreSQL provider lifecycle, without inference or model network traffic.

Most updates use the running API. Enable/consent cases use the ASGI application
with process-only operator flags; they still commit to the dedicated real DB.
"""

import json
import os
from uuid import UUID, uuid4

import httpx
import pytest
from app.core.config import get_settings
from app.integrations.credentials import decrypt
from app.main import app
from app.modules.ai.service import reserve_attempt
from app.modules.identity.access import ActorIdentity, token_digest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated stack required"
    ),
]


@pytest.fixture
def provider_accounts():
    settings = get_settings()
    assert settings.app_env == "test" and settings.migration_database_url
    engine = create_engine(
        settings.migration_database_url.get_secret_value(),
        hide_parameters=True,
        connect_args={"connect_timeout": 3},
    )
    accounts = []
    with httpx.Client(base_url=os.environ["API_BASE_URL"], timeout=20, trust_env=False) as live:
        try:
            for _ in range(2):
                response = live.post(
                    "/api/v1/auth/register",
                    json={
                        "email": f"{uuid4()}@example.invalid",
                        "password": "provider-lifecycle-fixture-2026",
                    },
                )
                assert response.status_code == 201
                accounts.append(response.json()["data"])
            yield live, engine, accounts
        finally:
            # Delete only this fixture's rows, preserving Worker lock order.
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout='5s'"))
                for account in accounts:
                    params = {"tenant": account["tenant_id"], "user": account["user_id"]}
                    connection.execute(
                        text("SELECT id FROM tenants WHERE id=:tenant FOR UPDATE"), params
                    )
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
    return {"Authorization": f"Bearer {account['access_token']}"}


def set_test_operator(monkeypatch, **values):
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    # Only a public test encryption key; never load a user's platform key.
    monkeypatch.setenv(
        "CREDENTIAL_KEYS", '{"acceptance":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}'
    )
    monkeypatch.setenv("CREDENTIAL_ACTIVE_KEY", "acceptance")
    get_settings.cache_clear()


def test_live_update_at_limit_preserves_history_vault_and_admin_boundary(
    provider_accounts, monkeypatch
):
    live, engine, (owner, other) = provider_accounts
    path = f"/api/v1/spaces/{owner['tenant_id']}"
    config = {"kind": "deepseek", "model": "original-not-invoked", "api_key": "old-key-never-sent"}
    auth = headers(owner)
    providers = []
    for _ in range(5):
        response = live.post(path + "/providers", headers=auth, json=config)
        assert response.status_code == 201
        providers.append(response.json()["data"])
    assert live.post(path + "/providers", headers=auth, json=config).status_code == 429
    provider = providers[0]
    update_path = path + f"/providers/{provider['id']}/update"
    update = {"kind": "deepseek", "model": "replacement-not-invoked", "priority": 2}
    assert live.post(update_path, json=update).status_code == 401
    assert live.post(update_path, headers=headers(other), json=update).status_code == 404
    assert (
        live.post(
            f"/api/v1/spaces/{other['tenant_id']}/providers/{provider['id']}/update",
            headers=headers(other),
            json=update,
        ).status_code
        == 404
    )
    assert (
        live.post(
            path + "/members", headers=auth, json={"user_id": other["user_id"], "role": "member"}
        ).status_code
        == 201
    )
    assert live.post(update_path, headers=headers(other), json=update).status_code == 403

    params = {"id": provider["id"], "tenant": owner["tenant_id"]}
    with engine.begin() as connection:
        original = connection.execute(
            text("SELECT encrypted_key FROM ai_providers WHERE tenant_id=:tenant AND id=:id"),
            params,
        ).scalar_one()
    # Reserve through the real business phase. This is not a generated response.
    try:
        set_test_operator(monkeypatch, DEEPSEEK_ENABLED="true")
        with TestClient(app) as local:
            enabled = local.post(
                update_path,
                headers=auth,
                json={
                    "kind": "deepseek",
                    "model": config["model"],
                    "enabled": True,
                    "consent": True,
                },
            )
            assert enabled.status_code == 200
        usage_id = uuid4()
        invocation = reserve_attempt(
            ActorIdentity(UUID(owner["user_id"]), token_digest(owner["access_token"])),
            UUID(owner["tenant_id"]),
            UUID(provider["id"]),
            usage_id,
            "deepseek",
        )
        assert invocation is not None and invocation.model == config["model"]
        assert invocation.api_key.get_secret_value() == config["api_key"]
        response = live.post(update_path, headers=auth, json=update)
        assert response.status_code == 200
        assert response.json()["data"]["priority"] == 2
        assert response.json()["data"]["has_key"] and not response.json()["data"]["enabled"]
        with engine.begin() as connection:
            retained = connection.execute(
                text("SELECT encrypted_key FROM ai_providers WHERE tenant_id=:tenant AND id=:id"),
                params,
            ).scalar_one()
        assert retained == original
        replacement = "replacement-key-never-sent"
        response = live.post(update_path, headers=auth, json={**update, "api_key": replacement})
        assert response.status_code == 200 and replacement not in response.text
        assert "encrypted_key" not in response.text and config["api_key"] not in response.text
        with engine.begin() as connection:
            stored = connection.execute(
                text(
                    "SELECT key_version,encrypted_key FROM ai_providers "
                    "WHERE tenant_id=:tenant AND id=:id"
                ),
                params,
            ).one()
            assert stored.encrypted_key != original and replacement not in stored.encrypted_key
            assert (
                decrypt(
                    stored.key_version,
                    stored.encrypted_key,
                    UUID(owner["tenant_id"]),
                    UUID(provider["id"]),
                )
                == replacement
            )
            history = connection.execute(
                text("SELECT model,status,input_tokens,output_tokens FROM ai_usage WHERE id=:id"),
                {"id": usage_id},
            ).one()
            assert tuple(history) == (config["model"], "reserved", None, None)
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM audit_events WHERE tenant_id=:tenant "
                        "AND resource_id=:id AND action='provider.updated'"
                    ),
                    params,
                ).scalar_one()
                == 3
            )
        listed = live.get(path + "/providers", headers=auth).json()["data"]
        assert len(listed) == 5 and {row["id"] for row in listed} == {
            row["id"] for row in providers
        }
        assert (
            live.get(path + "/ai-usage", headers=auth).json()["data"][0]["model"] == config["model"]
        )
    finally:
        get_settings.cache_clear()


def test_real_db_update_requires_fresh_consent_operator_and_key_after_disable(
    provider_accounts, monkeypatch
):
    live, _engine, (owner, _other) = provider_accounts
    path = f"/api/v1/spaces/{owner['tenant_id']}"
    auth = headers(owner)
    config = {"kind": "deepseek", "model": "not-invoked", "api_key": "never-used-key-2026"}
    created = live.post(path + "/providers", headers=auth, json=config)
    assert created.status_code == 201
    provider_id = created.json()["data"]["id"]
    update_path = path + f"/providers/{provider_id}/update"
    update = {"kind": "deepseek", "model": "not-invoked", "enabled": True, "consent": True}
    try:
        set_test_operator(monkeypatch, DEEPSEEK_ENABLED="false")
        with TestClient(app) as local:
            assert local.post(update_path, headers=auth, json=update).status_code == 409
            assert (
                local.post(update_path, headers=auth, json={**update, "consent": False}).status_code
                == 400
            )
        monkeypatch.setenv("DEEPSEEK_ENABLED", "true")
        get_settings.cache_clear()
        with TestClient(app) as local:
            response = local.post(update_path, headers=auth, json=update)
            assert response.status_code == 200 and response.json()["data"]["enabled"]
            assert (
                local.post(path + f"/providers/{provider_id}/disable", headers=auth).status_code
                == 200
            )
            response = local.post(update_path, headers=auth, json=update)
            assert (
                response.status_code == 400
                and response.json()["error"]["code"] == "PROVIDER_KEY_REQUIRED"
            )
            assert (
                local.post(
                    update_path, headers=auth, json={**update, "api_key": "too-short"}
                ).status_code
                == 400
            )
            response = local.post(
                update_path, headers=auth, json={**update, "api_key": "new-key-never-sent-2026"}
            )
            assert response.status_code == 200 and response.json()["data"]["enabled"]
            assert response.json()["data"]["has_key"]
            assert (
                local.post(update_path, headers=auth, json={**update, "kind": "openai"}).status_code
                == 400
            )
            assert (
                local.post(
                    update_path, headers=auth, json={**update, "endpoint_name": "custom-cloud"}
                ).status_code
                == 400
            )
            assert (
                local.post(update_path, headers=auth, json={**update, "priority": 101}).status_code
                == 422
            )
            assert local.get(path + "/ai-usage", headers=auth).json()["data"] == []
            assert (
                local.post(path + f"/providers/{provider_id}/disable", headers=auth).json()["data"][
                    "has_key"
                ]
                is False
            )
    finally:
        get_settings.cache_clear()


def test_real_db_local_update_revalidates_named_endpoint(provider_accounts, monkeypatch):
    _live, _engine, (owner, _other) = provider_accounts
    path = f"/api/v1/spaces/{owner['tenant_id']}"
    auth = headers(owner)
    try:
        set_test_operator(
            monkeypatch,
            OLLAMA_ENDPOINTS=json.dumps(
                {"lifecycle-a": "http://127.0.0.1:9", "lifecycle-b": "http://127.0.0.1:9"}
            ),
        )
        with TestClient(app) as local:
            config = {"kind": "ollama", "model": "not-invoked", "endpoint_name": "lifecycle-a"}
            created = local.post(path + "/providers", headers=auth, json=config)
            assert created.status_code == 201
            provider_id = created.json()["data"]["id"]
            update_path = path + f"/providers/{provider_id}/update"
            update = {**config, "endpoint_name": "lifecycle-b", "enabled": True, "consent": True}
            response = local.post(update_path, headers=auth, json=update)
            assert (
                response.status_code == 200
                and response.json()["data"]["endpoint_name"] == "lifecycle-b"
            )
        monkeypatch.setenv("OLLAMA_ENDPOINTS", json.dumps({"lifecycle-a": "http://127.0.0.1:9"}))
        get_settings.cache_clear()
        with TestClient(app) as local:
            response = local.post(update_path, headers=auth, json=update)
            assert (
                response.status_code == 400
                and response.json()["error"]["code"] == "LOCAL_ENDPOINT_NOT_AUTHORIZED"
            )
            assert local.get(path + "/ai-usage", headers=auth).json()["data"] == []
    finally:
        get_settings.cache_clear()
