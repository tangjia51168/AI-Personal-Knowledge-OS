"""Bound ordinary request bodies and keep exception data out of server logs."""

import asyncio
import json
import logging
import re
from time import monotonic
from uuid import uuid4

from fastapi import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.metrics import http_metrics

logger = logging.getLogger("knowledge_os.http")
ASSET_UPLOAD = re.compile(r"^/api/v1/spaces/[0-9a-f-]{36}/assets$")
SOURCE_INPUT = re.compile(r"^/api/v1/spaces/[0-9a-f-]{36}/sources(?:/inspect)?$")
CONTENT_IMPORT = re.compile(r"^/api/v1/spaces/[0-9a-f-]{36}/sources/[0-9a-f-]{36}/imports$")
NOTE_EDIT = re.compile(r"^/api/v1/spaces/[0-9a-f-]{36}/notes/[0-9a-f-]{36}/revisions$")
NOTE_CREATE = re.compile(r"^/api/v1/spaces/[0-9a-f-]{36}/snapshots/[0-9a-f-]{36}/notes$")


class HttpBoundary:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id, started, response_started, status = uuid4().hex, monotonic(), False, 500
        received = 0
        upload = scope["method"] == "POST" and bool(ASSET_UPLOAD.fullmatch(scope["path"]))
        source_input = scope["method"] == "POST" and bool(SOURCE_INPUT.fullmatch(scope["path"]))
        content_import = scope["method"] == "POST" and bool(CONTENT_IMPORT.fullmatch(scope["path"]))
        note_edit = scope["method"] == "POST" and bool(
            NOTE_EDIT.fullmatch(scope["path"]) or NOTE_CREATE.fullmatch(scope["path"])
        )
        # Source text is at most 16KiB UTF-8; its entire JSON representation,
        # including escapes and the idempotency key, must fit within 17KiB.
        limit = (
            10 * 1024 * 1024
            if upload
            else 128 * 1024
            if content_import or note_edit
            else 17 * 1024
            if source_input
            else 16384
        )
        deadline = monotonic() + 30

        async def bounded_receive() -> Message:
            nonlocal received
            try:
                async with asyncio.timeout(max(0, deadline - monotonic())):
                    message = await receive()
            except TimeoutError:
                raise HTTPException(408, detail={"code": "REQUEST_TIMEOUT"}) from None
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(413, detail={"code": "REQUEST_BODY_LIMIT"})
            return message

        async def safe_send(message: Message) -> None:
            nonlocal response_started, status
            if message["type"] == "http.response.start":
                response_started, status = True, message["status"]
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-request-id", request_id.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"cache-control", b"no-store"),
                ]
            await send(message)

        try:
            await self.app(scope, bounded_receive, safe_send)
        except Exception as exc:
            # This middleware sits inside ServerErrorMiddleware. Catching here
            # prevents Uvicorn from printing a secret-bearing exception traceback.
            if isinstance(exc, HTTPException):
                status = exc.status_code
                code = (
                    exc.detail.get("code", "HTTP_ERROR")
                    if isinstance(exc.detail, dict)
                    else "HTTP_ERROR"
                )
            elif isinstance(exc, TimeoutError):
                status, code = 408, "REQUEST_TIMEOUT"
            else:
                status, code = 500, "INTERNAL_ERROR"
            logger.error("request_failed id=%s class=%s", request_id, type(exc).__name__)
            if not response_started:
                body = json.dumps({"error": {"code": code}}).encode()
                await safe_send(
                    {
                        "type": "http.response.start",
                        "status": status,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": body})
        finally:
            # Route templates avoid query strings, raw UUIDs, email and user input.
            route = getattr(scope.get("route"), "path", "unmatched")
            elapsed_ms = (monotonic() - started) * 1000
            http_metrics.record(scope["method"], route, status, elapsed_ms)
            logger.info(
                "request id=%s method=%s route=%s status=%s duration_ms=%s",
                request_id,
                scope["method"],
                route,
                status,
                round(elapsed_ms),
            )
