"""Create a private bucket, waiting boundedly for MinIO. Never make it public."""

import time

from app.core.config import get_settings
from app.integrations.storage import get_s3_client
from botocore.exceptions import ClientError

client = get_s3_client()
bucket = get_settings().s3_bucket
for attempt in range(20):
    try:
        try:
            client.head_bucket(Bucket=bucket)
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in ("404", "NoSuchBucket", "NotFound"):
                raise
            client.create_bucket(Bucket=bucket)
        # Reject existing accidental public configuration; do not silently mutate it.
        try:
            client.get_bucket_policy(Bucket=bucket)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "NoSuchBucketPolicy":
                raise
        else:
            raise RuntimeError("Bucket has a policy; review it before continuing")
        print("Private storage bucket is ready")
        break
    except RuntimeError:
        raise
    except Exception:
        if attempt == 19:
            raise RuntimeError("Storage initialization failed") from None
        time.sleep(2)
