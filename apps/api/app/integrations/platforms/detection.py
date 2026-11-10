"""Shared host vocabulary; recognizing a platform never accesses it."""

from typing import Literal

Platform = Literal["web", "bilibili", "youtube", "douyin", "xiaohongshu", "zhihu"]

PLATFORM_DOMAINS = (
    ("bilibili", ("bilibili.com", "b23.tv")),
    ("youtube", ("youtube.com", "youtu.be", "youtube-nocookie.com")),
    ("douyin", ("douyin.com", "iesdouyin.com")),
    ("xiaohongshu", ("xiaohongshu.com", "xhslink.com")),
    ("zhihu", ("zhihu.com",)),
)


def platform_for_host(host: str) -> str:
    for name, domains in PLATFORM_DOMAINS:
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return name
    return "web"
