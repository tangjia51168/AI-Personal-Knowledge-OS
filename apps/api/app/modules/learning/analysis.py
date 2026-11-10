"""Personal review analysis from the current member's learning records.

The completed counters describe the last ``completed_at`` value stored for a
note.  ``note_learning`` does not contain an event history, so this module
deliberately does not present a review count, streak, mastery rate, or trend.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select

from app.modules.identity.access import ActorIdentity, SpaceAccess, recheck_identity
from app.modules.learning.models import NoteLearning
from app.modules.notes.models import Note

Count = Annotated[int, Field(ge=0)]


class AnalysisStateCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unseen: Count
    seen: Count
    skimmed: Count
    deep_read: Count


class AnalysisReminderCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due: Count
    upcoming: Count


class LearningAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: datetime
    total_notes: Count
    recorded_notes: Count
    unrecorded_notes: Count
    states: AnalysisStateCounts
    completed_notes: Count
    completed_last_7_days: Count
    completed_last_30_days: Count
    reminders: AnalysisReminderCounts

    @model_validator(mode="after")
    def consistent_counts(self) -> "LearningAnalysis":
        if (
            self.total_notes != self.recorded_notes + self.unrecorded_notes
            or self.total_notes != sum(self.states.model_dump().values())
            or self.unrecorded_notes > self.states.unseen
            or self.completed_notes > self.recorded_notes
            or self.completed_last_7_days > self.completed_notes
            or self.completed_last_30_days > self.completed_notes
            or self.reminders.due + self.reminders.upcoming
            > self.recorded_notes - self.states.deep_read
            or self.as_of.tzinfo is None
            or self.as_of.utcoffset() is None
        ):
            raise ValueError("LEARNING_ANALYSIS_INCONSISTENT")
        return self


class AnalysisResponse(BaseModel):
    data: LearningAnalysis


class AnalysisQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")


def analyze_learning(access: SpaceAccess) -> LearningAnalysis:
    """Read one tenant/member-bound aggregate without writes or side effects."""

    session = access.actor.session
    recheck_identity(session, ActorIdentity(access.actor.user.id, access.actor.token_hash))
    as_of = datetime.now(UTC)
    last_7_days = as_of - timedelta(days=7)
    last_30_days = as_of - timedelta(days=30)
    status = func.coalesce(NoteLearning.status, "unseen")
    pending = NoteLearning.status != "deep_read"
    row = session.execute(
        select(
            func.count(Note.id).label("total"),
            func.count(NoteLearning.id).label("recorded"),
            *(
                func.count(Note.id).filter(status == state).label(state)
                for state in ("unseen", "seen", "skimmed", "deep_read")
            ),
            func.count(NoteLearning.id)
            .filter(NoteLearning.completed_at.is_not(None))
            .label("completed"),
            func.count(NoteLearning.id)
            .filter(NoteLearning.completed_at >= last_7_days)
            .label("completed_7"),
            func.count(NoteLearning.id)
            .filter(NoteLearning.completed_at >= last_30_days)
            .label("completed_30"),
            func.count(NoteLearning.id)
            .filter(pending, NoteLearning.reminder_at <= as_of)
            .label("due"),
            func.count(NoteLearning.id)
            .filter(pending, NoteLearning.reminder_at > as_of)
            .label("upcoming"),
        )
        .select_from(Note)
        .outerjoin(
            NoteLearning,
            (NoteLearning.tenant_id == Note.tenant_id)
            & (NoteLearning.note_id == Note.id)
            & (NoteLearning.user_id == access.actor.user.id),
        )
        .where(Note.tenant_id == access.tenant.id)
    ).one()
    return LearningAnalysis(
        as_of=as_of,
        total_notes=row.total,
        recorded_notes=row.recorded,
        unrecorded_notes=row.total - row.recorded,
        states=AnalysisStateCounts(
            unseen=row.unseen, seen=row.seen, skimmed=row.skimmed, deep_read=row.deep_read
        ),
        completed_notes=row.completed,
        completed_last_7_days=row.completed_7,
        completed_last_30_days=row.completed_30,
        reminders=AnalysisReminderCounts(due=row.due, upcoming=row.upcoming),
    )
