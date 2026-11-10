from io import BytesIO
from uuid import uuid4

import pytest
from app.main import app
from app.modules.files.service import validate_file, verify_ticket
from fastapi import HTTPException
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    "body,mime",
    [
        (b"html", "text/html"),
        (b"not-pdf", "application/pdf"),
        (b"\xff", "text/plain"),
        (b"", "text/plain"),
    ],
)
def test_invalid_file_rejected(body, mime):
    with pytest.raises(HTTPException):
        validate_file(BytesIO(body), len(body), mime)


def test_file_apis_require_identity():
    path = f"/api/v1/spaces/{uuid4()}/assets"
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        assert client.post(path, content=b"file").status_code == 401


def test_expired_ticket_rejected_without_config():
    with pytest.raises(HTTPException) as error:
        verify_ticket(uuid4(), uuid4(), uuid4(), 1, "forged")
    assert error.value.status_code == 403
