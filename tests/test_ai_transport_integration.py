"""Real PostgreSQL + loopback HTTP fault injection, never model inference.

The disposable server only rejects requests; it does not emulate model output.
Only the test process gets its operator endpoint names. Runtime API flags stay closed.
"""

import json
import os
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx
import pytest
from app.core.config import get_settings
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated stack required"
    ),
]


class RejectionHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        assert 0 < size <= 16384
        self.rfile.read(size)
        self.server.requests.append(self.path)
        is_first = self.path == "/first/api/generate"
        if is_first and self.server.before_response:
            self.server.before_response()
        status = self.server.first_status if is_first else 404
        self.send_response(status)
        if status == 307:
            self.send_header("Location", "/second/api/generate")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@contextmanager
def rejecting_endpoint(status):
    with ThreadingHTTPServer(("127.0.0.1", 0), RejectionHandler) as server:
        server.requests, server.first_status, server.before_response = [], status, None
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=3)


@pytest.mark.parametrize(
    "status,revoke_second,expected_attempts,expected_status,error_code",
    [
        (401, False, 2, "failed", "ALL_PROVIDERS_UNAVAILABLE"),
        (408, False, 1, "reserved", "MODEL_OUTCOME_UNKNOWN"),
        (503, False, 1, "reserved", "MODEL_OUTCOME_UNKNOWN"),
        (307, False, 1, "reserved", "MODEL_OUTCOME_UNKNOWN"),
        (401, True, 1, "failed", "ALL_PROVIDERS_UNAVAILABLE"),
    ],
)
def test_real_http_rejection_fallback_unknown_and_revalidation(
    monkeypatch, status, revoke_second, expected_attempts, expected_status, error_code
):
    settings = get_settings()
    assert settings.app_env == "test" and settings.migration_database_url
    engine = create_engine(
        settings.migration_database_url.get_secret_value(),
        hide_parameters=True,
        connect_args={"connect_timeout": 3},
    )
    account = None
    try:
        with httpx.Client(base_url=os.environ["API_BASE_URL"], timeout=20, trust_env=False) as live:
            response = live.post(
                "/api/v1/auth/register",
                json={"email": f"{uuid4()}@example.invalid", "password": "fault-test-only-2026"},
            )
        assert response.status_code == 201
        account = response.json()["data"]
        path = f"/api/v1/spaces/{account['tenant_id']}"
        with rejecting_endpoint(status) as server, monkeypatch.context() as patch:
            base = f"http://127.0.0.1:{server.server_port}"
            patch.setenv(
                "OLLAMA_ENDPOINTS",
                json.dumps({"fault-first": base + "/first", "fault-second": base + "/second"}),
            )
            get_settings.cache_clear()
            with TestClient(app) as client:
                client.headers["Authorization"] = f"Bearer {account['access_token']}"
                providers = []
                for priority, endpoint in enumerate(("fault-first", "fault-second")):
                    response = client.post(
                        path + "/providers",
                        json={
                            "kind": "ollama",
                            "model": "rejection-only-no-model",
                            "endpoint_name": endpoint,
                            "priority": priority,
                            "enabled": True,
                            "consent": True,
                        },
                    )
                    assert response.status_code == 201
                    providers.append(response.json()["data"]["id"])
                if revoke_second:

                    def disable_second():
                        # Changes only the just-created fixture configuration.
                        with engine.begin() as connection:
                            connection.execute(
                                text(
                                    "UPDATE ai_providers SET enabled=false "
                                    "WHERE tenant_id=:tenant AND id=:provider"
                                ),
                                {"tenant": account["tenant_id"], "provider": providers[1]},
                            )

                    server.before_response = disable_second
                response = client.post(
                    path + "/ai-test",
                    json={"prompt": "HTTP failure fixture; no model inference", "consent": True},
                )
                assert response.status_code == 503
                error = response.json()["error"]
                assert error["code"] == error_code
                assert len(error["attempts"]) == expected_attempts
                assert all(row["code"] == "PROVIDER_HTTP_ERROR" for row in error["attempts"])
                usage = client.get(path + "/ai-usage").json()["data"]
                assert len(usage) == expected_attempts
                assert all(row["status"] == expected_status for row in usage)
                assert all(row["error_code"] == "PROVIDER_HTTP_ERROR" for row in usage)
                assert all(row["cost_usd"] is None for row in usage)
                assert all(
                    row["input_tokens"] is None and row["output_tokens"] is None for row in usage
                )
            assert (
                server.requests
                == ["/first/api/generate", "/second/api/generate"][:expected_attempts]
            )
    finally:
        get_settings.cache_clear()
        if account:
            params = {"tenant": account["tenant_id"], "user": account["user_id"]}
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout='5s'"))
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
                    connection.execute(text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), params)
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
