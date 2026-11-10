"""Real identity/membership acceptance, only in the isolated test stack."""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from app.core.config import get_settings
from app.modules.identity.rate_limit import rate_limit
from fastapi import HTTPException, Request
from redis import Redis
from sqlalchemy import create_engine, text

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated stack opt-in required"
    ),
]


def test_live_auth_rate_limit_rejects_spoofed_forwarded_headers():
    subject = str(uuid4())
    request = Request({"type": "http", "client": (subject, 1234), "headers": []})
    try:
        for _ in range(15):
            rate_limit(request, "login", subject)
        request.scope["headers"] = [(b"x-forwarded-for", b"different-address")]
        with pytest.raises(HTTPException) as error:
            rate_limit(request, "login", subject)
        assert error.value.status_code == 429
    finally:
        with Redis.from_url(get_settings().redis_url.get_secret_value()) as client:
            for value in (f"ip:{subject}", f"identity:{subject}"):
                client.delete("auth-limit:login:" + hashlib.sha256(value.encode()).hexdigest())


def test_live_identity_membership_and_revocation():
    settings = get_settings()
    assert settings.app_env == "test" and settings.migration_database_url
    password = "acceptance-only-password-2026"
    users = []
    spaces = []
    with httpx.Client(base_url=os.environ["API_BASE_URL"], timeout=20, trust_env=False) as client:
        try:
            for _ in range(2):
                email = f"{uuid4()}@example.invalid"
                response = client.post(
                    "/api/v1/auth/register", json={"email": email, "password": password}
                )
                assert response.status_code == 201, response.text
                data = response.json()["data"]
                data["email"] = email
                users.append(data)
                spaces.append(data["tenant_id"])
            a, b = users
            auth_a = {"Authorization": f"Bearer {a['access_token']}"}
            auth_b = {"Authorization": f"Bearer {b['access_token']}"}
            profile = client.get("/api/v1/auth/me", headers=auth_a).json()["data"]
            assert profile["id"] == a["user_id"]
            assert profile["expires_at"] == a["expires_at"]
            assert "access_token" not in profile
            own = client.get("/api/v1/spaces", headers=auth_a).json()["data"]
            assert [space["id"] for space in own] == [a["tenant_id"]]
            assert client.get(f"/api/v1/spaces/{b['tenant_id']}", headers=auth_a).status_code == 404
            assert (
                client.get(
                    f"/api/v1/spaces/{b['tenant_id']}",
                    headers={**auth_a, "X-Tenant-ID": b["tenant_id"]},
                ).status_code
                == 404
            )
            path = f"/api/v1/spaces/{a['tenant_id']}/members"
            assert (
                client.post(
                    path, headers=auth_a, json={"user_id": b["user_id"], "role": "member"}
                ).status_code
                == 201
            )
            assert (
                client.get(f"/api/v1/spaces/{a['tenant_id']}", headers=auth_b).json()["data"][
                    "role"
                ]
                == "member"
            )
            assert (
                client.post(path, headers=auth_b, json={"user_id": str(uuid4())}).status_code == 403
            )
            assert client.delete(f"{path}/{b['user_id']}", headers=auth_a).status_code == 200
            assert client.get(f"/api/v1/spaces/{a['tenant_id']}", headers=auth_b).status_code == 404
            assert (
                client.post(
                    "/api/v1/auth/login",
                    json={"email": a["email"], "password": "incorrect-password"},
                ).status_code
                == 401
            )
            login = client.post(
                "/api/v1/auth/login", json={"email": a["email"], "password": password}
            )
            assert login.status_code == 200
            other_auth = {"Authorization": "Bearer " + login.json()["data"]["access_token"]}
            assert client.post("/api/v1/auth/logout", headers=other_auth).status_code == 200
            assert client.get("/api/v1/auth/me", headers=other_auth).status_code == 401
            engine = create_engine(settings.migration_database_url.get_secret_value())
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE auth_sessions SET expires_at=now()-interval '1 second' "
                        "WHERE user_id=:id"
                    ),
                    {"id": b["user_id"]},
                )
            assert client.get("/api/v1/auth/me", headers=auth_b).status_code == 401
            for _ in range(3):
                created = client.post("/api/v1/spaces", headers=auth_a, json={"name": "quota-test"})
                assert created.status_code == 201
                spaces.append(created.json()["data"]["id"])

            def create_at_limit(_):
                return client.post("/api/v1/spaces", headers=auth_a, json={"name": "quota-race"})

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(create_at_limit, range(2)))
            for created in results:
                if created.status_code == 201:
                    spaces.append(created.json()["data"]["id"])
            assert sorted(created.status_code for created in results) == [201, 429]
            assert (
                next(created for created in results if created.status_code == 429).json()["error"][
                    "code"
                ]
                == "SPACE_QUOTA_EXCEEDED"
            )
            assert client.post("/api/v1/auth/logout-all", headers=auth_a).status_code == 200
            assert client.get("/api/v1/auth/me", headers=auth_a).status_code == 401
            engine.dispose()
        finally:
            # Only remove this test's newly created fixtures from the dedicated acceptance DB.
            engine = create_engine(settings.migration_database_url.get_secret_value())
            with engine.begin() as connection:
                for tenant_id in spaces:
                    connection.execute(
                        text("DELETE FROM audit_events WHERE tenant_id=:id"), {"id": tenant_id}
                    )
                for user in users:
                    for table in (
                        "auth_security_events",
                        "auth_sessions",
                        "auth_identities",
                        "tenant_members",
                    ):
                        connection.execute(
                            text(f"DELETE FROM {table} WHERE user_id=:id"), {"id": user["user_id"]}
                        )
                for tenant_id in spaces:
                    connection.execute(text("DELETE FROM tenants WHERE id=:id"), {"id": tenant_id})
                for user in users:
                    connection.execute(
                        text("DELETE FROM users WHERE id=:id"), {"id": user["user_id"]}
                    )
            engine.dispose()
