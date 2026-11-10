import asyncio
import base64
import json
from uuid import uuid4

import pytest
from app.core.config import get_settings
from app.integrations.ai import OllamaProvider, ProviderError
from app.integrations.credentials import decrypt, encrypt
from app.main import app
from fastapi import HTTPException
from fastapi.testclient import TestClient


def test_credential_encryption_binding_and_rotation(monkeypatch):
    monkeypatch.setenv(
        "CREDENTIAL_KEYS",
        json.dumps(
            {"v1": base64.b64encode(b"a" * 32).decode(), "v2": base64.b64encode(b"b" * 32).decode()}
        ),
    )
    monkeypatch.setenv("CREDENTIAL_ACTIVE_KEY", "v1")
    get_settings.cache_clear()
    tenant, provider = uuid4(), uuid4()
    try:
        version, ciphertext = encrypt("private-test-secret", tenant, provider)
        assert "private-test-secret" not in ciphertext
        assert decrypt(version, ciphertext, tenant, provider) == "private-test-secret"
        with pytest.raises(HTTPException):
            decrypt(version, ciphertext, uuid4(), provider)
        with pytest.raises(HTTPException):
            decrypt(version, ciphertext, tenant, uuid4())
        monkeypatch.setenv("CREDENTIAL_ACTIVE_KEY", "v2")
        get_settings.cache_clear()
        rotated, replacement = encrypt(
            decrypt(version, ciphertext, tenant, provider), tenant, provider
        )
        assert rotated == "v2" and replacement != ciphertext
        assert decrypt(rotated, replacement, tenant, provider) == "private-test-secret"
    finally:
        get_settings.cache_clear()


def test_local_provider_does_not_accept_arbitrary_endpoint():
    with pytest.raises(ProviderError) as error:
        asyncio.run(OllamaProvider("test-only", "not-authorized").generate("never sent"))
    assert error.value.code == "LOCAL_ENDPOINT_NOT_AUTHORIZED"


def test_ai_requires_identity():
    prefix = f"/api/v1/spaces/{uuid4()}"
    with TestClient(app) as client:
        assert client.get(prefix + "/providers").status_code == 401
        assert client.get(prefix + "/settings").status_code == 401
        assert (
            client.post(
                prefix + "/ai-test", json={"prompt": "not sent", "consent": True}
            ).status_code
            == 401
        )
