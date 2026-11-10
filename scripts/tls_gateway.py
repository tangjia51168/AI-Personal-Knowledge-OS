"""Prepare a disposable local certificate or verify the loopback-only TLS gateway.

No system trust changes, ACME, public DNS, notifications or production deployment.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import shutil
import socket
import ssl
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT = Path(__file__).resolve().parents[1]
TLS_ROOT = ROOT / ".cache" / "tls-gateway"
PROJECT = "knowledge-os-acceptance"


def prepare_certificate(directory: Path) -> None:
    # Exclusive files; never replace an operator's existing key or certificate.
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise RuntimeError("TLS directory is not empty; existing files retained")
    now = datetime.now(UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Knowledge OS local test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=7))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=7))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    files = {
        "ca.crt": ca.public_bytes(serialization.Encoding.PEM),
        "server.crt": certificate.public_bytes(serialization.Encoding.PEM),
        "server.key": key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    }
    for name, data in files.items():
        # The unprivileged container must read this disposable test key. This is
        # deliberately not production key custody; the CA private key is discarded.
        descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(descriptor, "wb") as target:
            target.write(data)


def docker(*arguments: str) -> bytes:
    command = shutil.which("docker") or "C:/Program Files/Docker/Docker/resources/bin/docker.exe"
    result = subprocess.run([command, *arguments], capture_output=True, check=False, timeout=25)
    if result.returncode:
        raise RuntimeError("Gateway Docker check failed")
    return result.stdout


def inspect_gateway() -> dict:
    ids = (
        docker(
            "ps",
            "--filter",
            f"label=com.docker.compose.project={PROJECT}",
            "--filter",
            "label=com.docker.compose.service=gateway",
            "--quiet",
        )
        .decode()
        .split()
    )
    if len(ids) != 1:
        raise RuntimeError("Expected one isolated gateway")
    info = json.loads(docker("inspect", ids[0]))[0]
    labels = info["Config"]["Labels"]
    ports = info["NetworkSettings"]["Ports"]
    if (
        labels.get("com.docker.compose.project") != PROJECT
        or labels.get("com.docker.compose.service") != "gateway"
        or info["Config"]["User"] != "101:101"
        or not info["HostConfig"]["ReadonlyRootfs"]
        or info["HostConfig"]["Memory"] <= 0
        or info["HostConfig"]["NanoCpus"] <= 0
    ):
        raise RuntimeError("Gateway isolation requirements failed")
    expected = {"8080/tcp": "14080", "8443/tcp": "14443"}
    if {key for key, value in ports.items() if value} != set(expected):
        raise RuntimeError("Unexpected gateway published ports")
    for port, host_port in expected.items():
        if ports.get(port) != [{"HostIp": "127.0.0.1", "HostPort": host_port}]:
            raise RuntimeError("Gateway must bind only the isolated loopback ports")
    docker("exec", ids[0], "nginx", "-t")
    return {"loopback_only": True, "nonroot_readonly": True, "configuration_valid": True}


def verify_protocols(ca_path: Path) -> list[str]:
    protocols = []
    for version, expected in (
        (ssl.TLSVersion.TLSv1_2, "TLSv1.2"),
        (ssl.TLSVersion.TLSv1_3, "TLSv1.3"),
    ):
        # A normal negotiation proves only the preferred protocol. Pin both ends
        # of the range separately; certificate and hostname checks stay enabled.
        context = ssl.create_default_context(cafile=str(ca_path))
        context.minimum_version = context.maximum_version = version
        with socket.create_connection(("127.0.0.1", 14443), timeout=5) as connection:
            with context.wrap_socket(connection, server_hostname="localhost") as tls:
                if tls.version() != expected:
                    raise RuntimeError("Required TLS protocol was not negotiated")
        protocols.append(expected)
    return protocols


def verify_http(ca_path: Path) -> dict:
    protocols = verify_protocols(ca_path)
    context = ssl.create_default_context(cafile=str(ca_path))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with httpx.Client(
        base_url="https://localhost:14443",
        verify=context,
        timeout=15,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        entry = client.get("/")
        if entry.status_code != 302 or entry.headers.get("location") != "/account":
            raise RuntimeError("Restricted gateway must enter the account page")
        for path in ("/account",):
            response = client.get(path)
            if response.status_code != 200 or "Knowledge OS" not in response.text:
                raise RuntimeError("Real web page unavailable through TLS")
            if (
                response.headers.get("x-content-type-options") != "nosniff"
                or response.headers.get("referrer-policy") != "no-referrer"
                or response.headers.get("x-frame-options") != "DENY"
            ):
                raise RuntimeError("Gateway response security headers missing")
        for path in (
            "/api/system",
            "/api/v1/system/metrics",
            "/api/v1/auth/me",
            "/health/live",
            "/health/ready",
            "/docs",
            "/redoc",
            "/openapi.json",
        ):
            if client.get(path).status_code != 404:
                raise RuntimeError("Diagnostic or direct API route was exposed")
        if client.get("/api/account/spaces").status_code != 401:
            raise RuntimeError("Anonymous BFF authorization failed")
        invalid_session = client.get(
            "/api/account/auth/me", headers={"Cookie": "knowledge_session=" + "a" * 43}
        )
        clearing = invalid_session.headers.get("set-cookie", "").lower()
        if invalid_session.status_code != 401 or not all(
            value in clearing
            for value in ("httponly", "secure", "samesite=strict", "max-age=0", "path=/api/account")
        ):
            raise RuntimeError("TLS session clearing cookie protections missing")
        denied = client.post(
            "/api/account/auth/login",
            headers={"Origin": "https://untrusted.invalid", "X-Knowledge-Request": "1"},
            json={},
        )
        if denied.status_code != 403:
            raise RuntimeError("Cross-origin login accepted through TLS")
        if client.get("/", headers={"Host": "untrusted.invalid"}).status_code != 421:
            raise RuntimeError("Untrusted Host accepted")
        if (
            client.post(
                "/api/account/auth/login", content=b"x" * (10 * 1024 * 1024 + 1)
            ).status_code
            != 413
        ):
            raise RuntimeError("Gateway body limit not enforced")
        # Every request claims a different forwarded address. It must still hit
        # the gateway's one actual-client bucket and return at least one 429.
        statuses = [
            client.get(
                "/api/account/auth/me", headers={"X-Forwarded-For": f"192.0.2.{i}"}
            ).status_code
            for i in range(20)
        ]
        if 429 not in statuses or not set(statuses) <= {401, 429}:
            raise RuntimeError("Gateway authentication rate limit failed")
    with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
        redirect = client.get("http://127.0.0.1:14080/account?signature=discard-this")
        if redirect.status_code != 308 or redirect.headers.get("location") != (
            "https://localhost:14443"
        ):
            raise RuntimeError("HTTP redirect must use the fixed HTTPS origin")
    return {
        "certificate_verified": True,
        "tls_protocol": protocols[-1],
        "tls_protocols": protocols,
        "real_pages": 1,
        "account_entry_redirect": True,
        "diagnostic_routes_blocked": 8,
        "bff_anonymous_denied": True,
        "secure_httponly_session_clearing": True,
        "cross_origin_login_rejected": True,
        "host_rejected": True,
        "body_limit": True,
        "forwarded_ip_rate_limit": True,
        "fixed_http_redirect": True,
    }


def run_acceptance() -> Path:
    report_directory = ROOT / ".cache" / "gateway-reports" / uuid4().hex
    report_directory.mkdir(parents=True, exist_ok=False)
    checks = {}
    for name, check in (
        ("container", inspect_gateway),
        ("https", lambda: verify_http(TLS_ROOT / "ca.crt")),
    ):
        try:
            checks[name] = {"status": "passed", **check()}
        except Exception as error:
            checks[name] = {"status": "failed", "error": type(error).__name__}
    passed = all(item["status"] == "passed" for item in checks.values())
    report = {
        "status": "passed" if passed else "failed",
        "completed_at": datetime.now(UTC).isoformat(),
        "project": PROJECT,
        "checks": checks,
        "production_tls_verified": False,
    }
    path = report_directory / "summary.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not passed:
        raise RuntimeError(f"Gateway acceptance failed; sanitized report: {path}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--acceptance", action="store_true")
    args = parser.parse_args()
    try:
        if args.prepare:
            prepare_certificate(TLS_ROOT)
            print("Prepared seven-day localhost test certificate; no system trust changes")
        else:
            print(f"PASS: local TLS gateway; report: {run_acceptance()}")
    except Exception as error:
        raise SystemExit(f"Local TLS gateway failed ({type(error).__name__})") from None


if __name__ == "__main__":
    main()
