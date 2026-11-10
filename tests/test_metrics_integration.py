"""Real API operator metrics, never a mocked readiness or metric collector."""

import os

import httpx
import pytest
from app.core.config import get_settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated stack required"
    ),
]


def test_live_operator_metrics_count_requests_and_exclude_query_and_credentials():
    token = get_settings().system_api_token.get_secret_value()
    auth = {"Authorization": "Bearer " + token}
    with httpx.Client(base_url=os.environ["API_BASE_URL"], timeout=15, trust_env=False) as client:
        assert client.get("/api/v1/system/metrics").status_code == 401
        assert (
            client.get(
                "/api/v1/system/metrics", headers={"Authorization": "Bearer invalid"}
            ).status_code
            == 401
        )
        first = client.get("/api/v1/system/metrics", headers=auth)
        assert first.status_code == 200
        before = sum(item["requests"] for item in first.json()["data"]["series"])
        assert client.get("/health/live?private=metrics-query-sentinel").status_code == 200
        last = client.get("/api/v1/system/metrics", headers=auth)
        assert last.status_code == 200
        data = last.json()["data"]
        assert data["scope"] == "api-process-since-start" and data["overflow_requests"] == 0
        assert sum(item["requests"] for item in data["series"]) >= before + 2
        assert token not in last.text and "metrics-query-sentinel" not in last.text
        assert last.headers["cache-control"] == "no-store"
        assert any(item["route"] == "/health/live" for item in data["series"])
        for item in data["series"]:
            assert item["histogram"] == sorted(item["histogram"])
            assert item["histogram"][-1] == item["requests"]
