"""Bounded user-supplied text and subtitle parsing; never accesses the network."""

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

ImportFormat = Literal["text", "srt", "vtt"]
ImportKind = Literal["article", "transcript"]
MAX_IMPORT_CHARACTERS = 60_000
MAX_IMPORT_BYTES = 65_536
MAX_SUBTITLE_CUES = 1_000
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SRT_TIME = re.compile(r"^(\d{2,3}):([0-5]\d):([0-5]\d),(\d{3})$")
_VTT_TIME = re.compile(r"^(?:(\d{2,3}):)?([0-5]\d):([0-5]\d)\.(\d{3})$")


@dataclass(frozen=True)
class ImportedContent:
    body_text: str
    raw_sha256: str
    utf8_bytes: int
    cue_count: int


def import_text_bytes(value: str) -> bytes:
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("IMPORT_TEXT_INVALID") from None
    if not value.strip() or _CONTROL.search(value):
        raise ValueError("IMPORT_TEXT_INVALID")
    if len(value) > MAX_IMPORT_CHARACTERS or len(raw) > MAX_IMPORT_BYTES:
        raise ValueError("IMPORT_TEXT_LIMIT")
    return raw


def import_title(value: str) -> str:
    if not value.strip() or _CONTROL.search(value) or "\n" in value or "\r" in value:
        raise ValueError("IMPORT_TITLE_INVALID")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("IMPORT_TITLE_INVALID") from None
    return value.strip()


def _timestamp(value: str, format: ImportFormat) -> tuple[int, str]:
    match = (_SRT_TIME if format == "srt" else _VTT_TIME).fullmatch(value)
    if match is None:
        raise ValueError("IMPORT_SUBTITLE_INVALID")
    hours, minutes, seconds, millis = match.groups()
    total = ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(millis)
    return total, f"{int(hours or 0):02d}:{minutes}:{seconds}.{millis}"


def parse_import_content(
    text: str, format: ImportFormat, content_kind: ImportKind
) -> ImportedContent:
    """Accept a closed subtitle subset without rendering markup or loading URLs.

    Time ranges remain in the evidence text. They are supplied by the user and
    are not checked against media. Unsupported cue settings/metadata are rejected
    rather than silently counted as content or silently omitted.
    """
    raw = import_text_bytes(text)
    if format not in ("text", "srt", "vtt") or content_kind not in ("article", "transcript"):
        raise ValueError("IMPORT_FORMAT_INVALID")
    normalized = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise ValueError("IMPORT_TEXT_INVALID")
    if format == "text":
        return ImportedContent(normalized, hashlib.sha256(raw).hexdigest(), len(raw), 0)
    if content_kind != "transcript":
        raise ValueError("IMPORT_SUBTITLE_KIND_REQUIRED")
    if format == "vtt":
        header, separator, normalized = normalized.partition("\n")
        if header != "WEBVTT" or not separator or not normalized.startswith("\n"):
            raise ValueError("IMPORT_SUBTITLE_INVALID")
        normalized = normalized.strip()
    blocks = re.split(r"\n[ \t]*\n", normalized)
    if not blocks or len(blocks) > MAX_SUBTITLE_CUES:
        raise ValueError("IMPORT_SUBTITLE_LIMIT")
    cues, previous_start = [], -1
    for block in blocks:
        lines = block.splitlines()
        if format == "srt":
            if len(lines) < 3 or not lines[0].isascii() or not lines[0].isdigit():
                raise ValueError("IMPORT_SUBTITLE_INVALID")
            lines = lines[1:]
        elif lines and "-->" not in lines[0]:
            if lines[0].split(" ", 1)[0] in ("NOTE", "STYLE", "REGION"):
                raise ValueError("IMPORT_SUBTITLE_UNSUPPORTED")
            lines = lines[1:]  # Optional WebVTT cue identifier.
        if len(lines) < 2:
            raise ValueError("IMPORT_SUBTITLE_INVALID")
        timing = lines[0].split(" --> ")
        if len(timing) != 2:
            raise ValueError("IMPORT_SUBTITLE_INVALID")
        start, start_label = _timestamp(timing[0], format)
        end, end_label = _timestamp(timing[1], format)
        body = "\n".join(lines[1:]).strip()
        if start >= end or start < previous_start or not body:
            raise ValueError("IMPORT_SUBTITLE_INVALID")
        previous_start = start
        cues.append(f"[{start_label} --> {end_label}]\n{body}")
    body_text = "\n\n".join(cues)
    if not body_text or len(body_text) > MAX_IMPORT_CHARACTERS:
        raise ValueError("IMPORT_TEXT_LIMIT")
    return ImportedContent(body_text, hashlib.sha256(raw).hexdigest(), len(raw), len(cues))
