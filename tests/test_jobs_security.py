from uuid import uuid4

import pytest
from app.main import app
from app.modules.jobs.routes import JobInput
from fastapi.testclient import TestClient
from pydantic import ValidationError


def test_job_api_requires_identity_before_database():
    path = f"/api/v1/spaces/{uuid4()}/jobs"
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        assert client.post(path, json={"idempotency_key": str(uuid4())}).status_code == 401


def test_job_kind_and_idempotency_are_bounded():
    with pytest.raises(ValidationError):
        JobInput(kind="arbitrary-handler", idempotency_key=str(uuid4()))
    with pytest.raises(ValidationError):
        JobInput(idempotency_key="../unsafe")
