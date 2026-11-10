"""RFC 5545 text escaping and UTF-8 line folding for inert calendar downloads."""

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException

from app.modules.learning.schedule import LearningSchedule

CALENDAR_LIMIT = 262_144


def escape_text(value: str) -> str:
    # Normalize line endings, remove controls, then escape iCalendar TEXT once.
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(char for char in value if char == "\n" or ord(char) >= 32)
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def fold_line(value: str) -> str:
    # The continuation space counts towards RFC 5545's 75-octet physical line.
    lines: list[str] = []
    current = ""
    size = 0
    for char in value:
        width = len(char.encode("utf-8"))
        if size + width > 75:
            lines.append(current)
            current, size = " ", 1
        current += char
        size += width
    lines.append(current)
    return "\r\n".join(lines)


def utc_stamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("CALENDAR_TIMEZONE_REQUIRED")
    # strftime %Y is not four digits for early years on every platform.
    stamp = value.astimezone(UTC)
    return (
        f"{stamp.year:04}{stamp.month:02}{stamp.day:02}T"
        f"{stamp.hour:02}{stamp.minute:02}{stamp.second:02}Z"
    )


def render_calendar(schedule: LearningSchedule, tenant_id: UUID, user_id: UUID) -> bytes:
    if schedule.truncated:
        raise HTTPException(409, detail={"code": "LEARNING_CALENDAR_TRUNCATED"})
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Knowledge OS//Personal Review//ZH",
        "CALSCALE:GREGORIAN",
    ]
    for item in schedule.items:
        uid = hashlib.sha256(f"{tenant_id}:{user_id}:{item.note_id}".encode()).hexdigest()
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}@knowledge-os.invalid",
                f"DTSTAMP:{utc_stamp(schedule.as_of)}",
                f"LAST-MODIFIED:{utc_stamp(item.updated_at)}",
                f"DTSTART:{utc_stamp(item.reminder_at)}",
                "SUMMARY:" + escape_text("复习：" + item.title),
                "DESCRIPTION:"
                + escape_text(
                    f"个人复习提醒；笔记 {item.note_id}。导出时的静态记录，不自动同步或发送通知。"
                ),
                "TRANSP:TRANSPARENT",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    result = ("\r\n".join(fold_line(line) for line in lines) + "\r\n").encode("utf-8")
    if len(result) > CALENDAR_LIMIT:
        raise HTTPException(413, detail={"code": "LEARNING_CALENDAR_LIMIT"})
    return result
