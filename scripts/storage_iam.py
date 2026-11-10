"""Verify the runtime S3 policy with 15-minute in-memory MinIO STS credentials.

Only the isolated loopback acceptance stack is supported. Root credentials are
used by this operator drill, never passed to an application process or persisted.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import operations_check  # noqa: E402


def runtime_policy(bucket: str) -> dict:
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise ValueError("Invalid bucket name")
    template = (ROOT / "infrastructure/storage/runtime-policy.json").read_text(encoding="utf-8")
    return json.loads(template.replace("${BUCKET}", bucket))


def denied(operation: Callable) -> None:
    try:
        response = operation()
    except ClientError as error:
        if error.response["ResponseMetadata"]["HTTPStatusCode"] == 403:
            return
        raise RuntimeError("Expected explicit authorization denial") from None
    if response and "Body" in response:
        response["Body"].close()
    raise RuntimeError("Runtime S3 operation unexpectedly allowed")


@contextmanager
def application_storage(values: dict[str, str], credentials: dict) -> Iterator:
    from app.core.config import get_settings
    from app.integrations.storage import get_s3_client

    environment = {
        "S3_ENDPOINT_URL": "http://127.0.0.1:19000",
        "S3_BUCKET": values["S3_BUCKET"],
        "S3_ACCESS_KEY": credentials["AccessKeyId"],
        "S3_SECRET_KEY": credentials["SecretAccessKey"],
        "S3_SESSION_TOKEN": credentials["SessionToken"],
    }
    previous = {name: os.environ.get(name) for name in environment}
    client = None
    try:
        os.environ.update(environment)
        get_settings.cache_clear()
        get_s3_client.cache_clear()
        client = get_s3_client()
        yield client
    finally:
        if client:
            client.close()
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        get_settings.cache_clear()
        get_s3_client.cache_clear()


def run_acceptance() -> Path:
    from app.integrations.storage import tenant_object_key

    values = operations_check.load_acceptance()
    operations_check.check_containers(values)
    configuration = Config(
        connect_timeout=3,
        read_timeout=5,
        retries={"max_attempts": 0},
        proxies={},
        s3={"addressing_style": "path"},
    )
    connection = {
        "endpoint_url": "http://127.0.0.1:19000",
        "aws_access_key_id": values["S3_ACCESS_KEY"],
        "aws_secret_access_key": values["S3_SECRET_KEY"],
        "region_name": "us-east-1",
        "config": configuration,
    }
    root = boto3.client("s3", **connection)
    sts = boto3.client("sts", **connection)
    run_id = uuid4().hex
    directory = ROOT / ".cache/storage-iam" / run_id
    directory.mkdir(parents=True, exist_ok=False)
    bucket = values["S3_BUCKET"]
    tenant, asset = uuid4(), uuid4()
    key = tenant_object_key(tenant, asset)
    outside = "acceptance-iam/" + run_id
    own_keys = [key, outside]
    content = b"isolated runtime policy acceptance"
    checks = {}
    try:
        root.put_object(Bucket=bucket, Key=outside, Body=content)
        credentials = sts.assume_role(
            RoleArn="arn:aws:iam::123456789012:role/knowledge-os-runtime",
            RoleSessionName="acceptance-runtime",
            DurationSeconds=900,
            Policy=json.dumps(runtime_policy(bucket)),
        )["Credentials"]
        with application_storage(values, credentials) as client:
            # Actual application adapter, not an alternate client with root fallback.
            client.head_bucket(Bucket=bucket)
            client.put_object(Bucket=bucket, Key=key, Body=content)
            response = client.get_object(Bucket=bucket, Key=key)
            try:
                if response["Body"].read() != content:
                    raise RuntimeError("Runtime object bytes differed")
            finally:
                response["Body"].close()
            client.delete_object(Bucket=bucket, Key=key)
            checks["application_head_put_get_delete"] = True
            # MinIO may return the allowed bucket for ListBuckets when ListBucket
            # is granted. Record that real behavior; do not claim total denial.
            try:
                listed = client.list_buckets().get("Buckets", [])
                if len(listed) > 1 or any(item["Name"] != bucket for item in listed):
                    raise RuntimeError("Unrelated bucket was listed")
            except ClientError as error:
                if error.response["ResponseMetadata"]["HTTPStatusCode"] != 403:
                    raise RuntimeError("Unexpected bucket enumeration failure") from None
            checks["bucket_enumeration_limited_or_denied"] = True
            for name, operation in (
                ("bucket_policy_denied", lambda: client.get_bucket_policy(Bucket=bucket)),
                (
                    "outside_prefix_read_denied",
                    lambda: client.get_object(Bucket=bucket, Key=outside),
                ),
                (
                    "outside_prefix_write_denied",
                    lambda: client.put_object(Bucket=bucket, Key=outside, Body=b"rejected"),
                ),
                ("other_bucket_denied", lambda: client.head_bucket(Bucket="outside-" + run_id)),
            ):
                denied(operation)
                checks[name] = True
        # A narrower per-tenant session demonstrates policy intersection. The
        # shared runtime policy above intentionally serves all authorized tenants.
        narrowed = runtime_policy(bucket)
        narrowed["Statement"][1]["Resource"] = [f"arn:aws:s3:::{bucket}/tenants/{tenant}/assets/*"]
        other_key = tenant_object_key(uuid4(), uuid4())
        own_keys.append(other_key)
        root.put_object(Bucket=bucket, Key=key, Body=content)
        root.put_object(Bucket=bucket, Key=other_key, Body=content)
        try:
            narrowed_credentials = sts.assume_role(
                RoleArn="arn:aws:iam::123456789012:role/knowledge-os-runtime",
                RoleSessionName="acceptance-tenant",
                DurationSeconds=900,
                Policy=json.dumps(narrowed),
            )["Credentials"]
            with application_storage(values, narrowed_credentials) as client:
                allowed = client.get_object(Bucket=bucket, Key=key)
                allowed["Body"].close()
                denied(lambda: client.get_object(Bucket=bucket, Key=other_key))
            checks["narrower_tenant_session_denies_other_tenant"] = True
        finally:
            root.delete_object(Bucket=bucket, Key=other_key)
    except Exception as error:
        checks["failure"] = type(error).__name__
    finally:
        # Only keys generated by this run are removed. No bucket/policy/user change.
        for own_key in own_keys:
            try:
                root.delete_object(Bucket=bucket, Key=own_key)
            except Exception as error:
                checks["cleanup_failure"] = type(error).__name__
        root.close()
        sts.close()
    passed = len(checks) == 7 and all(value is True for value in checks.values())
    report = {
        "status": "passed" if passed else "failed",
        "completed_at": datetime.now(UTC).isoformat(),
        "checks": checks,
        "temporary_session_seconds": 900,
        "credentials_persisted": False,
        "production_runtime_replaced": False,
    }
    path = directory / "summary.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not passed:
        raise RuntimeError("Storage IAM acceptance failed; see sanitized report")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acceptance", action="store_true", required=True)
    parser.parse_args()
    try:
        path = run_acceptance()
    except Exception as error:
        raise SystemExit(f"Storage IAM failed ({type(error).__name__})") from None
    print(f"PASS: runtime S3 policy; report: {path}")


if __name__ == "__main__":
    main()
