import hmac
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

bearer = HTTPBearer(auto_error=False)


def require_operator(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> None:
    """Fail closed. This is NOT an end-user identity or tenant authorization mechanism."""
    expected = get_settings().system_api_token.get_secret_value()
    if len(expected) < 32 or expected.startswith("CHANGE_ME"):
        raise HTTPException(503, detail={"code": "OPERATOR_AUTH_UNCONFIGURED"})
    if credentials is None or not hmac.compare_digest(credentials.credentials, expected):
        raise HTTPException(401, detail={"code": "UNAUTHORIZED"})
