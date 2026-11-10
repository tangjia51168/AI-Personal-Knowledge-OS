"""Bounded personal reminder agenda; no generated plans or notification delivery."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, func, select

from app.modules.identity.access import ActorIdentity, SpaceAccess, recheck_identity
from app.modules.learning.models import NoteLearning
from app.modules.notes.models import Note, NoteRevision

SCHEDULE_LIMIT = 100
Period = Literal["due", "7", "30", "90"]


class ScheduleQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: Period = "7"


class ScheduleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_id: UUID
    title: str
    status: Literal["unseen", "seen", "skimmed"]
    reminder_at: datetime
    updated_at: datetime


class LearningSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: datetime
    until: datetime
    period: Period
    limit: Literal[100] = 100
    truncated: bool
    items: list[ScheduleItem]


class ScheduleResponse(BaseModel):
    data: LearningSchedule


def read_schedule(access: SpaceAccess, query: ScheduleQuery) -> LearningSchedule:
    session = access.actor.session
    recheck_identity(session, ActorIdentity(access.actor.user.id, access.actor.token_hash))
    as_of = datetime.now(UTC)
    until = as_of if query.period == "due" else as_of + timedelta(days=int(query.period))
    latest = (
        select(func.max(NoteRevision.version))
        .where(NoteRevision.tenant_id == Note.tenant_id, NoteRevision.note_id == Note.id)
        .correlate(Note)
        .scalar_subquery()
    )
    statement = (
        select(
            Note.id.label("note_id"),
            NoteRevision.title,
            NoteLearning.status,
            NoteLearning.reminder_at,
            NoteLearning.updated_at,
        )
        .select_from(NoteLearning)
        .join(Note, and_(Note.tenant_id == NoteLearning.tenant_id, Note.id == NoteLearning.note_id))
        .join(
            NoteRevision,
            and_(
                NoteRevision.tenant_id == Note.tenant_id,
                NoteRevision.note_id == Note.id,
                NoteRevision.version == latest,
            ),
        )
        .where(
            NoteLearning.tenant_id == access.tenant.id,
            NoteLearning.user_id == access.actor.user.id,
            NoteLearning.status != "deep_read",
            NoteLearning.reminder_at <= until,
        )
    )
    if query.period != "due":
        statement = statement.where(NoteLearning.reminder_at > as_of)
    rows = list(
        session.execute(
            statement.order_by(NoteLearning.reminder_at.asc(), Note.id.asc()).limit(
                SCHEDULE_LIMIT + 1
            )
        )
    )
    return LearningSchedule(
        as_of=as_of,
        until=until,
        period=query.period,
        truncated=len(rows) > SCHEDULE_LIMIT,
        items=[ScheduleItem.model_validate(dict(row._mapping)) for row in rows[:SCHEDULE_LIMIT]],
    )
