from app.main import app
from app.modules.identity.passwords import hash_password, verify_password
from fastapi.testclient import TestClient


def test_scrypt_is_salted_and_verification_is_bounded():
    password = "long-test-password-安全"
    first, second = hash_password(password), hash_password(password)
    assert first != second and password not in first
    assert verify_password(password, first)
    assert not verify_password("incorrect-password", first)
    assert not verify_password(password, first.replace("32768", "999999999"))
    assert not verify_password(password, "malformed")


def test_all_identity_and_space_routes_fail_closed_without_auth():
    client = TestClient(app)
    for method, path in (
        ("GET", "/api/v1/auth/me"),
        ("GET", "/api/v1/auth/security-events"),
        ("POST", "/api/v1/auth/logout"),
        ("GET", "/api/v1/spaces"),
        ("GET", "/api/v1/spaces/00000000-0000-0000-0000-000000000001"),
    ):
        assert client.request(method, path).status_code == 401


def test_password_validation_never_echoes_password():
    response = TestClient(app).post(
        "/api/v1/auth/register", json={"email": "valid@example.invalid", "password": "secret"}
    )
    assert response.status_code == 422
    assert "secret" not in response.text
