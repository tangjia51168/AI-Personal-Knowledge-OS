"""Explicit batch selection, aware timestamps and stale-record token contracts."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.modules.learning.reminders import ReminderBatch, ReminderSelection, snapshot
from pydantic import ValidationError


def test_reminder_selection_is_unique_bounded_and_cannot_override_owner():
    note = uuid4()
    assert ReminderSelection(note_ids=[note]).note_ids == [note]
    for data in (
        {"note_ids": []},
        {"note_ids": [note, note]},
        {"note_ids": [uuid4() for _ in range(21)]},
        {"note_ids": [note], "user_id": uuid4()},
        {"note_ids": ["bad"]},
    ):
        with pytest.raises(ValidationError):
            ReminderSelection.model_validate(data)


def test_batch_requires_explicit_aware_time_or_null_and_valid_expected_records():
    item = {"note_id": uuid4(), "expected": snapshot(None)}
    assert ReminderBatch(items=[item], reminder_at=None).reminder_at is None
    assert ReminderBatch(
        items=[item], reminder_at="2026-11-08T08:00:00+08:00"
    ).reminder_at == datetime(2026, 11, 8, tzinfo=UTC)
    for data in (
        {"items": [item]},
        {"items": [item], "reminder_at": "2026-11-08T00:00:00"},
        {"items": [item, item], "reminder_at": None},
        {"items": [{**item, "expected": "bad"}], "reminder_at": None},
        {"items": [item], "reminder_at": None, "status": "deep_read"},
        {"items": [], "reminder_at": None},
    ):
        with pytest.raises(ValidationError):
            ReminderBatch.model_validate(data)
