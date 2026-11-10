"""Calendar interoperability and injection/resource bounds; live RLS tests separate."""

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from app.modules.learning.calendar_export import escape_text, fold_line, render_calendar, utc_stamp
from app.modules.learning.schedule import LearningSchedule, ScheduleItem, ScheduleQuery
from fastapi import HTTPException
from pydantic import ValidationError


def sample(title="复习协议标题", *, truncated=False):
    now = datetime(2026, 11, 8, tzinfo=UTC)
    return LearningSchedule(
        as_of=now,
        until=now + timedelta(days=7),
        period="7",
        truncated=truncated,
        items=[
            ScheduleItem(
                note_id=uuid4(),
                title=title,
                status="skimmed",
                reminder_at=now + timedelta(days=1),
                updated_at=now,
            )
        ],
    )


@pytest.mark.parametrize("period", ["due", "7", "30", "90"])
def test_schedule_query_has_bounded_exact_periods(period):
    assert ScheduleQuery(period=period).period == period


@pytest.mark.parametrize(
    "values",
    [
        {"period": "0"},
        {"period": "365"},
        {"period": ""},
        {"user_id": str(uuid4())},
        {"tenant_id": str(uuid4())},
    ],
)
def test_schedule_rejects_unbounded_ranges_and_actor_overrides(values):
    with pytest.raises(ValidationError):
        ScheduleQuery.model_validate(values)


def test_calendar_folds_utf8_escapes_text_and_has_no_active_properties():
    data = sample("中😀文" * 50 + "\\,;\r\nBEGIN:VALARM\nATTACH:https://example.com/\x00\x1b")
    tenant, actor = uuid4(), uuid4()
    raw = render_calendar(data, tenant, actor)
    assert raw.endswith(b"END:VCALENDAR\r\n") and b"\n" not in raw.replace(b"\r\n", b"")
    assert all(len(line) <= 75 for line in raw.split(b"\r\n"))
    text = raw.decode().replace("\r\n ", "")
    assert "SUMMARY:" + escape_text("复习：" + data.items[0].title) in text
    assert "\r\nBEGIN:VALARM" not in text and "\r\nATTACH:" not in text
    assert "\r\nURL:" not in text and "\r\nORGANIZER:" not in text
    assert "\r\nDTSTART:20261109T000000Z\r\n" in text
    assert text.count("BEGIN:VEVENT") == 1
    uid = next(line for line in text.splitlines() if line.startswith("UID:"))
    assert uid in render_calendar(data, tenant, actor).decode().replace("\r\n ", "")
    assert uid not in render_calendar(data, tenant, uuid4()).decode().replace("\r\n ", "")
    assert str(actor) not in text and str(tenant) not in text
    assert fold_line("a" * 74 + "😀") == "a" * 74 + "\r\n 😀"


def test_calendar_truncation_and_byte_limits_fail_without_partial_output():
    with pytest.raises(HTTPException) as truncated:
        render_calendar(sample(truncated=True), uuid4(), uuid4())
    assert truncated.value.status_code == 409
    with pytest.raises(HTTPException) as large:
        render_calendar(sample("中文" * 50000), uuid4(), uuid4())
    assert large.value.status_code == 413
    empty = sample().model_copy(update={"items": []})
    assert b"BEGIN:VEVENT" not in render_calendar(empty, uuid4(), uuid4())


def test_calendar_requires_aware_times_and_uses_absolute_utc_including_early_years():
    assert utc_stamp(datetime(1, 1, 2, tzinfo=UTC)) == "00010102T000000Z"
    assert (
        utc_stamp(datetime(2026, 11, 8, 5, tzinfo=timezone(timedelta(hours=5))))
        == "20261108T000000Z"
    )
    with pytest.raises(ValueError, match="TIMEZONE_REQUIRED"):
        utc_stamp(datetime(2026, 11, 8))
