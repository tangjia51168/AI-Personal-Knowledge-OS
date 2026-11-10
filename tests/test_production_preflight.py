import base64
import json

import pytest
from app.core.config import Settings, get_settings
from app.core.production import validate_production
from app.main import app
from fastapi.testclient import TestClient


def candidate(**changes):
    values = {
        "app_env": "production",
        "database_url": "postgresql+psycopg://knowledge_app:private-password@postgres.internal:5432/knowledge_os",
        "s3_endpoint_url": "https://objects.internal.example",
        "s3_access_key": "runtime-object-user",
        "system_api_token": "operator-" + "a" * 40,
        "download_signing_key": "download-" + "b" * 40,
        "s3_secret_key": "storage-" + "c" * 40,
        "redis_url": "redis://default:private-password@redis:6379/0",
        "registration_enabled": False,
        "cors_origins": ["https://knowledge.example.com"],
        "trusted_hosts": ["knowledge.example.com", "api"],
        "public_app_url": "https://knowledge.example.com",
        "credential_active_key": "v1",
        "credential_keys": json.dumps({"v1": base64.b64encode(bytes(range(32))).decode()}),
    }
    return Settings(_env_file=None, **{**values, **changes})


def test_valid_candidate_is_only_configuration_evidence():
    validate_production(candidate())


def test_private_services_may_use_loopback():
    validate_production(
        candidate(
            database_url="postgresql+psycopg://knowledge_app:private@localhost:5432/db",
            s3_endpoint_url="https://localhost:9000",
        )
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"public_app_url": "https://knowledge.example.com:notaport"},
        {"public_app_url": "https://knowledge.example.com:0"},
        {"public_app_url": "https://knowledge.example.com:65536"},
        {"public_app_url": "https://knowledge.example.com:"},
        {"public_app_url": "https://knowledge..example.com"},
        {"public_app_url": "https://knowledge.example.com\n"},
        {"cors_origins": ["https://knowledge.example.com:private-invalid-port"]},
        {"cors_origins": ["https://knowledge.example.com:65536"]},
        {"s3_endpoint_url": "https://objects.internal.example:private-invalid-port"},
        {"s3_endpoint_url": "https://objects.internal.example:0"},
        {"s3_endpoint_url": "https://objects.internal.example:65536"},
        {"s3_endpoint_url": "https://objects.internal.example:"},
        {"s3_endpoint_url": "https://objects.internal.example\n"},
    ],
)
def test_production_rejects_invalid_url_authority_without_values(changes):
    with pytest.raises(RuntimeError) as error:
        validate_production(candidate(**changes))
    assert "private-invalid-port" not in str(error.value)
    assert "example" not in str(error.value)


def test_production_accepts_valid_explicit_ports_and_ipv6_private_storage():
    validate_production(
        candidate(
            public_app_url="https://knowledge.example.com:8443",
            cors_origins=["https://knowledge.example.com:8443"],
            s3_endpoint_url="https://[::1]:9000",
        )
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://default:private-redis-secret@redis:6379/0",
        "unix://default:private-redis-secret@/tmp/redis.sock",
        "redis://default:private-redis-secret@:6379/0",
        "redis://default:private-redis-secret@redis:private-port/0",
        "redis://default:private-redis-secret@redis:0/0",
        "redis://default:private-redis-secret@redis:65536/0",
        "redis://default:private-redis-secret@redis:/0",
        "redis://default:private-redis-secret@redis:-1/0",
        "redis://default:private-redis-secret@redis/word",
        "redis://default:private-redis-secret@redis/-1",
        "redis://default:private-redis-secret@redis/0/1",
        "redis://default:private-redis-secret@redis/%31",
        "redis://default:private-redis-secret@redis/0?host=other",
        "redis://default:private-redis-secret@redis/0?db=1",
        "redis://default:private-redis-secret@redis/0?credential_provider=private.Class",
        "redis://default:private-redis-secret@redis/0#private-fragment",
        "redis://default:private-redis-secret@redis/0\n",
        "redis://default:%00@redis/0",
        "redis://default:private-redis-secret@bad%2ehost/0",
        "redis://default:private-redis-secret@redis/0;redis://other/0",
        "rediss://default:private-redis-secret@redis/0",
        "rediss://default:private-redis-secret@redis/0?ssl_cert_reqs=none",
        "rediss://default:private-redis-secret@redis/0?ssl_cert_reqs=optional",
        "rediss://default:private-redis-secret@redis/0?ssl_cert_reqs=CERT_REQUIRED",
        "rediss://default:private-redis-secret@redis/0?ssl_cert_reqs=required&ssl_cert_reqs=none",
        "rediss://default:private-redis-secret@redis/0?ssl_cert_reqs=required&db=1",
        "rediss://default:private-redis-secret@redis/0?ssl_cert_reqs=required&ssl_check_hostname=false",
        "rediss://default:private-redis-secret@redis/0?ssl_cert_reqs=required&ssl_ca_certs=",
        "rediss://default:private-redis-secret@redis/0?ssl_cert_reqs=required&ssl_certfile=one",
        "rediss://default:private-redis-secret@redis/0?ssl_cert_reqs=required&ssl_ca_certs=%00",
        "rediss://default:private-redis-secret@redis/0?ssl_cert_reqs=required&invalid",
        "redis://default:private-redis-secret@redis/0?ssl_cert_reqs=required",
    ],
)
def test_production_rejects_ambiguous_or_unsafe_redis_url_without_values(url):
    with pytest.raises(RuntimeError, match="authenticated Redis required") as error:
        validate_production(candidate(redis_url=url))
    assert "private" not in str(error.value)
    assert "redis://" not in str(error.value)


@pytest.mark.parametrize(
    "url",
    [
        "redis://:private-password@redis",
        "redis://default:private-password@redis/",
        "redis://default:private-password@localhost:6379/2",
        "redis://default:private%40password@[::1]:6379/0",
        "rediss://default:private-password@redis:6380/0?ssl_cert_reqs=required",
        "rediss://default:private-password@redis/2?ssl_cert_reqs=required&ssl_check_hostname=true",
        "rediss://default:private-password@redis/0?ssl_cert_reqs=required&ssl_ca_certs=%2Fca.pem",
        "rediss://default:private-password@redis/0?ssl_cert_reqs=required&ssl_certfile=%2Fcert.pem&ssl_keyfile=%2Fkey.pem",
    ],
)
def test_accepted_redis_urls_have_consistent_consumers_without_connection(url):
    import ssl

    from celery import Celery
    from redis import Redis

    validate_production(candidate(redis_url=url))
    direct = Redis.from_url(url)
    queue = Celery("production-url-fixture", broker=url, backend=url)
    broker = queue.connection_for_write()
    backend = queue.backend
    direct_options = direct.connection_pool.connection_kwargs
    assert direct_options["host"] == broker.hostname == backend.connparams["host"]
    assert direct_options["password"] == broker.password == backend.connparams["password"]
    broker_db = int(broker.virtual_host.strip("/") or "0")
    assert direct_options.get("db", 0) == broker_db == backend.connparams["db"]
    if url.startswith("rediss:"):
        assert (
            broker.ssl["ssl_cert_reqs"] == backend.connparams["ssl_cert_reqs"] == ssl.CERT_REQUIRED
        )
        assert direct_options["ssl_cert_reqs"] == "required"
        tls_connection = direct.connection_pool.make_connection()
        assert tls_connection.cert_reqs == ssl.CERT_REQUIRED
        assert tls_connection.check_hostname is True
    broker.close()
    queue.close()
    direct.close()


def verified_email_candidate(**changes):
    return candidate(
        **{
            "email_verification_required": True,
            "public_app_url": "https://knowledge.example.com",
            "smtp_host": "smtp.example.invalid",
            "smtp_username": "private-smtp-user",
            "smtp_password": "private-smtp-password",
            "smtp_from": "accounts@example.invalid",
            "smtp_security": "starttls",
            **changes,
        }
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"smtp_host": ""},
        {"smtp_security": "plain"},
        {"smtp_username": ""},
        {"smtp_password": ""},
        {"smtp_from": "private-sender\r\nBcc:private@example.invalid"},
        {"public_app_url": "http://knowledge.example.com"},
    ],
)
def test_verification_requires_safe_mail_configuration_without_leaking_values(changes):
    with pytest.raises(RuntimeError, match="authenticated TLS SMTP") as error:
        validate_production(verified_email_candidate(**changes))
    assert "private" not in str(error.value)
    assert "example" not in str(error.value)


@pytest.mark.parametrize("security", ["tls", "starttls"])
def test_authenticated_mail_configuration_is_only_configuration_evidence(security):
    validate_production(verified_email_candidate(smtp_security=security))


def test_even_complete_smtp_configuration_does_not_enable_public_registration():
    with pytest.raises(RuntimeError, match="public registration"):
        validate_production(verified_email_candidate(registration_enabled=True))


@pytest.mark.parametrize(
    "changes",
    [
        {"system_api_token": "CHANGE_ME"},
        {"download_signing_key": "operator-" + "a" * 40},
        {"migration_database_url": "postgresql://admin:do-not-print@example.com/db"},
        {"registration_enabled": True},
        {"redis_url": "redis://redis:6379/0"},
        {"cors_origins": ["http://example.com"]},
        {"trusted_hosts": ["*"]},
        {"credential_keys": '{"v1":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}'},
        {"s3_endpoint_url": "http://objects.internal.example"},
        {"s3_endpoint_url": "https://private-user:private-secret@objects.internal.example"},
        {"s3_endpoint_url": "https://objects.internal.example?private=value"},
        {"s3_endpoint_url": "https://objects.internal.example#private"},
        {"s3_endpoint_url": "https://[malformed"},
        {"s3_access_key": ""},
        {"public_app_url": "http://knowledge.example.com"},
        {"public_app_url": "https://127.0.0.1"},
        {"public_app_url": "https://[::1]"},
        {"public_app_url": "https://10.0.0.5"},
        {"cors_origins": ["https://192.168.1.5"]},
        {"public_app_url": "https://knowledge.example.com/private"},
        {"public_app_url": "https://knowledge.example.com?internal=true"},
        {"redis_url": "redis://[malformed"},
        {"public_app_url": "https://[malformed"},
    ],
)
def test_production_rejects_unsafe_configuration_without_values(changes):
    with pytest.raises(RuntimeError) as error:
        validate_production(candidate(**changes))
    assert "do-not-print" not in str(error.value)


def test_registration_can_be_disabled_before_database_work(monkeypatch):
    monkeypatch.setenv("REGISTRATION_ENABLED", "false")
    get_settings.cache_clear()
    try:
        response = TestClient(app).post(
            "/api/v1/auth/register",
            json={
                "email": "private@example.invalid",
                "password": "private-password-2026",
            },
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "REGISTRATION_DISABLED"
    finally:
        get_settings.cache_clear()
