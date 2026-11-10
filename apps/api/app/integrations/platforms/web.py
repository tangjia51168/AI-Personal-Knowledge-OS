"""Plain-text HTML extraction, without scripts, images or secondary requests."""

import codecs
import hashlib
import re
from html.parser import HTMLParser

from app.integrations.platforms.base import CollectedContent, CollectionError
from app.integrations.platforms.transport import FetchResult, fetch

MAX_TEXT = 60000
_SKIP = {"script", "style", "nav", "footer", "form", "noscript", "template", "svg", "canvas"}
_BREAK = {
    "p",
    "div",
    "main",
    "article",
    "section",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "ul",
    "ol",
    "br",
    "blockquote",
    "pre",
    "tr",
    "table",
    "hr",
}


def clean_text(value: str, limit: int) -> str:
    # Preserve paragraph boundaries for later evidence offsets. Drop C0/C1
    # control characters that should not reach notes or model prompts.
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", value)
    lines = [re.sub(r"\s+", " ", line).strip() for line in value.splitlines()]
    result = "\n".join(line for line in lines if line)
    return result[:limit]


class ArticleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.parts: list[str] = []
        self.article_parts: list[str] = []
        self.skip: list[str] = []
        self.in_title = False
        self.in_head = False
        self.article_depth = 0
        self.author: str | None = None
        self.description: str | None = None
        self.meta_title: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value for key, value in attrs if value is not None}
        if tag == "meta" and not self.skip:
            name = (values.get("name") or values.get("property") or "").lower()
            content = values.get("content") or ""
            if name in {"author", "article:author"}:
                self.author = clean_text(content, 200)
            elif name in {"description", "og:description"}:
                self.description = clean_text(content, 1000)
            elif name == "og:title":
                self.meta_title = clean_text(content, 300)
        if tag in _SKIP:
            self.skip.append(tag)
        if self.skip:
            return
        if tag == "title":
            self.in_title = True
        if tag == "head":
            self.in_head = True
        if tag == "article":
            self.article_depth += 1
        if tag in _BREAK:
            self.parts.append("\n")
            if self.article_depth:
                self.article_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.skip:
            if tag == self.skip[-1]:
                self.skip.pop()
            return
        if tag == "title":
            self.in_title = False
        if tag == "head":
            self.in_head = False
        if tag in _BREAK:
            self.parts.append("\n")
            if self.article_depth:
                self.article_parts.append("\n")
        if tag == "article" and self.article_depth:
            self.article_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if self.in_title:
            self.title_parts.append(data)
        if self.in_head or self.in_title:
            return
        self.parts.append(data)
        if self.article_depth:
            self.article_parts.append(data)


def decode(result: FetchResult) -> str:
    try:
        codec = codecs.lookup(result.charset)
        if codec.name not in {"utf-8", "utf-8-sig", "ascii", "iso8859-1", "gb18030", "gbk", "big5"}:
            raise LookupError
        return result.body.decode(codec.name, errors="replace")
    except (LookupError, UnicodeError):
        raise CollectionError("COLLECTION_TEXT_ENCODING") from None


def parse_document(result: FetchResult, canonical_url: str) -> CollectedContent:
    document = decode(result)
    title, author, metadata = "", None, {"media_type": result.content_type}
    if result.content_type == "text/plain":
        content = clean_text(document, MAX_TEXT + 1)
    else:
        parser = ArticleParser()
        try:
            parser.feed(document)
            parser.close()
        except Exception:
            raise CollectionError("COLLECTION_HTML_INVALID") from None
        title = clean_text("".join(parser.title_parts), 300) or parser.meta_title or ""
        author = parser.author
        if parser.description:
            metadata["description"] = parser.description
        content = clean_text("".join(parser.article_parts or parser.parts), MAX_TEXT + 1)
    if not content.strip():
        raise CollectionError("COLLECTION_NO_TEXT")
    if len(content) > MAX_TEXT:
        raise CollectionError("COLLECTION_TEXT_LIMIT")
    return CollectedContent(
        "web",
        canonical_url,
        result.url,
        "article",
        title,
        author,
        content,
        metadata,
        ("NO_COMMENTS_COLLECTED", "NO_IMAGES_COLLECTED", "STATIC_TEXT_ONLY"),
        hashlib.sha256(result.body).hexdigest(),
    )


def collect_web(canonical_url: str) -> CollectedContent:
    result = fetch(canonical_url, allowed_types=frozenset({"text/html", "text/plain"}))
    return parse_document(result, canonical_url)
