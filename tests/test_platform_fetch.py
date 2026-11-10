"""Collector boundaries and parsers; these do not claim live platform acceptance."""

import socket
import ssl
import threading
import time
from email.message import Message

import pytest
from app.integrations.platforms import (
    CollectionError,
    capabilities,
    collect,
    detect_platform,
    registry,
    transport,
)
from app.integrations.platforms.transport import FetchResult
from app.integrations.platforms.web import MAX_TEXT, parse_document


class FakeSocket:
    def __init__(self):
        self.timeouts = []
        self.address = None
        self.closed = False

    def settimeout(self, value):
        self.timeouts.append(value)

    def connect(self, address):
        self.address = address

    def getpeername(self):
        return self.address

    def close(self):
        self.closed = True

    def shutdown(self, value):
        pass


class FakeResponse:
    def __init__(self, *, status=200, headers=None, body=b"article"):
        self.status = status
        self.headers = Message()
        for key, value in (headers or {"Content-Type": "text/plain"}).items():
            self.headers[key] = value
        self.body = body
        self.closed = False

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read1(self, size):
        result, self.body = self.body[:size], self.body[size:]
        return result

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, response):
        self.sock = FakeSocket()
        self.response = response
        self.requests = []
        self.closed = False

    def request(self, *args, **kwargs):
        self.requests.append((args, kwargs))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def deadline():
    return time.monotonic() + 10


def test_resolution_rejects_mixed_public_and_private_answers(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(CollectionError, match="COLLECTION_DNS_NOT_PUBLIC"):
        transport.resolve_public("example.com", 443, deadline())


def test_stalled_dns_does_not_hold_worker_or_create_unbounded_threads(monkeypatch):
    release = threading.Event()
    resolved = threading.Event()

    def stalled(*args, **kwargs):
        release.wait(1)
        resolved.set()
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", stalled)
    started = time.monotonic()
    try:
        with pytest.raises(CollectionError, match="COLLECTION_TIMEOUT"):
            transport.resolve_public("example.com", 443, started + 0.02)
        assert time.monotonic() - started < 0.5
    finally:
        release.set()
        resolved.wait(1)


@pytest.mark.parametrize("address", ["64:ff9b::a00:1", "2002:a00:1::", "2001:0000::1"])
def test_resolution_rejects_ipv6_transition_addresses(monkeypatch, address):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 443, 0, 0))
        ],
    )
    with pytest.raises(CollectionError, match="COLLECTION_DNS_NOT_PUBLIC"):
        transport.resolve_public("example.com", 443, deadline())


def test_tcp_connects_only_checked_address_and_tls_verifies_original_name(monkeypatch):
    calls, sock = [], FakeSocket()
    monkeypatch.setattr(
        transport,
        "resolve_public",
        lambda *args: [(socket.AF_INET, socket.IPPROTO_TCP, ("93.184.216.34", 443))],
    )
    monkeypatch.setattr(socket, "socket", lambda *args: sock)

    class Context:
        def wrap_socket(self, value, *, server_hostname):
            calls.append(server_hostname)
            return value

    monkeypatch.setattr(ssl, "create_default_context", lambda: Context())
    connection = transport.open_pinned("https://example.com/article", deadline())
    assert sock.address == ("93.184.216.34", 443)
    assert calls == ["example.com"] and connection.host == "example.com"
    assert connection.auto_open == 0
    connection.close()


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (
            FakeResponse(headers={"Content-Type": "text/plain", "Content-Encoding": "gzip"}),
            "COLLECTION_RESPONSE_ENCODING",
        ),
        (FakeResponse(headers={"Content-Type": "image/png"}), "COLLECTION_CONTENT_TYPE"),
        (
            FakeResponse(headers={"Content-Type": "text/plain", "Content-Length": "1048577"}),
            "COLLECTION_BODY_LIMIT",
        ),
        (
            FakeResponse(headers={"Content-Type": "text/plain", "Content-Length": "9"}),
            "COLLECTION_RESPONSE_INCOMPLETE",
        ),
        (FakeResponse(status=403), "COLLECTION_ACCESS_DENIED"),
        (FakeResponse(body=b"x" * (transport.MAX_BODY + 1)), "COLLECTION_BODY_LIMIT"),
    ],
)
def test_transport_rejects_encoding_type_length_status_and_stream_overflow(
    monkeypatch, response, code
):
    connection = FakeConnection(response)
    monkeypatch.setattr(transport, "open_pinned", lambda *args: connection)
    with pytest.raises(CollectionError, match=code):
        transport.fetch("https://example.com/", allowed_types=frozenset({"text/plain"}))
    assert connection.closed and response.closed


def test_transport_rechecks_each_redirect_and_never_forwards_credentials(monkeypatch):
    responses = [
        FakeResponse(status=302, headers={"Location": "https://other.example/文章?a=汉"}),
        FakeResponse(),
    ]
    opened = []

    def connect(url, stop):
        connection = FakeConnection(responses.pop(0))
        opened.append((url, connection))
        return connection

    monkeypatch.setattr(transport, "open_pinned", connect)
    result = transport.fetch("https://example.com/", allowed_types=frozenset({"text/plain"}))
    assert result.url == "https://other.example/文章?a=汉"
    assert len(opened) == 2
    target = opened[1][1].requests[0][0][1]
    assert target == "/%E6%96%87%E7%AB%A0?a=%E6%B1%89"
    for _url, connection in opened:
        headers = connection.requests[0][1]["headers"]
        assert headers["Accept-Encoding"] == "identity"
        assert not ({"Cookie", "Authorization", "Referer"} & headers.keys())


@pytest.mark.parametrize(
    ("target", "code"),
    [
        ("http://127.0.0.1/secret", "COLLECTION_UNSAFE_URL"),
        ("https://example.com:8443/", "COLLECTION_PORT_NOT_ALLOWED"),
        ("http://example.com/", "COLLECTION_TLS_DOWNGRADE"),
        ("https://user:secret@example.com/", "COLLECTION_UNSAFE_URL"),
    ],
)
def test_redirect_destination_is_validated_before_connecting(monkeypatch, target, code):
    opened = []

    def connect(url, stop):
        opened.append(url)
        return FakeConnection(FakeResponse(status=302, headers={"Location": target}))

    monkeypatch.setattr(transport, "open_pinned", connect)
    with pytest.raises(CollectionError, match=code):
        transport.fetch("https://example.com/", allowed_types=frozenset({"text/plain"}))
    assert opened == ["https://example.com/"]


def test_redirect_count_is_bounded(monkeypatch):
    opened = []

    def connect(url, stop):
        opened.append(url)
        return FakeConnection(FakeResponse(status=302, headers={"Location": "/again"}))

    monkeypatch.setattr(transport, "open_pinned", connect)
    with pytest.raises(CollectionError, match="COLLECTION_REDIRECT_LIMIT"):
        transport.fetch("https://example.com/", allowed_types=frozenset({"text/plain"}))
    assert len(opened) == 4


def test_platform_precondition_rejection_is_access_denied_without_retry(monkeypatch):
    opened = []

    def connect(url, stop):
        opened.append(url)
        return FakeConnection(FakeResponse(status=412))

    monkeypatch.setattr(transport, "open_pinned", connect)
    with pytest.raises(CollectionError, match="COLLECTION_ACCESS_DENIED"):
        transport.fetch("https://example.com/", allowed_types=frozenset({"application/json"}))
    assert opened == ["https://example.com/"]


def test_html_parser_uses_article_text_and_drops_executable_and_navigation_content():
    html = b"""<html><head><title>Useful &amp; safe</title>
    <meta name="author" content="Alice"></head>
    <body><nav>navigation secret</nav><p>outside article</p><article><h1>Method</h1>
    <p>Step <b>one</b>.</p><script>steal token</script><style>.hidden{}</style>
    <p>Step two.</p></article><footer>footer</footer></body></html>"""
    result = parse_document(
        FetchResult("https://example.com/final", html, "text/html", "utf-8"),
        "https://example.com/original",
    )
    assert result.title == "Useful & safe" and result.author == "Alice"
    assert result.text == "Method\nStep one.\nStep two."
    assert result.canonical_url.endswith("original") and result.fetched_url.endswith("final")
    assert result.content_kind == "article" and "NO_COMMENTS_COLLECTED" in result.limitations


def test_text_limit_rejects_instead_of_silently_truncating():
    with pytest.raises(CollectionError, match="COLLECTION_TEXT_LIMIT"):
        parse_document(
            FetchResult("https://example.com/", b"x" * (MAX_TEXT + 1), "text/plain", "utf-8"),
            "https://example.com/",
        )


def test_platform_detection_uses_domain_boundaries_and_capabilities_are_explicit():
    assert detect_platform("https://www.bilibili.com/video/BV1xx411c7mD") == "bilibili"
    assert detect_platform("https://bilibili.com.evil.example/video/BV1xx411c7mD") == "web"
    assert detect_platform("https://youtu.be/dQw4w9WgXcQ") == "youtube"
    supported = {entry["platform"]: entry for entry in capabilities()}
    assert supported["web"]["article_text"] is True
    assert supported["bilibili"]["article_text"] is False
    assert all(not entry["transcript"] and not entry["comments"] for entry in supported.values())
    with pytest.raises(CollectionError, match="COLLECTION_PLATFORM_NOT_IMPLEMENTED"):
        collect("https://www.zhihu.com/question/123")


def test_bilibili_collects_only_metadata_not_video_or_comments(monkeypatch):
    called = []

    def document(url):
        called.append(url)
        return (
            {
                "code": 0,
                "data": {
                    "title": "Title",
                    "desc": "Description",
                    "duration": 120,
                    "owner": {"name": "Author"},
                },
            },
            url,
            "a" * 64,
        )

    monkeypatch.setattr(registry, "json_document", document)
    result = collect("https://www.bilibili.com/video/BV1xx411c7mD")
    assert called == ["https://api.bilibili.com/x/web-interface/view?bvid=BV1xx411c7mD"]
    assert result.content_kind == "metadata"
    assert result.text == "标题：Title\n作者：Author\n简介：Description"
    assert result.raw_sha256 == "a" * 64
    assert result.metadata["duration"] == 120 and "NO_DANMAKU_COLLECTED" in result.limitations


def test_youtube_embedded_html_is_never_saved_or_executed(monkeypatch):
    monkeypatch.setattr(
        registry,
        "json_document",
        lambda url: (
            {"title": "Video", "author_name": "A", "html": "<script>bad</script>"},
            url,
            "b" * 64,
        ),
    )
    result = collect("https://youtu.be/dQw4w9WgXcQ")
    assert result.content_kind == "metadata" and result.text == "标题：Video\n作者：A"
    assert result.raw_sha256 == "b" * 64
    assert result.metadata == {"external_id": "dQw4w9WgXcQ"}
    assert "METADATA_ONLY" in result.limitations


def test_bilibili_without_description_still_preserves_real_title(monkeypatch):
    monkeypatch.setattr(
        registry,
        "json_document",
        lambda url: ({"code": 0, "data": {"title": "Real title"}}, url, "a" * 64),
    )
    result = collect("https://www.bilibili.com/video/BV1xx411c7mD")
    assert result.text == "标题：Real title"
    assert result.author is None and result.metadata["description"] == ""
    assert "NO_TRANSCRIPT_COLLECTED" in result.limitations


@pytest.mark.parametrize("title", ["\x00\x01", "\u0080\u0081"])
def test_metadata_title_must_remain_nonempty_after_sanitizing(monkeypatch, title):
    monkeypatch.setattr(registry, "json_document", lambda url: ({"title": title}, url, "b" * 64))
    with pytest.raises(CollectionError, match="COLLECTION_METADATA_INVALID"):
        collect("https://youtu.be/dQw4w9WgXcQ")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.bilibili.com/video/not-video",
        "https://b23.tv/abc",
        "https://youtube.com/watch?v=bad",
        "https://youtu.be/123",
    ],
)
def test_unimplemented_platform_link_shapes_fail_closed(url):
    with pytest.raises(CollectionError, match="COLLECTION_LINK_FORMAT_UNSUPPORTED"):
        collect(url)


def test_stdlib_connection_close_preserves_deadline_socket_until_final_close():
    # Actual socket/HTTP parser regression. It uses a local socket pair, never
    # opens a listener or bypasses URL policy in the production connector.
    client, server = socket.socketpair()
    server.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 7\r\nConnection: close\r\n\r\narticle")
    connection = transport.PinnedConnection("example.com")
    connection.sock, connection.auto_open = client, 0
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        client.settimeout(1)
        assert response.read1(20) == b"article"
        assert client.fileno() >= 0
        response.close()
    finally:
        connection.close()
        server.close()
    assert client.fileno() == -1


def test_total_deadline_interrupts_real_drip_fed_header_reads(monkeypatch):
    client, server = socket.socketpair()
    stopped = threading.Event()

    def drip():
        try:
            while not stopped.wait(0.01):
                server.sendall(b"H")
        except OSError:
            pass

    def connect(url, stop):
        connection = transport.PinnedConnection("example.com")
        connection.sock, connection.auto_open = client, 0
        return connection

    thread = threading.Thread(target=drip, daemon=True)
    thread.start()
    monkeypatch.setattr(transport, "open_pinned", connect)
    started = time.monotonic()
    try:
        with pytest.raises(CollectionError):
            transport.fetch(
                "https://example.com/",
                allowed_types=frozenset({"text/plain"}),
                deadline=started + 0.15,
            )
    finally:
        stopped.set()
        server.close()
        thread.join(timeout=1)
    assert time.monotonic() - started < 1
