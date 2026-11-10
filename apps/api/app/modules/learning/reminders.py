"""Explicit bounded reminder edits with atomic optimistic conflict detection."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import and_, func, select

from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.identity.access import ActorIdentity, authorized_transaction, recheck_identity
from app.modules.learning.content import LearningUpdate, learning_data
from app.modules.learning.models import NoteLearning
from app.modules.notes.models import Note, NoteRevision


class ReminderSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    note_ids: list[UUID] = Field(min_length=1, max_length=20)

    @field_validator("note_ids")
    @classmethod
    def unique_notes(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("REMINDER_DUPLICATE_NOTE")
        return value


class ReminderExpected(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    note_id: UUID
    expected: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReminderBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: list[ReminderExpected] = Field(min_length=1, max_length=20)
    reminder_at: Annotated[datetime | None, Field()]

    @field_validator("reminder_at")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        return LearningUpdate.aware_reminder(value)

    @field_validator("items")
    @classmethod
    def unique_items(cls, value: list[ReminderExpected]) -> list[ReminderExpected]:
        if len({item.note_id for item in value}) != len(value):
            raise ValueError("REMINDER_DUPLICATE_NOTE")
        return value


class PlannedReminder(ReminderExpected):
    reminder_at: datetime

    @field_validator("reminder_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        normalized = LearningUpdate.aware_reminder(value)
        assert normalized is not None
        return normalized


class ReminderPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: list[PlannedReminder] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def bounded_plan(self) -> "ReminderPlan":
        if len({item.note_id for item in self.items}) != len(self.items):
            raise ValueError("REMINDER_DUPLICATE_NOTE")
        times = [item.reminder_at for item in self.items]
        # Covers at most 90 local calendar days, including DST offset changes.
        if max(times) - min(times) > timedelta(days=91):
            raise ValueError("REMINDER_PLAN_SPAN_EXCEEDED")
        return self


def snapshot(row: NoteLearning | None) -> str:
    value = learning_data(row) if row else None
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def reminder_selection(identity: ActorIdentity, tenant_id: UUID, body: ReminderSelection) -> dict:
    with authorized_transaction(identity, tenant_id) as (session, _role):
        recheck_identity(session, identity)
        latest = (
            select(func.max(NoteRevision.version))
            .where(NoteRevision.tenant_id == Note.tenant_id, NoteRevision.note_id == Note.id)
            .correlate(Note)
            .scalar_subquery()
        )
        rows = session.execute(
            select(Note.id, NoteRevision.title, NoteLearning)
            .join(
                NoteRevision,
                and_(
                    NoteRevision.tenant_id == Note.tenant_id,
                    NoteRevision.note_id == Note.id,
                    NoteRevision.version == latest,
                ),
            )
            .outerjoin(
                NoteLearning,
                and_(
                    NoteLearning.tenant_id == Note.tenant_id,
                    NoteLearning.note_id == Note.id,
                    NoteLearning.user_id == identity.user_id,
                ),
            )
            .where(Note.tenant_id == tenant_id, Note.id.in_(body.note_ids))
            .limit(20)
        ).all()
        if len(rows) != len(body.note_ids):
            raise HTTPException(404, detail={"code": "REMINDER_NOTE_NOT_FOUND"})
        by_id = {row.id: row for row in rows}
        return {
            "items": [
                {
                    "note_id": str(note_id),
                    "title": by_id[note_id].title,
                    "status": by_id[note_id].NoteLearning.status
                    if by_id[note_id].NoteLearning
                    else "unseen",
                    "reminder_at": (
                        by_id[note_id].NoteLearning.reminder_at.isoformat()
                        if by_id[note_id].NoteLearning and by_id[note_id].NoteLearning.reminder_at
                        else None
                    ),
                    "expected": snapshot(by_id[note_id].NoteLearning),
                }
                for note_id in body.note_ids
            ]
        }


def apply_reminders(identity: ActorIdentity, tenant_id: UUID, body: ReminderBatch) -> dict:
    return _apply_reminders(
        identity, tenant_id, body.items, {item.note_id: body.reminder_at for item in body.items}
    )


def apply_plan(identity: ActorIdentity, tenant_id: UUID, body: ReminderPlan) -> dict:
    return _apply_reminders(
        identity,
        tenant_id,
        list(body.items),
        {item.note_id: item.reminder_at for item in body.items},
    )


def _apply_reminders(
    identity: ActorIdentity,
    tenant_id: UUID,
    items: list[ReminderExpected],
    targets: dict[UUID, datetime | None],
) -> dict:
    with authorized_transaction(identity, tenant_id) as (session, _role):
        recheck_identity(session, identity)
        ids = [item.note_id for item in items]
        notes = list(
            session.scalars(select(Note.id).where(Note.tenant_id == tenant_id, Note.id.in_(ids)))
        )
        if len(notes) != len(ids):
            raise HTTPException(404, detail={"code": "REMINDER_NOTE_NOT_FOUND"})
        rows = {
            row.note_id: row
            for row in session.scalars(
                select(NoteLearning).where(
                    NoteLearning.tenant_id == tenant_id,
                    NoteLearning.user_id == identity.user_id,
                    NoteLearning.note_id.in_(ids),
                )
            )
        }
        # Validate the whole selection before changing any row or writing audit events.
        if any(snapshot(rows.get(item.note_id)) != item.expected for item in items):
            raise HTTPException(409, detail={"code": "REMINDER_BATCH_CONFLICT"})
        changed = 0
        now = datetime.now(UTC)
        for item in items:
            reminder_at = targets[item.note_id]
            row = rows.get(item.note_id)
            if (row.reminder_at if row else None) == reminder_at:
                continue
            if row is None:
                row = NoteLearning(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    note_id=item.note_id,
                    user_id=identity.user_id,
                    status="unseen",
                    reminder_at=reminder_at,
                    completed_at=None,
                )
                session.add(row)
            else:
                row.reminder_at, row.updated_at = reminder_at, now
            session.flush()
            record(session, tenant_id, identity.user_id, AuditAction.LEARNING_UPDATED, row.id)
            changed += 1
        return {"selected": len(ids), "changed": changed}
