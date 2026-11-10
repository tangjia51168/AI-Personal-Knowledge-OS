from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.identity.access import ActorIdentity, authorized_transaction, recheck_identity
from app.modules.learning.content import LearningUpdate, learning_data
from app.modules.learning.models import NoteLearning
from app.modules.learning.reminders import snapshot
from app.modules.notes.models import Note


def page(
    identity: ActorIdentity, tenant_id: UUID, *, due_only: bool = False, note_id: UUID | None = None
) -> list[dict[str, Any]]:
    with authorized_transaction(identity, tenant_id) as (session, _role):
        query = select(NoteLearning).where(
            NoteLearning.tenant_id == tenant_id, NoteLearning.user_id == identity.user_id
        )
        if note_id is not None:
            if (
                session.scalar(
                    select(Note.id).where(Note.tenant_id == tenant_id, Note.id == note_id)
                )
                is None
            ):
                raise HTTPException(404, detail={"code": "NOTE_NOT_FOUND"})
            query = query.where(NoteLearning.note_id == note_id)
        if due_only:
            query = query.where(
                NoteLearning.reminder_at <= datetime.now(UTC), NoteLearning.status != "deep_read"
            )
        rows = list(
            session.scalars(
                query.order_by(
                    NoteLearning.reminder_at.asc().nulls_last(), NoteLearning.updated_at.desc()
                ).limit(100)
            )
        )
        return [learning_data(row) for row in rows]


def update(
    identity: ActorIdentity,
    tenant_id: UUID,
    note_id: UUID,
    body: LearningUpdate,
    *,
    expected: str | None = None,
) -> dict[str, Any]:
    with authorized_transaction(identity, tenant_id) as (session, _role):
        recheck_identity(session, identity)
        if (
            session.scalar(select(Note.id).where(Note.tenant_id == tenant_id, Note.id == note_id))
            is None
        ):
            raise HTTPException(404, detail={"code": "NOTE_NOT_FOUND"})
        row = session.scalar(
            select(NoteLearning).where(
                NoteLearning.tenant_id == tenant_id,
                NoteLearning.note_id == note_id,
                NoteLearning.user_id == identity.user_id,
            )
        )
        if expected is not None and snapshot(row) != expected:
            raise HTTPException(409, detail={"code": "REVIEW_CONFLICT"})
        now = datetime.now(UTC)
        if row is None:
            row = NoteLearning(
                id=uuid4(),
                tenant_id=tenant_id,
                note_id=note_id,
                user_id=identity.user_id,
                status=body.status,
                reminder_at=body.reminder_at,
                completed_at=now if body.status != "unseen" else None,
            )
            session.add(row)
        else:
            row.status = body.status
            row.reminder_at = body.reminder_at
            row.completed_at = now if body.status != "unseen" else None
            row.updated_at = now
        session.flush()
        record(session, tenant_id, identity.user_id, AuditAction.LEARNING_UPDATED, row.id)
        return learning_data(row)
