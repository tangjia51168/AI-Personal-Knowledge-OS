"""Explicit per-note plan contracts reuse atomic reminder writes."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.modules.learning.reminders import ReminderPlan, snapshot
from pydantic import ValidationError


def test_plan_normalizes_each_aware_timestamp_and_preserves_selection_order():
    ids = [uuid4(), uuid4()]
    body = ReminderPlan(
        items=[
            {
                "note_id": ids[0],
                "expected": snapshot(None),
                "reminder_at": "2026-11-08T08:00:00+08:00",
            },
            {
                "note_id": ids[1],
                "expected": snapshot(None),
                "reminder_at": "2026-11-10T08:00:00+08:00",
            },
        ]
    )
    assert [item.note_id for item in body.items] == ids
    assert body.items[0].reminder_at == datetime(2026, 11, 8, tzinfo=UTC)
    assert body.items[1].reminder_at - body.items[0].reminder_at == timedelta(days=2)


def test_plan_rejects_missing_naive_duplicate_oversized_or_forged_records():
    item = {"note_id": uuid4(), "expected": snapshot(None), "reminder_at": datetime.now(UTC)}
    for body in (
        {"items": []},
        {"items": [item, item]},
        {"items": [{**item, "note_id": uuid4()} for _ in range(21)]},
        {"items": [{**item, "reminder_at": None}]},
        {"items": [{**item, "reminder_at": "2026-11-08T12:00:00"}]},
        {"items": [{"note_id": item["note_id"], "expected": item["expected"]}]},
        {"items": [{**item, "expected": "bad"}]},
        {"items": [{**item, "status": "deep_read"}]},
        {"items": [item], "user_id": uuid4()},
        {
            "items": [
                item,
                {
                    **item,
                    "note_id": uuid4(),
                    "reminder_at": item["reminder_at"] + timedelta(days=92),
                },
            ]
        },
    ):
        with pytest.raises(ValidationError):
            ReminderPlan.model_validate(body)
