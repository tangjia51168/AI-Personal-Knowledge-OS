from app.main import app
from fastapi.testclient import TestClient


def test_action_validation_does_not_echo_token_or_new_password():
    client = TestClient(app)
    for path, body, secret in (
        ("verify-email", {"token": "private-token"}, "private-token"),
        ("reset-password", {"token": "a" * 43, "password": "private"}, "private"),
    ):
        response = client.post("/api/v1/auth/" + path, json=body)
        assert response.status_code == 422
        assert secret not in response.text


def test_no_get_can_consume_an_account_action():
    client = TestClient(app)
    assert client.get("/api/v1/auth/verify-email?token=private").status_code == 405
    assert client.get("/api/v1/auth/reset-password?token=private").status_code == 405
