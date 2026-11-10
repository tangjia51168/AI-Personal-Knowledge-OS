"""Versioned salted scrypt hashes. Bounded inputs and work factors on verification."""

import hashlib
import hmac
import secrets


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=1, maxmem=67108864)
    return f"scrypt$32768$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = encoded.split("$")
        if (scheme, n, r, p) != ("scrypt", "32768", "8", "1") or len(password) > 256:
            return False
        if len(salt) != 32 or len(expected) != 128:
            return False
        actual = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1, maxmem=67108864
        )
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False
