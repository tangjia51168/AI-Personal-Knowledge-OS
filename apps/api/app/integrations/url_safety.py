"""URL extraction and fetch-boundary checks for Phase 1 source intake.

This module never fetches a URL. A collector must call
``assert_public_resolution`` immediately before connecting and after every
redirect, because DNS answers can change between inspection and fetch.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit

MAX_URL_LENGTH = 2048
MAX_CANDIDATES = 20
_CONTROL = re.compile(r"[\x00-\x20\x7f]")
_URL_IN_TEXT = re.compile(r"(?i)(?:https?://|www\.)[^\s<>\"']+")
_LOCAL_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


@dataclass(frozen=True)
class URLCandidate:
    original_url: str
    canonical_url: str | None
    accepted: bool
    reason: str | None = None


def _reject_reason(parsed: SplitResult) -> str | None:
    if parsed.scheme not in {"http", "https"}:
        return "UNSUPPORTED_SCHEME"
    if not parsed.netloc or parsed.username is not None or parsed.password is not None:
        return "URL_CREDENTIALS_OR_AUTHORITY"
    host = parsed.hostname
    if host is None:
        return "MISSING_HOST"
    try:
        # Apply policy to the same IDNA host that a future connector would use.
        host_lower = host.encode("idna").decode("ascii").rstrip(".").lower()
    except UnicodeError:
        return "INVALID_HOST"
    if host_lower in _LOCAL_NAMES or host_lower.endswith((".localhost", ".local", ".internal")):
        return "PRIVATE_HOSTNAME"
    try:
        address = ipaddress.ip_address(host_lower)
    except ValueError:
        address = None
    if address is not None and (not address.is_global or address.is_multicast):
        return "PRIVATE_IP"
    if address is None:
        labels = host_lower.split(".")
        if (
            len(host_lower) > 253
            or len(labels) < 2
            or labels[-1].isdigit()
            or not all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
            )
        ):
            return "INVALID_HOST"
    try:
        port = parsed.port
    except ValueError:
        return "INVALID_PORT"
    if port is not None and not 1 <= port <= 65535:
        return "INVALID_PORT"
    return None


def canonicalize_url(value: str) -> str:
    """Validate and canonicalize one user-supplied URL without doing I/O."""

    if not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH:
        raise ValueError("URL_TOO_LONG_OR_EMPTY")
    if _CONTROL.search(value):
        raise ValueError("URL_CONTROL_CHARACTER")
    candidate = value.strip()
    if candidate.lower().startswith("www."):
        candidate = "https://" + candidate
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        raise ValueError("INVALID_URL") from None
    reason = _reject_reason(parsed)
    if reason:
        raise ValueError(reason)
    host = parsed.hostname
    assert host is not None
    try:
        ascii_host = host.encode("idna").decode("ascii").lower().rstrip(".")
        port = parsed.port
    except (UnicodeError, ValueError):
        raise ValueError("INVALID_HOST") from None
    authority = f"[{ascii_host}]" if ":" in ascii_host else ascii_host
    if port is not None and not (
        (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)
    ):
        authority = f"{authority}:{port}"
    path = parsed.path or "/"
    canonical = urlunsplit((parsed.scheme.lower(), authority, path, parsed.query, ""))
    if len(canonical) > MAX_URL_LENGTH:
        raise ValueError("URL_TOO_LONG_OR_EMPTY")
    return canonical


def inspect_url(value: str) -> URLCandidate:
    original = value.strip()
    try:
        canonical = canonicalize_url(original)
    except ValueError as exc:
        return URLCandidate(original, None, False, str(exc))
    return URLCandidate(original, canonical, True)


def extract_urls(
    text: str, *, limit: int = MAX_CANDIDATES, preserve_variants: bool = False
) -> list[URLCandidate]:
    """Extract bounded URL candidates from pasted share text."""

    if not isinstance(text, str) or len(text) > 16 * 1024:
        raise ValueError("TEXT_TOO_LONG")
    found: list[URLCandidate] = []
    seen: set[str] = set()
    for match in _URL_IN_TEXT.finditer(text):
        raw = match.group(0).rstrip(".,;:!?，。；：！？")
        # Trim share-text wrappers only when unmatched. Closing IPv6 brackets
        # and balanced path parentheses are part of the URL and must survive.
        closing = {")": "(", "]": "[", "}": "{", "）": "（", "】": "【", "》": "《"}
        while raw and raw[-1] in closing and raw.count(raw[-1]) > raw.count(closing[raw[-1]]):
            raw = raw[:-1].rstrip(".,;:!?，。；：！？")
        item = inspect_url(raw)
        key = item.original_url if preserve_variants else item.canonical_url or item.original_url
        if key in seen:
            continue
        seen.add(key)
        found.append(item)
        if len(found) >= limit:
            break
    return found


def assert_public_resolution(host: str, port: int) -> list[str]:
    """Resolve a host and reject any private answer before a collector connects."""

    if not host or not 1 <= port <= 65535:
        raise ValueError("INVALID_ENDPOINT")
    try:
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise ValueError("DNS_RESOLUTION_FAILED") from None
    addresses = sorted({str(row[4][0]) for row in answers})
    if not addresses:
        raise ValueError("DNS_RESOLUTION_EMPTY")
    for value in addresses:
        address = ipaddress.ip_address(value)
        if not address.is_global or address.is_multicast:
            raise ValueError("PRIVATE_DNS_RESULT")
    return addresses
