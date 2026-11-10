import json
from concurrent.futures import ThreadPoolExecutor

from app.core.config import get_settings
from app.core.metrics import MAX_SERIES, HttpMetrics
from app.main import app
from fastapi.testclient import TestClient


def test_metrics_only_operator_can_read_and_never_returns_identity(monkeypatch):
    secret = "test-metrics-operator-token-very-long-12345"
    monkeypatch.setenv("SYSTEM_API_TOKEN", secret)
    get_settings.cache_clear()
    try:
        client = TestClient(app)
        client.get("/health/live?secret=do-not-record")
        assert client.get("/api/v1/system/metrics").status_code == 401
        response = client.get(
            "/api/v1/system/metrics", headers={"Authorization": "Bearer " + secret}
        )
        assert response.status_code == 200
        assert response.json()["data"]["scope"] == "api-process-since-start"
        assert secret not in response.text and "do-not-record" not in response.text
        assert response.headers["cache-control"] == "no-store"
    finally:
        get_settings.cache_clear()


def test_metrics_concurrency_histogram_and_snapshot_copy():
    metrics = HttpMetrics()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda _: metrics.record("GET", "/api/v1/spaces/{tenant_id}/assets", 503, 80),
                range(1000),
            )
        )
    result = metrics.snapshot()["series"][0]
    assert result["requests"] == result["errors_5xx"] == 1000
    assert result["route"] == "/api/v1/spaces/{tenant_id}/assets"
    assert result["histogram"] == [0, 0, 1000, 1000, 1000, 1000, 1000]
    result["histogram"][0] = 999
    assert metrics.snapshot()["series"][0]["histogram"][0] == 0


def test_metrics_reject_raw_ids_queries_methods_and_cap_cardinality():
    metrics = HttpMetrics()
    for route in ("/api/v1/users/12345", "/unsafe?email=user@example.invalid", "/x" * 250):
        metrics.record("UNTRUSTED INPUT", route, 404, float("nan"))
    result = metrics.snapshot()["series"][0]
    assert result["method"] == "OTHER" and result["route"] == "unmatched"
    assert "example.invalid" not in json.dumps(metrics.snapshot())
    for index in range(MAX_SERIES + 5):
        metrics.record("GET", "/route" + "a" * (index // 26) + chr(97 + index % 26), 200, 1)
    assert len(metrics.snapshot()["series"]) == MAX_SERIES
    assert metrics.snapshot()["overflow_requests"] > 0
