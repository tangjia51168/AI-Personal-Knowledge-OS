"""Review requires an explicit completion state, reminder decision and current snapshot."""

from datetime import UTC, datetime

import pytest
from app.modules.learning.content import ReviewUpdate
from pydantic import ValidationError


def test_review_requires_all_decisions_and_normalizes_time():
    data = {"status": "seen", "reminder_at": None, "expected": "a" * 64}
    assert ReviewUpdate(**data).reminder_at is None
    assert ReviewUpdate(
        **{**data, "reminder_at": "2026-11-08T08:00:00+08:00"}
    ).reminder_at == datetime(2026, 11, 8, tzinfo=UTC)
    for field in data:
        with pytest.raises(ValidationError):
            ReviewUpdate.model_validate({key: value for key, value in data.items() if key != field})


def test_review_rejects_unseen_extra_fields_bad_snapshot_and_naive_time():
    data = {"status": "seen", "reminder_at": None, "expected": "a" * 64}
    for change in (
        {"status": "unseen"},
        {"status": "bad"},
        {"expected": "x"},
        {"user_id": "x"},
        {"reminder_at": "2026-11-08T12:00:00"},
    ):
        with pytest.raises(ValidationError):
            ReviewUpdate.model_validate({**data, **change})
