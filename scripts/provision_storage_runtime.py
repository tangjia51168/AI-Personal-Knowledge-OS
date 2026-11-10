"""Provision a scoped local MinIO account without rotating existing identities.

Only the isolated acceptance stack is supported. Verified mc runs in a temporary,
nonroot container. Credentials travel through stdin, never command arguments,
Docker container environment, mc config files, or displayed subprocess output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.config import Config
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import operations_check, storage_iam  # noqa: E402

MC_VERSION = "RELEASE.2025-04-16T18-13-26Z"
MC_SIZE = 30179512
MC_SHA256 = "ac90da87a35641be5a0ac75d49de5161ddb47d629b5ba01261b0ae9e00aea15f"
MC_FILE = ROOT / ".cache/minio-mc/mc"
RUNTIME_FILE = ROOT / ".env.storage-runtime.acceptance"
MC_IMAGE = "knowledge-os/minio:RELEASE.2025-04-22T22-12-26Z-local"


def verify_mc() -> None:
    if (
        not MC_FILE.is_file()
        or MC_FILE.stat().st_size != MC_SIZE
        or hashlib.sha256(MC_FILE.read_bytes()).hexdigest() != MC_SHA256
    ):
        raise RuntimeError("Verified official mc artifact unavailable; see storage runtime docs")


def save_credentials() -> dict[str, str]:
    if RUNTIME_FILE.exists():
        values = {
            key: value
            for key, value in dotenv_values(RUNTIME_FILE, interpolate=False).items()
            if value is not None
        }
        for key in ("S3_RUNTIME_ACCESS_KEY", "S3_RUNTIME_SECRET_KEY", "S3_RUNTIME_POLICY"):
            if not values.get(key):
                raise RuntimeError("Existing runtime credential file is incomplete; left unchanged")
        return values
    identifier = uuid4().hex
    values = {
        "S3_RUNTIME_ACCESS_KEY": "kosruntime" + identifier,
        "S3_RUNTIME_SECRET_KEY": secrets.token_hex(32),
        "S3_RUNTIME_POLICY": "kos-runtime-" + identifier,
        "S3_RUNTIME_STATE": "pending",
    }
    content = "# Local scoped storage identity. Never commit or print.\n" + "".join(
        f"{key}={value}\n" for key, value in values.items()
    )
    descriptor = os.open(RUNTIME_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    return values


def runtime_client(operator: dict, runtime: dict):
    return boto3.client(
        "s3",
        endpoint_url=operator["S3_ENDPOINT_URL"],
        aws_access_key_id=runtime["S3_RUNTIME_ACCESS_KEY"],
        aws_secret_access_key=runtime["S3_RUNTIME_SECRET_KEY"],
        region_name=operator.get("S3_REGION", "us-east-1"),
        config=Config(
            connect_timeout=3,
            read_timeout=5,
            retries={"max_attempts": 0},
            proxies={},
            s3={"addressing_style": "path"},
        ),
    )


def check_credentials(operator: dict, runtime: dict) -> None:
    if (
        runtime["S3_RUNTIME_ACCESS_KEY"] == operator["S3_ACCESS_KEY"]
        or runtime["S3_RUNTIME_SECRET_KEY"] == operator["S3_SECRET_KEY"]
    ):
        raise RuntimeError("Runtime identity must differ from operator")
    client = runtime_client(operator, runtime)
    key = f"tenants/{uuid4()}/assets/{uuid4()}"
    outside = "acceptance-runtime-denied/" + uuid4().hex
    try:
        client.head_bucket(Bucket=operator["S3_BUCKET"])
        client.put_object(Bucket=operator["S3_BUCKET"], Key=key, Body=b"scoped-identity")
        response = client.get_object(Bucket=operator["S3_BUCKET"], Key=key)
        try:
            if response["Body"].read() != b"scoped-identity":
                raise RuntimeError("Scoped storage bytes differed")
        finally:
            response["Body"].close()
        storage_iam.denied(lambda: client.get_bucket_policy(Bucket=operator["S3_BUCKET"]))
        try:
            storage_iam.denied(
                lambda: client.put_object(Bucket=operator["S3_BUCKET"], Key=outside, Body=b"denied")
            )
        except RuntimeError:
            # A mistakenly broad identity may have written the generated canary.
            # Clean up only that exact key before preserving the failed result.
            client.delete_object(Bucket=operator["S3_BUCKET"], Key=outside)
            raise
        storage_iam.denied(lambda: client.head_bucket(Bucket="outside-" + uuid4().hex))
    finally:
        try:
            client.delete_object(Bucket=operator["S3_BUCKET"], Key=key)
        finally:
            client.close()


def mark_ready() -> None:
    content = RUNTIME_FILE.read_text(encoding="utf-8")
    if "S3_RUNTIME_STATE=pending" not in content:
        raise RuntimeError("Unexpected runtime file state; left unchanged")
    temporary = RUNTIME_FILE.with_name(RUNTIME_FILE.name + ".tmp-" + uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content.replace("S3_RUNTIME_STATE=pending", "S3_RUNTIME_STATE=ready"))
    os.replace(temporary, RUNTIME_FILE)


def provision(operator: dict, runtime: dict) -> None:
    verify_mc()
    user = runtime["S3_RUNTIME_ACCESS_KEY"]
    policy_name = runtime["S3_RUNTIME_POLICY"]
    # Exact generated names only. Existing unrecognised files never target an account.
    if (
        not re.fullmatch(r"kosruntime[a-f0-9]{32}", user)
        or not re.fullmatch(r"kos-runtime-[a-f0-9]{32}", policy_name)
        or not re.fullmatch(r"[a-f0-9]{64}", runtime["S3_RUNTIME_SECRET_KEY"])
    ):
        raise RuntimeError("Unrecognised runtime identity; left unchanged")
    if runtime.get("S3_RUNTIME_STATE") == "ready":
        check_credentials(operator, runtime)
        return
    if runtime.get("S3_RUNTIME_STATE") != "pending":
        raise RuntimeError("Unrecognised runtime state; left unchanged")
    try:
        check_credentials(operator, runtime)
    except Exception:
        # Only a successful administrator listing below may prove the generated
        # user absent. Authentication/network errors never permit an overwrite.
        pass
    else:
        # A crash after the full grant but before saving state is recoverable
        # using the same credential, without calling an administrator mutation.
        mark_ready()
        return
    policy = storage_iam.runtime_policy(operator["S3_BUCKET"])
    # Python child reads one JSON input, launches mc without a shell, and discards
    # every mc output. No credential-bearing environment is saved in Docker config.
    source = """
import json, os, subprocess, sys
from urllib.parse import quote
p = json.load(sys.stdin)
environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "MC_CONFIG_DIR": "/tmp/mc"}
environment["MC_HOST_local"] = (
    "http://" + quote(p["root_user"], safe="") + ":"
    + quote(p["root_secret"], safe="") + "@minio:9000"
)
def mc(arguments, data=None):
    return subprocess.run(["/tools/mc", *arguments], input=data, env=environment,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=False).returncode
# Only a successful complete listing proves the generated username absent.
# A lookup/network error is never interpreted as absence or permission to reset.
listing = subprocess.run(["/tools/mc", "--json", "admin", "user", "list", "local"],
    env=environment, capture_output=True, timeout=20, check=False)
if listing.returncode or len(listing.stdout) > 1048576:
    sys.exit(3)
for line in listing.stdout.splitlines():
    entry = json.loads(line)
    if entry.get("status") != "success" or not entry.get("accessKey"):
        sys.exit(4)
    if entry["accessKey"] == p["user"]:
        sys.exit(5)
data = (p["user"] + "\\n" + p["secret"] + "\\n").encode()
if mc(["admin", "user", "add", "local"], data) != 0:
    sys.exit(6)
with open("/tmp/policy.json", "w") as f:
    json.dump(p["policy"], f)
if mc(["admin", "policy", "create", "local", p["policy_name"], "/tmp/policy.json"]) != 0:
    sys.exit(6)
if mc(["admin", "policy", "attach", "local", p["policy_name"], "--user", p["user"]]) != 0:
    sys.exit(7)
"""
    # A ready file never reruns user add. Pending retries are fail-closed if the
    # account exists: operator must inspect rather than overwrite credentials.
    payload = {
        "root_user": operator["S3_ACCESS_KEY"],
        "root_secret": operator["S3_SECRET_KEY"],
        "user": user,
        "secret": runtime["S3_RUNTIME_SECRET_KEY"],
        "policy_name": policy_name,
        "policy": policy,
    }
    # Interpreter code is public; JSON follows through stdin to fd0 using -c.
    result = subprocess.run(
        [
            operations_check.docker_path(),
            "run",
            "--rm",
            "-i",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--memory=192m",
            "--cpus=0.5",
            "--pids-limit=64",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16777216,mode=1777",
            "--network",
            operations_check.PROJECT + "_default",
            "--mount",
            f"type=bind,source={MC_FILE.parent},target=/tools,readonly",
            "--entrypoint",
            "python",
            MC_IMAGE,
            "-c",
            source,
        ],
        input=json.dumps(payload).encode(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=100,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "Scoped account provisioning failed; credentials retained, never overwritten"
        )
    check_credentials(operator, runtime)
    mark_ready()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acceptance", action="store_true", required=True)
    parser.parse_args()
    try:
        operator = operations_check.load_acceptance()
        runtime = save_credentials()
        provision(operator, runtime)
    except Exception as error:
        raise SystemExit(f"Storage runtime provisioning failed ({type(error).__name__})") from None
    print("PASS: scoped local storage identity configured; secrets not displayed")


if __name__ == "__main__":
    main()
