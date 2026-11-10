"""Real service integration. Only opt in against the isolated acceptance stack."""

import os
import time
from uuid import uuid4

import httpx
import pytest
from app.core.config import get_settings
from app.integrations.storage import get_s3_client, tenant_object_key
from redis import Redis
from sqlalchemy import create_engine, text

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated stack opt-in required"
    ),
]


def test_live_private_object_roundtrip():
    settings = get_settings()
    client = get_s3_client()
    key = tenant_object_key(uuid4(), uuid4())
    body = b"knowledge-os-private-acceptance"
    try:
        client.put_object(Bucket=settings.s3_bucket, Key=key, Body=body)
        response = client.get_object(Bucket=settings.s3_bucket, Key=key)
        try:
            assert response["Body"].read() == body
        finally:
            response["Body"].close()
        url = f"{settings.s3_endpoint_url}/{settings.s3_bucket}/{key}"
        # Existing known object: anonymous GET must not reveal it.
        assert httpx.get(url, timeout=10, trust_env=False).status_code == 403
    finally:
        client.delete_object(Bucket=settings.s3_bucket, Key=key)


def test_live_redis_roundtrip():
    key = f"acceptance:{uuid4()}"
    with Redis.from_url(get_settings().redis_url.get_secret_value()) as client:
        try:
            assert client.set(key, b"verified", ex=60)
            assert client.get(key) == b"verified"
        finally:
            client.delete(key)


def test_live_application_role_and_transaction_context_reset():
    # A real application login, not only SET ROLE on a superuser connection.
    engine = create_engine(
        get_settings().database_url.get_secret_value(), pool_size=1, max_overflow=0
    )
    try:
        with engine.begin() as connection:
            role = connection.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user")
            ).one()
            assert not role.rolsuper and not role.rolbypassrls
            assert connection.execute(text("SELECT count(*) FROM processing_jobs")).scalar() == 0
            connection.execute(
                text("SELECT set_config('app.tenant_id', :id, true)"), {"id": str(uuid4())}
            )
        with engine.begin() as connection:
            assert (
                connection.execute(
                    text("SELECT NULLIF(current_setting('app.tenant_id', true), '')")
                ).scalar()
                is None
            )
            assert connection.execute(text("SELECT count(*) FROM processing_jobs")).scalar() == 0
            assert (
                connection.execute(
                    text("SELECT extname FROM pg_extension WHERE extname='vector'")
                ).scalar()
                == "vector"
            )
    finally:
        engine.dispose()


def test_live_api_redis_independent_worker():
    settings = get_settings()
    base = os.environ["API_BASE_URL"]
    headers = {"Authorization": f"Bearer {settings.system_api_token.get_secret_value()}"}
    with httpx.Client(base_url=base, timeout=15, trust_env=False) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.post("/api/v1/system/smoke-jobs").status_code == 401
        response = client.post("/api/v1/system/smoke-jobs", headers=headers)
        assert response.status_code == 202
        job_id = response.json()["data"]["id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = client.get(f"/api/v1/system/smoke-jobs/{job_id}", headers=headers)
            assert result.status_code == 200
            status = result.json()["data"]["status"]
            assert status not in ("FAILURE", "REVOKED")
            if status == "SUCCESS":
                return
            time.sleep(0.5)
        pytest.fail("Independent Worker did not complete real Redis task within 30s")
