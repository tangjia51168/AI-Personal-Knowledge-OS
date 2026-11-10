from functools import lru_cache
from typing import Any
from uuid import UUID

import boto3
from botocore.config import Config

from app.core.config import get_settings


@lru_cache
def get_s3_client() -> Any:
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key.get_secret_value(),
        aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
        aws_session_token=settings.s3_session_token.get_secret_value() or None,
        region_name=settings.s3_region,
        config=Config(
            connect_timeout=3,
            read_timeout=3,
            retries={"max_attempts": 0},
            s3={"addressing_style": "path"},
        ),
    )


def tenant_object_key(tenant_id: UUID, asset_id: UUID) -> str:
    """No user-controlled filename/path; private bucket, no public object URLs."""
    if not isinstance(tenant_id, UUID) or not isinstance(asset_id, UUID):
        raise ValueError("Validated tenant and asset UUIDs are required")
    return f"tenants/{tenant_id}/assets/{asset_id}"
