"""Atomic Redis limits; do not trust spoofable forwarded IP headers. Fail closed."""

import hashlib

from fastapi import HTTPException, Request
from redis import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings

SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return count
"""


def rate_limit(request: Request, kind: str, identity: str = "") -> None:
    address = request.client.host if request.client else "unknown"
    limit, window = (get_settings().auth_register_limit, 3600) if kind == "register" else (15, 900)
    try:
        with Redis.from_url(
            get_settings().redis_url.get_secret_value(), socket_timeout=3, socket_connect_timeout=3
        ) as client:
            subjects = [f"ip:{address}"]
            if identity:
                subjects.append(f"identity:{identity}")
            for subject in subjects:
                digest = hashlib.sha256(subject.encode()).hexdigest()
                count = int(client.eval(SCRIPT, 1, f"auth-limit:{kind}:{digest}", window))
                if count > limit:
                    raise HTTPException(
                        429, detail={"code": "RATE_LIMITED"}, headers={"Retry-After": str(window)}
                    )
    except RedisError:
        raise HTTPException(503, detail={"code": "AUTH_UNAVAILABLE"}) from None
