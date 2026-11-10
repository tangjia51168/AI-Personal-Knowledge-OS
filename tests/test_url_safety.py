import socket

import pytest
from app.integrations.url_safety import (
    assert_public_resolution,
    canonicalize_url,
    extract_urls,
    inspect_url,
)


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("http://127.0.0.1:8000/secret", "PRIVATE_IP"),
        ("https://[::1]/", "PRIVATE_IP"),
        ("http://localhost/admin", "PRIVATE_HOSTNAME"),
        ("file:///etc/passwd", "UNSUPPORTED_SCHEME"),
        ("https://user:password@example.com/", "URL_CREDENTIALS_OR_AUTHORITY"),
        ("https://example.com:99999/", "INVALID_PORT"),
        ("http://100.64.0.1/", "PRIVATE_IP"),
        ("http://2130706433/", "INVALID_HOST"),
        ("http://127.1/", "INVALID_HOST"),
        ("https://[malformed]/", "INVALID_URL"),
        ("http://ｌｏｃａｌｈｏｓｔ/", "PRIVATE_HOSTNAME"),
        ("http://１２７.０.０.１/", "PRIVATE_IP"),
    ],
)
def test_inspect_rejects_unsafe_urls(value, reason):
    result = inspect_url(value)
    assert result.accepted is False
    assert result.reason == reason


def test_canonicalize_keeps_original_semantics_and_drops_fragment():
    assert canonicalize_url("HTTPS://Example.COM:443/a?b=2#private") == "https://example.com/a?b=2"
    assert canonicalize_url("www.Example.com") == "https://www.example.com/"
    assert canonicalize_url("https://[2606:4700:4700::1111]/") == (
        "https://[2606:4700:4700::1111]/"
    )


def test_canonical_expansion_is_still_bounded_and_variant_input_is_preserved():
    assert inspect_url("www.example.com/" + "a" * (2048 - 16)).accepted is False
    items = extract_urls(
        "https://Example.com/a#one https://example.com/a#two，", preserve_variants=True
    )
    assert len(items) == 2
    assert items[0].original_url.endswith("#one")
    assert items[0].canonical_url == items[1].canonical_url


def test_extraction_keeps_ipv6_and_balanced_url_parentheses():
    items = extract_urls(
        "https://[2606:4700:4700::1111] (https://example.com/a(b))。", preserve_variants=True
    )
    assert all(item.accepted for item in items)
    assert [item.canonical_url for item in items] == [
        "https://[2606:4700:4700::1111]/",
        "https://example.com/a(b)",
    ]


def test_extract_urls_is_bounded_and_deduplicates():
    result = extract_urls(
        "See https://Example.com/a, https://example.com/a. and http://localhost/x"
    )
    assert [item.canonical_url for item in result] == ["https://example.com/a", None]
    assert result[-1].reason == "PRIVATE_HOSTNAME"


def test_dns_answers_are_checked_before_connection(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))],
    )
    with pytest.raises(ValueError, match="PRIVATE_DNS_RESULT"):
        assert_public_resolution("public.example", 443)


def test_dns_public_answers_are_returned(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        ],
    )
    assert assert_public_resolution("example.com", 443) == ["93.184.216.34"]
