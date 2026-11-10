from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.learning.models import NoteLearning

STATUSES = ("unseen", "seen", "skimmed", "deep_read")


class LearningUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str = Field(default="unseen")
    reminder_at: datetime | None = None

    @field_validator("reminder_at")
    @classmethod
    def aware_reminder(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("REMINDER_TIMEZONE_REQUIRED")
            return value.astimezone(UTC)
        return None

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: str) -> str:
        if value not in STATUSES:
            raise ValueError("LEARNING_STATUS_INVALID")
        return value


class ReviewUpdate(LearningUpdate):
    status: str = Field()
    reminder_at: datetime | None = Field()
    expected: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("status")
    @classmethod
    def reviewed_status(cls, value: str) -> str:
        if value == "unseen":
            raise ValueError("REVIEW_STATUS_REQUIRED")
        return value


def learning_data(row: NoteLearning) -> dict:
    return {
        "id": str(row.id),
        "note_id": str(row.note_id),
        "user_id": str(row.user_id),
        "status": row.status,
        "reminder_at": row.reminder_at.isoformat() if row.reminder_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }
