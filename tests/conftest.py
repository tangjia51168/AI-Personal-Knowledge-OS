"""Isolate only closed rate-limit scopes for this dedicated test container's IP."""

import hashlib
import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from app.core.config import get_settings
from redis import Redis

STACK_IP_BUCKET_KINDS = (
    "login",
    "register",
    "source_inspect",
    "source_import",
    "source_content_import",
    "source_collect",
    "note_edit",
    "note_annotation",
    "note_link",
    "note_summary",
    "organization_write",
    "learning_write",
)


@pytest.fixture(scope="session", autouse=True)
def isolated_stack_ip_buckets():
    yield from isolate_stack_ip_buckets(STACK_IP_BUCKET_KINDS)


@pytest.fixture(scope="module", autouse=True)
def isolated_module_registration_bucket():
    # A full suite now creates over 100 short-lived accounts across modules.
    # Reset only this dedicated runner's registration/login/organization IP
    # buckets at module boundaries; identity buckets, other clients and product
    # limits are untouched. Modules still exercise their actual in-scope limits.
    # The rate-limit module still exercises the actual limit within its scope.
    yield from isolate_stack_ip_buckets(("register", "login", "organization_write"))


def isolate_stack_ip_buckets(kinds):
    if not kinds or any(kind not in STACK_IP_BUCKET_KINDS for kind in kinds):
        raise RuntimeError("Unknown test isolation scope")
    if os.getenv("ISOLATE_STACK_AUTH_LIMIT") != "1":
        yield
        return
    settings = get_settings()
    redis_endpoint = urlsplit(settings.redis_url.get_secret_value())
    if (
        settings.app_env != "test"
        or os.getenv("RUN_INTEGRATION") != "1"
        or os.getenv("RUN_STACK_INTEGRATION") != "1"
        or os.getenv("API_BASE_URL") != "http://api:8000"
        or not Path("/.dockerenv").is_file()
        or redis_endpoint.scheme != "redis"
        or redis_endpoint.netloc != "redis:6379"
        or redis_endpoint.path != "/0"
        or redis_endpoint.query
        or redis_endpoint.fragment
    ):
        raise RuntimeError("IP bucket isolation requires the dedicated test container")
    # A direct TCP connection uses the same source address as HTTPX (trust_env=False).
    # Do not trust request headers or reset other clients, identities, or Redis data.
    with socket.create_connection(("api", 8000), timeout=3) as connection:
        address = connection.getsockname()[0]
    if not ipaddress.ip_address(address).is_private:
        raise RuntimeError("Test container must use a private source address")
    digest = hashlib.sha256(f"ip:{address}".encode()).hexdigest()
    keys = [f"auth-limit:{kind}:{digest}" for kind in kinds]
    with Redis.from_url(
        settings.redis_url.get_secret_value(), socket_timeout=3, socket_connect_timeout=3
    ) as client:
        client.delete(*keys)
        try:
            yield
        finally:
            client.delete(*keys)
