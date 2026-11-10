"""Latest-version matching before bounded keyset pagination; no per-note reads."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import ConfigDict
from sqlalchemy import and_, func, or_, select, tuple_

from app.core.list_filters import ContentListQuery, literal_pattern
from app.modules.identity.access import SpaceAccess
from app.modules.learning.models import NoteLearning
from app.modules.notes.models import Note, NoteRevision
from app.modules.organization.models import NoteLabel
from app.modules.sources.listing import source_platform
from app.modules.sources.models import Source


class NoteListQuery(ContentListQuery):
    model_config = ConfigDict(extra="forbid")

    origin: Literal["ai_generated", "user_edit"] | None = None
    label_id: UUID | None = None
    reading_status: Literal["unseen", "seen", "skimmed", "deep_read", "unrecorded"] | None = None
    review: Literal["due", "upcoming"] | None = None


def list_page(access: SpaceAccess, params: NoteListQuery) -> dict[str, Any]:
    as_of = datetime.now(UTC)
    reading_status = func.coalesce(NoteLearning.status, "unseen")
    latest_version = (
        select(func.max(NoteRevision.version))
        .where(NoteRevision.tenant_id == access.tenant.id, NoteRevision.note_id == Note.id)
        .correlate(Note)
        .scalar_subquery()
    )
    statement = (
        select(
            Note.id,
            Note.source_id,
            Note.created_at,
            NoteRevision.title,
            NoteRevision.version,
            NoteRevision.origin,
            reading_status.label("reading_status"),
            NoteLearning.id.label("learning_id"),
            NoteLearning.reminder_at,
        )
        .join(
            NoteRevision,
            and_(
                NoteRevision.tenant_id == Note.tenant_id,
                NoteRevision.note_id == Note.id,
                NoteRevision.version == latest_version,
            ),
        )
        .join(Source, and_(Source.tenant_id == Note.tenant_id, Source.id == Note.source_id))
        .outerjoin(
            NoteLearning,
            and_(
                NoteLearning.tenant_id == Note.tenant_id,
                NoteLearning.note_id == Note.id,
                NoteLearning.user_id == access.actor.user.id,
            ),
        )
        .where(Note.tenant_id == access.tenant.id)
    )
    if params.reading_status == "unrecorded":
        statement = statement.where(NoteLearning.id.is_(None))
    elif params.reading_status is not None:
        statement = statement.where(reading_status == params.reading_status)
    if params.review is not None:
        statement = statement.where(
            NoteLearning.status != "deep_read",
            NoteLearning.reminder_at <= as_of
            if params.review == "due"
            else NoteLearning.reminder_at > as_of,
        )
    if params.q:
        pattern = literal_pattern(params.q)
        statement = statement.where(
            or_(
                NoteRevision.title.ilike(pattern, escape="!"),
                NoteRevision.content_md.ilike(pattern, escape="!"),
            )
        )
    if params.platform is not None:
        statement = statement.where(source_platform() == params.platform)
    if params.origin is not None:
        statement = statement.where(NoteRevision.origin == params.origin)
    if params.label_id is not None:
        statement = statement.where(
            select(NoteLabel.id)
            .where(
                NoteLabel.tenant_id == access.tenant.id,
                NoteLabel.note_id == Note.id,
                NoteLabel.label_id == params.label_id,
                NoteLabel.active.is_(True),
            )
            .exists()
        )
    if params.cursor is not None:
        anchor = access.actor.session.execute(
            statement.where(Note.id == params.cursor)
        ).one_or_none()
        if anchor is None:
            raise HTTPException(404, detail={"code": "NOTE_CURSOR_NOT_FOUND"})
        statement = statement.where(
            tuple_(Note.created_at, Note.id) < (anchor.created_at, anchor.id)
        )
    rows = list(
        access.actor.session.execute(
            statement.order_by(Note.created_at.desc(), Note.id.desc()).limit(params.limit + 1)
        )
    )
    page = rows[: params.limit]
    return {
        "data": [
            {
                "id": str(row.id),
                "source_id": str(row.source_id),
                "title": row.title,
                "version": row.version,
                "origin": row.origin,
                "created_at": row.created_at.isoformat(),
                "reading_status": row.reading_status,
                "learning_recorded": row.learning_id is not None,
                "reminder_at": row.reminder_at.isoformat() if row.reminder_at else None,
            }
            for row in page
        ],
        "next_cursor": str(page[-1].id) if len(rows) > params.limit else None,
    }
