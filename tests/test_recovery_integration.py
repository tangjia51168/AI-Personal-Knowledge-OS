"""Live PostgreSQL/API identity actions and real local TCP SMTP delivery."""

import importlib.util
import os
import re
from concurrent.futures import ThreadPoolExecutor
from email import policy
from email.parser import BytesParser
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from app.core.config import get_settings
from app.db.session import get_engine
from app.main import app
from app.modules.identity.recovery import consume_action, request_action
from app.modules.identity.routes import Credentials, login
from app.modules.jobs.execution import worker_job
from conftest import isolate_stack_ip_buckets
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated stack required"
    ),
]


@pytest.fixture(scope="module", autouse=True)
def recovery_login_bucket():
    yield from isolate_stack_ip_buckets(("login",))


def test_live_email_actions_single_use_expiry_and_session_revocation(monkeypatch):
    settings = get_settings()
    assert settings.app_env == "test" and settings.migration_database_url
    engine = create_engine(settings.migration_database_url.get_secret_value(), hide_parameters=True)
    spec = importlib.util.spec_from_file_location(
        "smtp_fixture", Path(__file__).with_name("test_mail_transport.py")
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    accounts = []
    with (
        fixture.sink() as smtp,
        httpx.Client(base_url=os.environ["API_BASE_URL"], timeout=20, trust_env=False) as client,
    ):
        monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
        monkeypatch.setenv("SMTP_PORT", str(smtp.server_address[1]))
        monkeypatch.setenv("SMTP_SECURITY", "plain")
        monkeypatch.setenv("SMTP_FROM", "account@example.invalid")
        monkeypatch.setenv("PUBLIC_APP_URL", "http://localhost:3000")
        get_settings.cache_clear()
        try:
            password = "recovery-original-password-2026"
            email = f"{uuid4()}@example.invalid"
            response = client.post(
                "/api/v1/auth/register", json={"email": email, "password": password}
            )
            assert response.status_code == 201, response.text
            account = response.json()["data"]
            accounts.append(account)
            auth = {"Authorization": "Bearer " + account["access_token"]}

            def last_token(purpose):
                message = BytesParser(policy=policy.default).parsebytes(smtp.messages[-1])
                match = re.search(r"#(?:verify|reset)=([A-Za-z0-9_-]{43})", message.get_content())
                assert match and message["To"] == email
                token = match.group(1)
                with engine.begin() as connection:
                    stored = connection.execute(
                        text(
                            "SELECT token_hash FROM auth_action_tokens "
                            "WHERE user_id=:id AND purpose=:purpose"
                        ),
                        {"id": account["user_id"], "purpose": purpose},
                    ).scalar_one()
                    assert token not in stored and len(stored) == 64
                return token

            request_action(email, "verify_email")
            verification = last_token("verify_email")
            # A reset token cannot be used for email verification, nor vice versa.
            with pytest.raises(HTTPException) as wrong_purpose:
                consume_action(verification, "reset_password", "replacement-password-2026")
            assert wrong_purpose.value.status_code == 400
            monkeypatch.setenv("EMAIL_VERIFICATION_REQUIRED", "true")
            get_settings.cache_clear()
            with engine.begin() as connection:
                job_id, dispatch_id = uuid4(), uuid4()
                params = {
                    "id": job_id,
                    "tenant": account["tenant_id"],
                    "user": account["user_id"],
                    "key": uuid4().hex,
                }
                connection.execute(
                    text(
                        "INSERT INTO processing_jobs "
                        "(id,tenant_id,requested_by,kind,status,progress,"
                        "idempotency_key,payload) "
                        "VALUES (:id,:tenant,:user,'space_export','queued',0,:key,'{}')"
                    ),
                    params,
                )
                connection.execute(
                    text(
                        "INSERT INTO job_dispatches (id,tenant_id,job_id,requested_by,done,due_at) "
                        "VALUES (:dispatch,:tenant,:id,:user,false,now()+interval '1 hour')"
                    ),
                    {**params, "dispatch": dispatch_id},
                )
            with Session(get_engine()) as session, session.begin():
                assert worker_job(session, dispatch_id) is None
            with pytest.raises(HTTPException) as unverified:
                login(
                    Credentials(email=email, password=password),
                    Request({"type": "http", "client": (uuid4().hex, 123), "headers": []}),
                )
            assert unverified.value.status_code == 403
            with TestClient(app, client=(uuid4().hex, 123)) as local_api:
                assert local_api.get("/api/v1/auth/me", headers=auth).status_code == 401
                confirmed = local_api.post(
                    "/api/v1/auth/verify-email", json={"token": verification}
                )
                assert confirmed.status_code == 200
                pending = local_api.post(
                    "/api/v1/auth/register",
                    json={"email": f"{uuid4()}@example.invalid", "password": password},
                )
                assert pending.status_code == 201, pending.text
                pending_account = pending.json()["data"]
                accounts.append(pending_account)
                assert pending_account["verification_required"]
                assert "access_token" not in pending_account
                with engine.begin() as connection:
                    assert (
                        connection.execute(
                            text("SELECT count(*) FROM auth_sessions WHERE user_id=:id"),
                            {"id": pending_account["user_id"]},
                        ).scalar_one()
                        == 0
                    )
            with pytest.raises(HTTPException):
                consume_action(verification, "verify_email")
            with engine.begin() as connection:
                assert connection.execute(
                    text("SELECT email_verified FROM users WHERE id=:id"),
                    {"id": account["user_id"]},
                ).scalar_one()

            with TestClient(app, client=(uuid4().hex, 123)) as local_api:
                before = len(smtp.messages)
                known = local_api.post("/api/v1/auth/request-password-reset", json={"email": email})
                unknown = local_api.post(
                    "/api/v1/auth/request-password-reset",
                    json={"email": f"{uuid4()}@example.invalid"},
                )
                assert known.status_code == unknown.status_code == 202
                assert known.json() == unknown.json()
                assert len(smtp.messages) == before + 1

            before = len(smtp.messages)
            request_action(f"{uuid4()}@example.invalid", "reset_password")
            assert len(smtp.messages) == before
            request_action(email, "reset_password")
            expired = last_token("reset_password")
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE auth_action_tokens SET expires_at=now()-interval '1 second' "
                        "WHERE user_id=:id"
                    ),
                    {"id": account["user_id"]},
                )
            assert (
                client.post(
                    "/api/v1/auth/reset-password",
                    json={"token": expired, "password": "new-password-2026"},
                ).status_code
                == 400
            )
            request_action(email, "reset_password")
            superseded = last_token("reset_password")
            request_action(email, "reset_password")
            token = last_token("reset_password")
            assert superseded != token
            with pytest.raises(HTTPException):
                consume_action(superseded, "reset_password", "new-password-2026")
            new_password = "new-strong-password-2026"

            def reset(_):
                return client.post(
                    "/api/v1/auth/reset-password", json={"token": token, "password": new_password}
                ).status_code

            with ThreadPoolExecutor(max_workers=2) as pool:
                assert sorted(pool.map(reset, range(2))) == [200, 400]
            assert client.get("/api/v1/auth/me", headers=auth).status_code == 401
            assert (
                client.post(
                    "/api/v1/auth/login", json={"email": email, "password": password}
                ).status_code
                == 401
            )
            assert (
                client.post(
                    "/api/v1/auth/login", json={"email": email, "password": new_password}
                ).status_code
                == 200
            )
        finally:
            get_settings.cache_clear()
            with engine.begin() as connection:
                for account in accounts:
                    params = {"tenant": account["tenant_id"], "user": account["user_id"]}
                    connection.execute(
                        text("DELETE FROM audit_events WHERE tenant_id=:tenant"), params
                    )
                    for table in ("job_dispatches", "processing_jobs", "tenant_members"):
                        connection.execute(
                            text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), params
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
