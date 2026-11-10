"""Scheduler controls and fixed boundaries; no network or inference."""

from uuid import uuid4

import pytest
from app.modules.sources.schedules import ScheduleInput, ScheduleUpdate
from pydantic import ValidationError


@pytest.mark.parametrize("interval", [360, 1440, 10080])
def test_schedule_interval_bounds(interval):
    assert (
        ScheduleInput(source_id=uuid4(), interval_minutes=interval, consent=True).interval_minutes
        == interval
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"interval_minutes": 359},
        {"interval_minutes": 10081},
        {"interval_minutes": True},
        {"interval_minutes": "1440"},
        {"consent": 1},
        {"url": "https://example.com"},
    ],
)
def test_schedule_rejects_coercion_and_arbitrary_endpoints(changes):
    with pytest.raises(ValidationError):
        ScheduleInput.model_validate(
            {"source_id": uuid4(), "interval_minutes": 1440, "consent": True, **changes}
        )


def test_schedule_requires_explicit_version_and_consent_fields():
    with pytest.raises(ValidationError):
        ScheduleUpdate(active=True, expected_version=1)
    with pytest.raises(ValidationError):
        ScheduleUpdate(active=False, consent=False, expected_version=0)
