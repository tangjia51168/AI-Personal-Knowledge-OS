"""Personal reading-state counts, not an estimate of understanding or mastery."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select

from app.modules.identity.access import ActorIdentity, SpaceAccess, recheck_identity
from app.modules.learning.models import NoteLearning
from app.modules.notes.models import Note

Count = Annotated[int, Field(ge=0)]


class StateCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unseen: Count
    seen: Count
    skimmed: Count
    deep_read: Count


class ReminderCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due: Count
    upcoming: Count


class LearningSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: datetime
    total_notes: Count
    recorded_notes: Count
    unrecorded_notes: Count
    states: StateCounts
    reminders: ReminderCounts

    @model_validator(mode="after")
    def consistent_counts(self) -> "LearningSummary":
        if (
            self.total_notes != self.recorded_notes + self.unrecorded_notes
            or self.total_notes != sum(self.states.model_dump().values())
            or self.unrecorded_notes > self.states.unseen
            or self.reminders.due + self.reminders.upcoming
            > self.recorded_notes - self.states.deep_read
            or self.as_of.tzinfo is None
            or self.as_of.utcoffset() is None
        ):
            raise ValueError("LEARNING_SUMMARY_INCONSISTENT")
        return self


class SummaryResponse(BaseModel):
    data: LearningSummary


class SummaryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")


def summarize_learning(access: SpaceAccess) -> LearningSummary:
    """One aggregate statement: no pagination cap, per-note queries or writes.

    Note identities are the denominator, not revisions. The outer join must keep
    the actor predicate in ON so missing personal records remain in the result.
    Existing RLS remains active in addition to both explicit tenant predicates.
    """
    session = access.actor.session
    recheck_identity(session, ActorIdentity(access.actor.user.id, access.actor.token_hash))
    as_of = datetime.now(UTC)
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
    return LearningSummary(
        as_of=as_of,
        total_notes=row.total,
        recorded_notes=row.recorded,
        unrecorded_notes=row.total - row.recorded,
        states=StateCounts(
            unseen=row.unseen, seen=row.seen, skimmed=row.skimmed, deep_read=row.deep_read
        ),
        reminders=ReminderCounts(due=row.due, upcoming=row.upcoming),
    )
