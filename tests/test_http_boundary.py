import asyncio
import json

import pytest
from app.core import http
from app.core.http import HttpBoundary
from app.main import app
from fastapi.testclient import TestClient


def test_http_boundary_sanitizes_exceptions_and_correlates_logs(caplog):
    secret = "private-password-provider-token"

    async def failed(scope, receive, send):
        raise RuntimeError(secret)

    output = []

    async def send(message):
        output.append(message)

    async def receive():
        return {"type": "http.request", "body": b""}

    asyncio.run(
        HttpBoundary(failed)(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/private",
                "query_string": secret.encode(),
            },
            receive,
            send,
        )
    )
    assert output[0]["status"] == 500
    assert json.loads(output[1]["body"])["error"]["code"] == "INTERNAL_ERROR"
    assert any(key == b"x-request-id" for key, _ in output[0]["headers"])
    assert secret not in caplog.text and secret.encode() not in output[1]["body"]
    assert "RuntimeError" in caplog.text


def test_direct_api_body_is_bounded_without_content_length():
    async def consume(scope, receive, send):
        await receive()

    async def receive():
        return {"type": "http.request", "body": b"x" * 16385}

    output = []

    async def send(message):
        output.append(message)

    asyncio.run(
        HttpBoundary(consume)(
            {"type": "http", "method": "POST", "path": "/api/v1/auth/login"},
            receive,
            send,
        )
    )
    assert output[0]["status"] == 413


def test_fastapi_preserves_request_limit_status():
    response = TestClient(app).post(
        "/api/v1/auth/login", content=b"x" * 20000, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_BODY_LIMIT"


def test_slow_body_deadline_is_408(monkeypatch):
    clock = iter([0, 0, 31, 31])
    monkeypatch.setattr(http, "monotonic", lambda: next(clock))

    async def consume(scope, receive, send):
        await receive()

    async def receive():
        await asyncio.sleep(0.01)
        return {"type": "http.request", "body": b""}

    output = []

    async def send(message):
        output.append(message)

    asyncio.run(
        HttpBoundary(consume)(
            {"type": "http", "method": "POST", "path": "/api/v1/auth/login"},
            receive,
            send,
        )
    )
    assert output[0]["status"] == 408


@pytest.mark.parametrize("suffix", ["", "/inspect"])
@pytest.mark.parametrize("extra", [0, 1])
def test_source_json_body_boundary_counts_streamed_bytes(suffix, extra):
    # Exercise both source endpoints with no Content-Length and split chunks.
    chunks = iter([b"x" * 16000, b"x" * (17 * 1024 - 16000 + extra)])
    output = []

    async def receive():
        return {"type": "http.request", "body": next(chunks)}

    async def consume(scope, receive, send):
        await receive()
        await receive()
        await send({"type": "http.response.start", "status": 204, "headers": []})

    async def send(message):
        output.append(message)

    asyncio.run(
        HttpBoundary(consume)(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/spaces/00000000-0000-0000-0000-000000000001/sources" + suffix,
            },
            receive,
            send,
        )
    )
    assert output[0]["status"] == (413 if extra else 204)


@pytest.mark.parametrize(
    "method,suffix,limit",
    [
        ("POST", "/revisions", 128 * 1024),
        ("POST", "/revisions/extra", 16384),
        ("PATCH", "/revisions", 16384),
    ],
)
@pytest.mark.parametrize("extra", [0, 1])
def test_note_edit_exact_post_path_streams_bounded_body(method, suffix, limit, extra):
    chunks = iter([b"x" * (limit - 10), b"x" * (10 + extra)])
    output = []

    async def receive():
        return {"type": "http.request", "body": next(chunks)}

    async def consume(scope, receive, send):
        await receive()
        await receive()
        await send({"type": "http.response.start", "status": 204, "headers": []})

    async def send(message):
        output.append(message)

    asyncio.run(
        HttpBoundary(consume)(
            {
                "type": "http",
                "method": method,
                "path": "/api/v1/spaces/00000000-0000-0000-0000-000000000001/notes/"
                "00000000-0000-0000-0000-000000000002" + suffix,
            },
            receive,
            send,
        )
    )
    assert output[0]["status"] == (413 if extra else 204)


@pytest.mark.parametrize(
    "method,suffix,limit",
    [
        ("POST", "/imports", 128 * 1024),
        ("POST", "/imports/extra", 16384),
        ("PUT", "/imports", 16384),
        ("POST", "/collect", 16384),
    ],
)
@pytest.mark.parametrize("extra", [0, 1])
def test_import_limit_is_streamed_and_only_expands_exact_post_route(method, suffix, limit, extra):
    chunks = iter([b"x" * (limit - 10), b"x" * (10 + extra)])
    output = []

    async def receive():
        return {"type": "http.request", "body": next(chunks)}

    async def consume(scope, receive, send):
        await receive()
        await receive()
        await send({"type": "http.response.start", "status": 204, "headers": []})

    async def send(message):
        output.append(message)

    asyncio.run(
        HttpBoundary(consume)(
            {
                "type": "http",
                "method": method,
                "path": "/api/v1/spaces/00000000-0000-0000-0000-000000000001/sources/"
                "00000000-0000-0000-0000-000000000002" + suffix,
            },
            receive,
            send,
        )
    )
    assert output[0]["status"] == (413 if extra else 204)


@pytest.mark.parametrize("extra", [0, 1])
@pytest.mark.parametrize(
    "method,suffix,limit",
    [
        ("POST", "/notes", 128 * 1024),
        ("POST", "/notes/extra", 16384),
        ("PUT", "/notes", 16384),
        ("GET", "/note-evidence", 16384),
    ],
)
def test_first_note_exact_post_path_has_same_bounded_edit_allowance(method, suffix, limit, extra):
    output = []

    async def receive():
        return {"type": "http.request", "body": b"x" * (limit + extra)}

    async def consume(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 204, "headers": []})

    async def send(message):
        output.append(message)

    asyncio.run(
        HttpBoundary(consume)(
            {
                "type": "http",
                "method": method,
                "path": "/api/v1/spaces/00000000-0000-0000-0000-000000000001/snapshots/"
                "00000000-0000-0000-0000-000000000002" + suffix,
            },
            receive,
            send,
        )
    )
    assert output[0]["status"] == (413 if extra else 204)
