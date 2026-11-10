"""Live API -> Redis -> Worker acceptance; fails when infrastructure isn't running."""

import os
import time

import httpx
from app.core.config import get_settings

settings = get_settings()
token = settings.system_api_token.get_secret_value()
if len(token) < 32 or token.startswith("CHANGE_ME"):
    raise SystemExit("Configure SYSTEM_API_TOKEN in .env before running smoke tests")
base = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
with httpx.Client(base_url=base, timeout=15) as client:
    response = client.get("/health/ready")
    response.raise_for_status()
    print("PASS: API and infrastructure readiness")
    headers = {"Authorization": f"Bearer {token}"}
    submitted = client.post("/api/v1/system/smoke-jobs", headers=headers)
    submitted.raise_for_status()
    job_id = submitted.json()["data"]["id"]
    for _ in range(30):
        result = client.get(f"/api/v1/system/smoke-jobs/{job_id}", headers=headers)
        result.raise_for_status()
        state = result.json()["data"]["status"]
        if state == "SUCCESS":
            print("PASS: API -> Redis -> independent Celery Worker -> result backend")
            break
        if state in ("FAILURE", "REVOKED"):
            raise SystemExit(f"FAIL: smoke task ended with {state}")
        time.sleep(1)
    else:
        raise SystemExit("FAIL: Worker did not complete the smoke task within 30 seconds")
