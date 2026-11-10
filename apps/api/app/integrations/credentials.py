"""Versioned authenticated encryption; tenant/provider binding prevents ciphertext swaps."""

import base64
import json
import secrets
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException

from app.core.config import get_settings


def keyring() -> dict[str, bytes]:
    try:
        keys = json.loads(get_settings().credential_keys.get_secret_value())
        result = {
            version: base64.b64decode(value, validate=True) for version, value in keys.items()
        }
        if not result or any(len(value) != 32 for value in result.values()):
            raise ValueError("Invalid keyring")
        return result
    except Exception:
        raise HTTPException(503, detail={"code": "CREDENTIAL_VAULT_UNAVAILABLE"}) from None


def encrypt(secret: str, tenant_id: UUID, provider_id: UUID) -> tuple[str, str]:
    version = get_settings().credential_active_key
    keys = keyring()
    if version not in keys:
        raise HTTPException(503, detail={"code": "CREDENTIAL_VAULT_UNAVAILABLE"})
    nonce = secrets.token_bytes(12)
    aad = f"provider-v1:{tenant_id}:{provider_id}".encode()
    ciphertext = AESGCM(keys[version]).encrypt(nonce, secret.encode(), aad)
    return version, base64.b64encode(nonce + ciphertext).decode()


def decrypt(version: str, ciphertext: str, tenant_id: UUID, provider_id: UUID) -> str:
    keys = keyring()
    try:
        raw = base64.b64decode(ciphertext, validate=True)
        aad = f"provider-v1:{tenant_id}:{provider_id}".encode()
        return AESGCM(keys[version]).decrypt(raw[:12], raw[12:], aad).decode()
    except Exception:
        raise HTTPException(503, detail={"code": "CREDENTIAL_DECRYPT_FAILED"}) from None
