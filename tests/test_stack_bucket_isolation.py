"""Test-only limiter cleanup must remain scoped to its guarded container IP."""

import hashlib
from types import SimpleNamespace

import conftest as stack_fixture
import pytest
from pydantic import SecretStr


def safe_context(monkeypatch):
    for name in ("ISOLATE_STACK_AUTH_LIMIT", "RUN_INTEGRATION", "RUN_STACK_INTEGRATION"):
        monkeypatch.setenv(name, "1")
    monkeypatch.setenv("API_BASE_URL", "http://api:8000")
    settings = SimpleNamespace(app_env="test", redis_url=SecretStr("redis://redis:6379/0"))
    monkeypatch.setattr(stack_fixture, "get_settings", lambda: settings)
    monkeypatch.setattr(stack_fixture, "Path", lambda _path: SimpleNamespace(is_file=lambda: True))
    return settings


def test_stack_ip_isolation_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("ISOLATE_STACK_AUTH_LIMIT", raising=False)

    def forbidden():
        raise AssertionError("Opt-out must not even read configuration")

    monkeypatch.setattr(stack_fixture, "get_settings", forbidden)
    generator = stack_fixture.isolated_stack_ip_buckets.__wrapped__()
    next(generator)
    with pytest.raises(StopIteration):
        next(generator)


def test_module_isolation_only_resets_closed_runner_ip_scopes(monkeypatch):
    seen = []

    def scoped(kinds):
        seen.append(kinds)
        yield

    monkeypatch.setattr(stack_fixture, "isolate_stack_ip_buckets", scoped)
    generator = stack_fixture.isolated_module_registration_bucket.__wrapped__()
    next(generator)
    with pytest.raises(StopIteration):
        next(generator)
    assert seen == [("register", "login", "organization_write")]


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("RUN_INTEGRATION", "0"),
        ("RUN_STACK_INTEGRATION", "0"),
        ("API_BASE_URL", "http://127.0.0.1:8000"),
        ("app_env", "production"),
        ("redis_url", "redis://redis:6379/1"),
        ("redis_url", "redis://other:6379/0"),
        ("redis_url", "redis://user:password@redis:6379/0"),
        ("redis_url", "rediss://redis:6379/0"),
        ("redis_url", "redis://redis:6379/0?query=value"),
        ("redis_url", "redis://redis:6379/0#fragment"),
        ("container", False),
    ],
)
def test_stack_ip_isolation_rejects_unsafe_context_before_network(monkeypatch, kind, value):
    settings = safe_context(monkeypatch)
    if kind == "app_env":
        settings.app_env = value
    elif kind == "redis_url":
        settings.redis_url = SecretStr(value)
    elif kind == "container":
        monkeypatch.setattr(
            stack_fixture, "Path", lambda _path: SimpleNamespace(is_file=lambda: value)
        )
    else:
        monkeypatch.setenv(kind, value)

    def forbidden(*args, **kwargs):
        raise AssertionError("Unsafe context must not connect to any service")

    monkeypatch.setattr(stack_fixture.socket, "create_connection", forbidden)
    monkeypatch.setattr(stack_fixture.Redis, "from_url", forbidden)
    generator = stack_fixture.isolated_stack_ip_buckets.__wrapped__()
    with pytest.raises(RuntimeError, match="dedicated test container"):
        next(generator)


@pytest.mark.parametrize("address", ["172.18.254.201", "8.8.8.8"])
def test_stack_ip_isolation_deletes_only_closed_scopes_for_own_private_ip(monkeypatch, address):
    safe_context(monkeypatch)
    deleted = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def getsockname(self):
            return (address, 12345)

    class Client(Connection):
        def delete(self, *keys):
            deleted.append(keys)

    monkeypatch.setattr(
        stack_fixture.socket, "create_connection", lambda *_args, **_kwargs: Connection()
    )
    monkeypatch.setattr(stack_fixture.Redis, "from_url", lambda *_args, **_kwargs: Client())
    generator = stack_fixture.isolated_stack_ip_buckets.__wrapped__()
    if address == "8.8.8.8":
        with pytest.raises(RuntimeError, match="private source address"):
            next(generator)
        assert deleted == []
        return
    next(generator)
    digest = hashlib.sha256(f"ip:{address}".encode()).hexdigest()
    expected = tuple(
        f"auth-limit:{kind}:{digest}"
        for kind in (
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
    )
    assert deleted == [expected]
    # The finally branch repeats the same exact keys after a failed suite.
    generator.close()
    assert deleted == [expected, expected]
