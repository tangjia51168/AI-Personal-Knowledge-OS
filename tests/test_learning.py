from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.modules.learning.content import LearningUpdate
from pydantic import ValidationError


def test_learning_input_accepts_four_statuses_and_timezone_reminders():
    value = datetime.now(UTC) + timedelta(days=1)
    for status in ("unseen", "seen", "skimmed", "deep_read"):
        body = LearningUpdate(status=status, reminder_at=value)
        assert body.status == status and body.reminder_at == value


def test_learning_input_rejects_unknown_status_and_extra_fields():
    with pytest.raises(ValidationError):
        LearningUpdate(status="watching")
    with pytest.raises(ValidationError):
        LearningUpdate(status="seen", unknown="x")
    with pytest.raises(ValidationError):
        LearningUpdate(status="seen", reminder_at=datetime(2026, 11, 8, 10, 0))


def test_learning_data_does_not_leak_content():
    from app.modules.learning.content import learning_data

    row = SimpleNamespace(
        id=uuid4(),
        note_id=uuid4(),
        user_id=uuid4(),
        status="deep_read",
        reminder_at=None,
        completed_at=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    data = learning_data(row)
    assert set(data) == {
        "id",
        "note_id",
        "user_id",
        "status",
        "reminder_at",
        "completed_at",
        "created_at",
        "updated_at",
    }
