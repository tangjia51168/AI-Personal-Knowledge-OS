"""Process-local, bounded operational aggregates; no tenant/user labels or bodies."""

from __future__ import annotations

import math
import re
from threading import Lock
from time import monotonic
from typing import Any

BUCKETS = (5, 25, 100, 500, 2000, 10000)
MAX_SERIES = 256
METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
TEMPLATE = re.compile(r"^/(?:[a-z0-9_/-]|\{[a-z_]+\}){0,220}$")


class HttpMetrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self._started = monotonic()
        self._series: dict[tuple[str, str], dict[str, Any]] = {}
        self._overflow = 0

    def record(self, method: str, route: str, status: int, duration_ms: float) -> None:
        # Only framework templates are supplied. Reject high-cardinality/raw data
        # even if a future caller accidentally sends a UUID or a query string.
        if not TEMPLATE.fullmatch(route) or any(c.isdigit() for c in route.replace("/v1/", "/v/")):
            route = "unmatched"
        method = method if method in METHODS else "OTHER"
        duration_ms = duration_ms if math.isfinite(duration_ms) else 0.0
        duration_ms = min(max(duration_ms, 0.0), 3_600_000.0)
        with self._lock:
            key = (method, route)
            if key not in self._series and len(self._series) >= MAX_SERIES:
                self._overflow += 1
                return
            entry = self._series.setdefault(
                key,
                {
                    "requests": 0,
                    "errors_5xx": 0,
                    "responses_4xx": 0,
                    "duration_ms_sum": 0.0,
                    "duration_ms_max": 0.0,
                    "histogram": [0] * (len(BUCKETS) + 1),
                },
            )
            entry["requests"] += 1
            entry["errors_5xx"] += int(status >= 500)
            entry["responses_4xx"] += int(400 <= status < 500)
            entry["duration_ms_sum"] += duration_ms
            entry["duration_ms_max"] = max(entry["duration_ms_max"], duration_ms)
            for index, upper in enumerate((*BUCKETS, math.inf)):
                entry["histogram"][index] += int(duration_ms <= upper)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "scope": "api-process-since-start",
                "uptime_seconds": round(monotonic() - self._started, 3),
                "series_limit": MAX_SERIES,
                "overflow_requests": self._overflow,
                "duration_bucket_upper_ms": [*BUCKETS, "+Inf"],
                "series": [
                    {
                        "method": method,
                        "route": route,
                        **entry,
                        "duration_ms_sum": round(entry["duration_ms_sum"], 3),
                        "duration_ms_max": round(entry["duration_ms_max"], 3),
                        "histogram": list(entry["histogram"]),
                    }
                    for (method, route), entry in sorted(self._series.items())
                ],
            }


http_metrics = HttpMetrics()
