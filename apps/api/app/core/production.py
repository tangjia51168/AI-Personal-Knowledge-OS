"""Fail closed at runtime; a passing preflight is not a release approval."""

import base64
import ipaddress
import json
import re
from urllib.parse import SplitResult, parse_qsl, unquote, urlsplit

from app.core.config import Settings


def _clean_url(value: str) -> bool:
    # urlsplit discards some controls; reject them before interpreting authority.
    return not any(ord(character) <= 32 or ord(character) == 127 for character in value) and (
        "\\" not in value
    )


def _valid_host_port(parsed: SplitResult) -> bool:
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    if not hostname or parsed.netloc.endswith(":") or port is not None and port < 1:
        return False
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return len(hostname) <= 253 and all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in hostname.rstrip(".").split(".")
        )
    return True


def _safe_public_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
    except ValueError:
        return False
    if (
        not _clean_url(value)
        or not _valid_host_port(parsed)
        or parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        return False
    hostname = hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(
        (".localhost", ".local", ".invalid")
    ):
        return False
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return bool(re.fullmatch(r"[A-Za-z0-9.-]+", hostname))
    return False


def _safe_redis_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        if (
            not _clean_url(value)
            or ";" in value  # Celery interprets semicolons as broker failover URLs.
            or parsed.scheme not in {"redis", "rediss"}
            or not _valid_host_port(parsed)
            or not parsed.password
            or parsed.fragment
            or not re.fullmatch(r"(?:/[0-9]+|/)?", parsed.path)
        ):
            return False
        password = unquote(parsed.password)
        if not password or any(
            ord(character) < 32 or ord(character) == 127 for character in password
        ):
            return False
        parameters = parse_qsl(
            parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=5
        )
        if parsed.scheme == "redis":
            return not parameters
        options = dict(parameters)
        # All three consumers (redis-py, Kombu, Celery result backend) accept
        # these TLS settings. No URL query may override host, credentials or DB.
        if (
            len(options) != len(parameters)
            or set(options)
            - {"ssl_cert_reqs", "ssl_ca_certs", "ssl_certfile", "ssl_keyfile", "ssl_check_hostname"}
            or options.get("ssl_cert_reqs") != "required"
            or options.get("ssl_check_hostname", "true") != "true"
            or bool(options.get("ssl_certfile")) != bool(options.get("ssl_keyfile"))
            or any(
                not option
                or any(ord(character) < 32 or ord(character) == 127 for character in option)
                for option in options.values()
            )
        ):
            return False
        return True
    except ValueError:
        return False


def validate_production(settings: Settings) -> None:
    if settings.app_env != "production":
        return
    issues = []
    secrets = [
        settings.system_api_token.get_secret_value(),
        settings.download_signing_key.get_secret_value(),
        settings.s3_secret_key.get_secret_value(),
    ]
    if any(len(value) < 32 or value.startswith("CHANGE_ME") for value in secrets):
        issues.append("strong independent secrets required")
    if len(set(secrets)) != len(secrets):
        issues.append("secrets must be independent")
    if settings.migration_database_url is not None:
        issues.append("runtime must not hold migration credentials")
    if not settings.s3_access_key.get_secret_value():
        issues.append("object storage access identity required")
    try:
        storage = urlsplit(settings.s3_endpoint_url)
        unsafe_storage = (
            not _clean_url(settings.s3_endpoint_url)
            or not _valid_host_port(storage)
            or storage.scheme != "https"
            or storage.hostname is None
            or storage.username is not None
            or storage.password is not None
            or storage.query
            or storage.fragment
        )
    except ValueError:
        unsafe_storage = True
    if unsafe_storage:
        issues.append("production object storage requires a clean HTTPS endpoint")
    if settings.registration_enabled:
        issues.append("public registration requires verified email delivery before enablement")
    if settings.email_verification_required:
        from app.integrations.mail import MailDeliveryError, account_link, valid_address

        try:
            account_link(settings.public_app_url, "verify_email", "a" * 43, True)
            if (
                not settings.smtp_host
                or not valid_address(settings.smtp_from)
                or settings.smtp_security == "plain"
                or not settings.smtp_username
                or not settings.smtp_password.get_secret_value()
            ):
                raise MailDeliveryError()
        except MailDeliveryError:
            issues.append("verified email requires authenticated TLS SMTP and public HTTPS URL")
    if not settings.trusted_hosts or "*" in settings.trusted_hosts:
        issues.append("explicit trusted hosts required")
    if not _safe_public_origin(settings.public_app_url):
        issues.append("production public HTTPS URL required")
    if not settings.cors_origins or any(
        not _safe_public_origin(origin) for origin in settings.cors_origins
    ):
        issues.append("explicit HTTPS origins required")
    if not _safe_redis_url(settings.redis_url.get_secret_value()):
        issues.append("authenticated Redis required")
    try:
        keys = json.loads(settings.credential_keys.get_secret_value())
        decoded = {
            version: base64.b64decode(value, validate=True) for version, value in keys.items()
        }
        if settings.credential_active_key not in decoded or any(
            len(value) != 32 or len(set(value)) < 8 for value in decoded.values()
        ):
            raise ValueError
    except Exception:
        issues.append("private versioned credential keyring required")
    if issues:
        # Field names and fixed errors only; never include invalid values.
        raise RuntimeError("Production preflight failed: " + "; ".join(issues))
