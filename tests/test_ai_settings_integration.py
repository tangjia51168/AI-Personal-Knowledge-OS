"""Live settings/vault/RLS acceptance. Deliberately no real paid/local-model calls."""

import os
from uuid import uuid4

import httpx
import pytest
from app.core.config import get_settings
from sqlalchemy import create_engine, text

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated stack required"
    ),
]


def test_live_ai_settings_vault_roles_and_privacy():
    settings = get_settings()
    assert settings.app_env == "test" and settings.migration_database_url
    engine = create_engine(settings.migration_database_url.get_secret_value())
    accounts = []
    with httpx.Client(base_url=os.environ["API_BASE_URL"], timeout=20, trust_env=False) as client:
        try:
            for _ in range(2):
                response = client.post(
                    "/api/v1/auth/register",
                    json={
                        "email": f"{uuid4()}@example.invalid",
                        "password": "acceptance-only-password-2026",
                    },
                )
                assert response.status_code == 201, response.text
                accounts.append(response.json()["data"])
            a, b = accounts
            auth = {"Authorization": f"Bearer {a['access_token']}"}
            other = {"Authorization": f"Bearer {b['access_token']}"}
            path = f"/api/v1/spaces/{a['tenant_id']}"
            assert client.get(path + "/providers", headers=other).status_code == 404
            dummy_key = "acceptance-dummy-never-sent-to-a-model"
            config = {
                "kind": "openai",
                "model": "user-specified-unverified-model",
                "api_key": dummy_key,
            }
            response = client.post(path + "/providers", headers=auth, json=config)
            assert response.status_code == 201, response.text
            provider = response.json()["data"]
            assert provider["has_key"] and not provider["enabled"] and provider["adapter_verified"]
            assert dummy_key not in response.text and "encrypted_key" not in response.text
            with engine.begin() as connection:
                encrypted = connection.execute(
                    text("SELECT encrypted_key FROM ai_providers WHERE id=:id"),
                    {"id": provider["id"]},
                ).scalar_one()
                assert encrypted and dummy_key not in encrypted
            assert (
                client.post(
                    path + "/providers",
                    headers=auth,
                    json={**config, "enabled": True, "consent": True},
                ).status_code
                == 409
            )
            assert (
                client.post(
                    path + "/providers",
                    headers=auth,
                    json={
                        "kind": "ollama",
                        "model": "not-used",
                        "endpoint_name": "http://127.0.0.1:11434",
                    },
                ).status_code
                == 422
            )
            assert (
                client.post(
                    path + "/providers",
                    headers=auth,
                    json={"kind": "ollama", "model": "not-used", "endpoint_name": "not-authorized"},
                ).status_code
                == 400
            )
            assert (
                client.post(
                    path + "/ai-test", headers=auth, json={"prompt": "not sent", "consent": False}
                ).status_code
                == 400
            )
            assert (
                client.post(
                    path + "/ai-test", headers=auth, json={"prompt": "not sent", "consent": True}
                ).status_code
                == 409
            )
            assert client.get(path + "/ai-usage", headers=auth).json()["data"] == []
            assert (
                client.post(
                    path + f"/providers/{provider['id']}/rotate-encryption", headers=auth
                ).status_code
                == 200
            )
            assert (
                client.post(
                    path + "/settings",
                    headers=auth,
                    json={"theme": "dark", "allow_cloud_fallback": False},
                ).status_code
                == 200
            )
            assert client.get(path + "/settings", headers=auth).json()["data"]["theme"] == "dark"
            members = path + "/members"
            assert (
                client.post(members, headers=auth, json={"user_id": b["user_id"]}).status_code
                == 201
            )
            assert client.get(path + "/providers", headers=other).status_code == 403
            assert client.get(path + "/settings", headers=other).json()["data"]["theme"] == "system"
            assert (
                client.post(path + f"/providers/{provider['id']}/disable", headers=auth).json()[
                    "data"
                ]["has_key"]
                is False
            )
            with engine.begin() as connection:
                events = (
                    connection.execute(
                        text(
                            "SELECT action FROM audit_events "
                            "WHERE tenant_id=:tenant AND resource_id=:provider"
                        ),
                        {"tenant": a["tenant_id"], "provider": provider["id"]},
                    )
                    .scalars()
                    .all()
                )
                assert sorted(events) == [
                    "provider.created",
                    "provider.disabled",
                    "provider.reencrypted",
                ]
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
