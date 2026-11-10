import pytest
from botocore.exceptions import ClientError

from scripts import storage_iam


def test_policy_rejects_wildcard_bucket_and_excludes_administration():
    with pytest.raises(ValueError):
        storage_iam.runtime_policy("knowledge-*")
    policy = storage_iam.runtime_policy("knowledge-os")
    assert {action for statement in policy["Statement"] for action in statement["Action"]} == {
        "s3:ListBucket",
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
    }
    assert policy["Statement"][1]["Resource"] == ["arn:aws:s3:::knowledge-os/tenants/*/assets/*"]


def test_denied_requires_explicit_authorization_failure():
    def forbidden():
        raise ClientError({"ResponseMetadata": {"HTTPStatusCode": 403}}, "HeadBucket")

    storage_iam.denied(forbidden)
    for status in (404, 500):

        def failed(status=status):
            raise ClientError({"ResponseMetadata": {"HTTPStatusCode": status}}, "HeadBucket")

        with pytest.raises(RuntimeError):
            storage_iam.denied(failed)
    with pytest.raises(RuntimeError, match="unexpectedly allowed"):
        storage_iam.denied(lambda: {})
