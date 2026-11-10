"""Consumer contract for bounded, unauthenticated platform collection."""

from dataclasses import dataclass
from typing import Any, Literal, Protocol


class CollectionError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CollectedContent:
    platform: str
    canonical_url: str
    fetched_url: str
    content_kind: Literal["article", "metadata"]
    title: str
    author: str | None
    text: str
    metadata: dict[str, Any]
    limitations: tuple[str, ...]
    raw_sha256: str = ""

    @property
    def final_url(self) -> str:
        return self.fetched_url


class PlatformAdapter(Protocol):
    def collect(self, canonical_url: str) -> CollectedContent: ...
