"""Small HTTP transport with DNS pinning and a deadline across all redirects.

No proxy, cookie jar, authentication, compression or HTML subresource loading.
The TCP socket connects to an already checked address; TLS still verifies the
original hostname. DNS uses bounded daemon slots so a stuck OS resolver cannot
block a Worker forever or spawn an unbounded number of threads.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlsplit

from app.integrations.platforms.base import CollectionError
from app.integrations.url_safety import canonicalize_url

MAX_BODY = 1024 * 1024
TOTAL_SECONDS = 20
MAX_REDIRECTS = 3
_DNS_SLOTS = threading.BoundedSemaphore(4)
_REDIRECTS = {301, 302, 303, 307, 308}
_TRANSITION_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32", "::ffff:0:0/96")
)


@dataclass(frozen=True)
class FetchResult:
    url: str
    body: bytes
    content_type: str
    charset: str


class PinnedConnection(http.client.HTTPConnection):
    """Retain our socket while a Connection: close response is being read."""

    def getresponse(self) -> http.client.HTTPResponse:
        # HTTPConnection normally closes its socket object immediately for a
        # nonpersistent response (the response file retains a handle). Keep the
        # original socket so the total-deadline watchdog can still interrupt it.
        self._reading_response = True
        try:
            return super().getresponse()
        finally:
            self._reading_response = False

    def close(self) -> None:
        if getattr(self, "_reading_response", False):
            return
        super().close()


def remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        raise CollectionError("COLLECTION_TIMEOUT")
    return value


def checked_url(value: str) -> str:
    try:
        result = canonicalize_url(value)
    except (ValueError, UnicodeError):
        raise CollectionError("COLLECTION_UNSAFE_URL") from None
    parsed = urlsplit(result)
    # Alternate service ports add little value to an article collector and make
    # it usable as a port scanner. HTTPS upgrades from HTTP are allowed below.
    if parsed.port not in (None, 80 if parsed.scheme == "http" else 443):
        raise CollectionError("COLLECTION_PORT_NOT_ALLOWED")
    return result


def resolve_public(host: str, port: int, deadline: float) -> list[tuple[int, int, tuple]]:
    if not _DNS_SLOTS.acquire(blocking=False):
        raise CollectionError("COLLECTION_DNS_BUSY")
    finished = threading.Event()
    output: list[tuple[int, int, tuple]] = []
    failed = False

    def resolve() -> None:
        nonlocal failed
        try:
            answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            for family, _kind, protocol, _name, address in answers:
                ip = ipaddress.ip_address(address[0])
                if family not in (socket.AF_INET, socket.AF_INET6) or not (
                    ip.is_global
                    and not ip.is_multicast
                    and not any(ip in network for network in _TRANSITION_NETWORKS)
                ):
                    failed = True
                    break
                entry = (int(family), protocol, address)
                if entry not in output:
                    output.append(entry)
        except Exception:
            failed = True
        finally:
            _DNS_SLOTS.release()
            finished.set()

    threading.Thread(target=resolve, name="source-dns", daemon=True).start()
    if not finished.wait(remaining(deadline)):
        raise CollectionError("COLLECTION_TIMEOUT")
    remaining(deadline)
    if failed or not output:
        raise CollectionError("COLLECTION_DNS_NOT_PUBLIC")
    return output


def open_pinned(url: str, deadline: float) -> http.client.HTTPConnection:
    parsed = urlsplit(url)
    host = parsed.hostname
    assert host is not None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = resolve_public(host, port, deadline)
    for family, protocol, address in addresses[:4]:
        sock = socket.socket(family, socket.SOCK_STREAM, protocol)
        try:
            sock.settimeout(min(3.0, remaining(deadline)))
            sock.connect(address)
            if ipaddress.ip_address(sock.getpeername()[0]) != ipaddress.ip_address(address[0]):
                raise CollectionError("COLLECTION_PEER_MISMATCH")
            if parsed.scheme == "https":
                sock.settimeout(min(3.0, remaining(deadline)))
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            connection = PinnedConnection(host, port, timeout=remaining(deadline))
            connection.auto_open = 0
            connection.sock = sock
            return connection
        except CollectionError:
            sock.close()
            raise
        except (OSError, ValueError):
            sock.close()
    raise CollectionError("COLLECTION_CONNECT_FAILED")


def fetch(url: str, *, allowed_types: frozenset[str], deadline: float | None = None) -> FetchResult:
    stop = time.monotonic() + TOTAL_SECONDS if deadline is None else deadline
    current = checked_url(url)
    for redirects in range(MAX_REDIRECTS + 1):
        connection = open_pinned(current, stop)
        assert connection.sock is not None
        transport_socket = connection.sock

        def expire(sock: socket.socket = transport_socket) -> None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        # HTTP header parsing contains repeated socket reads. A socket timeout
        # alone is an inactivity timeout and does not enforce an overall budget.
        timer = threading.Timer(max(0.0, stop - time.monotonic()), expire)
        timer.daemon = True
        timer.start()
        response: http.client.HTTPResponse | None = None
        try:
            parsed = urlsplit(current)
            target = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
            if parsed.query:
                target += "?" + quote(parsed.query, safe="%/?@!$&'()*+,;=:-._~")
            transport_socket.settimeout(remaining(stop))
            connection.request(
                "GET",
                target,
                headers={
                    "Accept": ", ".join(sorted(allowed_types)),
                    "Accept-Encoding": "identity",
                    "User-Agent": "KnowledgeOS/0.1 (public-content-collector)",
                    "Connection": "close",
                },
            )
            transport_socket.settimeout(remaining(stop))
            response = connection.getresponse()
            remaining(stop)
            if response.status in _REDIRECTS:
                if redirects == MAX_REDIRECTS:
                    raise CollectionError("COLLECTION_REDIRECT_LIMIT")
                location = response.getheader("Location")
                if not location:
                    raise CollectionError("COLLECTION_REDIRECT_INVALID")
                destination = checked_url(urljoin(current, location))
                if parsed.scheme == "https" and urlsplit(destination).scheme != "https":
                    raise CollectionError("COLLECTION_TLS_DOWNGRADE")
                current = destination
                continue
            if response.status != 200:
                code = (
                    "COLLECTION_ACCESS_DENIED"
                    if response.status in (401, 403, 407, 412, 429, 451)
                    else "COLLECTION_HTTP_ERROR"
                )
                raise CollectionError(code)
            if response.getheader("Content-Encoding", "identity").strip().lower() != "identity":
                raise CollectionError("COLLECTION_RESPONSE_ENCODING")
            content_type = response.headers.get_content_type().lower()
            if content_type not in allowed_types:
                raise CollectionError("COLLECTION_CONTENT_TYPE")
            raw_length = response.getheader("Content-Length")
            if raw_length is not None:
                try:
                    length = int(raw_length)
                except ValueError:
                    raise CollectionError("COLLECTION_RESPONSE_INVALID") from None
                if length < 0 or length > MAX_BODY:
                    raise CollectionError("COLLECTION_BODY_LIMIT")
            data = bytearray()
            while True:
                # read1 performs at most one underlying read; a drip-fed stream
                # cannot keep an individual read() alive beyond the total budget.
                transport_socket.settimeout(remaining(stop))
                chunk = response.read1(min(16384, MAX_BODY + 1 - len(data)))
                remaining(stop)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_BODY:
                    raise CollectionError("COLLECTION_BODY_LIMIT")
            if raw_length is not None and len(data) != int(raw_length):
                raise CollectionError("COLLECTION_RESPONSE_INCOMPLETE")
            return FetchResult(
                current,
                bytes(data),
                content_type,
                response.headers.get_content_charset() or "utf-8",
            )
        except CollectionError:
            raise
        except TimeoutError:
            raise CollectionError("COLLECTION_TIMEOUT") from None
        except Exception:
            if time.monotonic() >= stop:
                raise CollectionError("COLLECTION_TIMEOUT") from None
            raise CollectionError("COLLECTION_RESPONSE_INVALID") from None
        finally:
            timer.cancel()
            if response is not None:
                response.close()
            connection.close()
    raise CollectionError("COLLECTION_REDIRECT_LIMIT")
