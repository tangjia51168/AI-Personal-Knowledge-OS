"""Platform detection and explicit capabilities; recognition is not collection."""

import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from app.integrations.platforms.base import CollectedContent, CollectionError
from app.integrations.platforms.detection import platform_for_host
from app.integrations.platforms.transport import checked_url, fetch
from app.integrations.platforms.web import clean_text, collect_web, decode


def detect_platform(url: str) -> str:
    host = urlsplit(checked_url(url)).hostname or ""
    return platform_for_host(host)


def capabilities() -> list[dict[str, Any]]:
    # These are implemented capabilities. A successful request on a specific
    # deployment is recorded separately; availability is never inferred here.
    return [
        {
            "platform": name,
            "metadata": name in {"web", "bilibili", "youtube"},
            "article_text": name == "web",
            "transcript": False,
            "comments": False,
            "danmaku": False,
            "collections": False,
            "requires_platform_cookie": False,
            "verification": "per-request",
        }
        for name in ("web", "bilibili", "youtube", "douyin", "xiaohongshu", "zhihu")
    ]


def json_document(url: str) -> tuple[dict[str, Any], str, str]:
    result = fetch(url, allowed_types=frozenset({"application/json", "text/json"}))
    try:
        data = json.loads(decode(result))
    except (ValueError, TypeError):
        raise CollectionError("COLLECTION_METADATA_INVALID") from None
    if not isinstance(data, dict):
        raise CollectionError("COLLECTION_METADATA_INVALID")
    return data, result.url, hashlib.sha256(result.body).hexdigest()


def bilibili_id(url: str) -> tuple[str, str]:
    host = urlsplit(url).hostname or ""
    if host not in {"www.bilibili.com", "bilibili.com", "m.bilibili.com"}:
        raise CollectionError("COLLECTION_LINK_FORMAT_UNSUPPORTED")
    match = re.fullmatch(r"/video/(BV[A-Za-z0-9]{10}|av[1-9][0-9]{0,18})/?", urlsplit(url).path)
    if not match:
        raise CollectionError("COLLECTION_LINK_FORMAT_UNSUPPORTED")
    value = match.group(1)
    return ("bvid", value) if value.startswith("BV") else ("aid", value[2:])


def metadata_text(
    title: str, author: str | None, description: str = ""
) -> tuple[str, str | None, str]:
    """Keep real metadata usable even when a platform provides no description.

    Field labels declare scope; no video body or transcript is inferred. Clean
    before validating because a nonempty API string may contain only controls.
    """
    title = clean_text(title, 300)
    author = clean_text(author, 200) if author else None
    description = clean_text(description, 4000)
    if not title:
        raise CollectionError("COLLECTION_METADATA_INVALID")
    parts = ["标题：" + title]
    if author:
        parts.append("作者：" + author)
    if description:
        parts.append("简介：" + description)
    return title, author, "\n".join(parts)


def collect_bilibili(url: str) -> CollectedContent:
    key, value = bilibili_id(url)
    data, fetched, raw_sha256 = json_document(
        "https://api.bilibili.com/x/web-interface/view?" + urlencode({key: value})
    )
    metadata = data.get("data")
    if data.get("code") != 0 or not isinstance(metadata, dict):
        raise CollectionError("COLLECTION_METADATA_UNAVAILABLE")
    if metadata.get("state", 0) != 0:
        raise CollectionError("COLLECTION_ACCESS_DENIED")
    title = metadata.get("title")
    description = metadata.get("desc", "")
    owner = metadata.get("owner")
    if not isinstance(title, str) or not title.strip() or not isinstance(description, str):
        raise CollectionError("COLLECTION_METADATA_INVALID")
    author = owner.get("name") if isinstance(owner, dict) else None
    title, author, body = metadata_text(
        title, author if isinstance(author, str) else None, description
    )
    attributes: dict[str, Any] = {
        "external_id": value,
        "description": clean_text(description, 4000),
    }
    for name in ("duration", "pubdate"):
        number = metadata.get(name)
        if type(number) is int and number >= 0:
            attributes[name] = number
    return CollectedContent(
        "bilibili",
        url,
        fetched,
        "metadata",
        title,
        author,
        body,
        attributes,
        (
            "METADATA_ONLY",
            "NO_TRANSCRIPT_COLLECTED",
            "NO_COMMENTS_COLLECTED",
            "NO_DANMAKU_COLLECTED",
        ),
        raw_sha256,
    )


def youtube_id(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if host == "youtu.be":
        value = parsed.path.removeprefix("/")
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "www.youtube-nocookie.com"}:
        if parsed.path == "/watch":
            values = parse_qs(parsed.query).get("v", [])
            value = values[0] if len(values) == 1 else ""
        else:
            match = re.fullmatch(r"/(?:shorts|embed|live)/([A-Za-z0-9_-]{11})/?", parsed.path)
            value = match.group(1) if match else ""
    else:
        value = ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        raise CollectionError("COLLECTION_LINK_FORMAT_UNSUPPORTED")
    return value


def collect_youtube(url: str) -> CollectedContent:
    video = youtube_id(url)
    data, fetched, raw_sha256 = json_document(
        "https://www.youtube.com/oembed?"
        + urlencode({"url": "https://www.youtube.com/watch?v=" + video, "format": "json"})
    )
    title, author = data.get("title"), data.get("author_name")
    if not isinstance(title, str) or not title.strip():
        raise CollectionError("COLLECTION_METADATA_INVALID")
    title, author, body = metadata_text(title, author if isinstance(author, str) else None)
    return CollectedContent(
        "youtube",
        url,
        fetched,
        "metadata",
        title,
        author,
        body,
        {"external_id": video},
        ("METADATA_ONLY", "NO_TRANSCRIPT_COLLECTED", "NO_COMMENTS_COLLECTED"),
        raw_sha256,
    )


def collect(canonical_url: str) -> CollectedContent:
    url = checked_url(canonical_url)
    platform = detect_platform(url)
    if platform == "bilibili":
        return collect_bilibili(url)
    if platform == "youtube":
        return collect_youtube(url)
    if platform != "web":
        raise CollectionError("COLLECTION_PLATFORM_NOT_IMPLEMENTED")
    result = collect_web(url)
    if detect_platform(result.fetched_url) != "web":
        raise CollectionError("COLLECTION_PLATFORM_REDIRECT_UNSUPPORTED")
    return result
