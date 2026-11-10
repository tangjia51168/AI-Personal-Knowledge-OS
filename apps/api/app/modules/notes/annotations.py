"""Member-authorized append-only paragraph annotations and bounded pagination."""

from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.identity.access import ActorIdentity, authorized_transaction, recheck_identity
from app.modules.notes.annotation_content import AnnotationInput, paragraph
from app.modules.notes.models import NoteAnnotation, NoteRevision

MAX_NOTE_ANNOTATIONS = 100
MAX_SPACE_ANNOTATIONS = 1000


def annotation_data(row: NoteAnnotation) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "revision_id": str(row.revision_id),
        "kind": row.kind,
        "item_index": row.item_index,
        "quote": row.quote,
        "body": row.body,
        "created_by": str(row.created_by),
        "created_at": row.created_at.isoformat(),
    }


def require_revision(session: Session, tenant: UUID, note: UUID, revision: UUID) -> NoteRevision:
    row = session.scalar(
        select(NoteRevision).where(
            NoteRevision.tenant_id == tenant,
            NoteRevision.note_id == note,
            NoteRevision.id == revision,
        )
    )
    if row is None:
        raise HTTPException(404, detail={"code": "REVISION_NOT_FOUND"})
    return row


def create_annotation(
    identity: ActorIdentity, tenant: UUID, note: UUID, revision: UUID, body: AnnotationInput
) -> dict[str, Any]:
    request_hash = body.digest(note, revision)
    with authorized_transaction(identity, tenant) as (session, _role):
        recheck_identity(session, identity)
        source = require_revision(session, tenant, note, revision)
        existing = session.scalar(
            select(NoteAnnotation).where(
                NoteAnnotation.tenant_id == tenant,
                NoteAnnotation.idempotency_key == body.idempotency_key,
            )
        )
        if existing is not None:
            if existing.created_by != identity.user_id or existing.request_sha256 != request_hash:
                raise HTTPException(409, detail={"code": "ANNOTATION_IDEMPOTENCY_CONFLICT"})
            return annotation_data(existing)
        try:
            quote = paragraph(source.document, body.kind, body.item_index)
        except ValueError:
            raise HTTPException(409, detail={"code": "ANNOTATION_ANCHOR_INVALID"}) from None
        if quote != body.expected_quote:
            raise HTTPException(409, detail={"code": "ANNOTATION_QUOTE_MISMATCH"})
        for note_scope, maximum in ((False, MAX_SPACE_ANNOTATIONS), (True, MAX_NOTE_ANNOTATIONS)):
            statement = (
                select(func.count())
                .select_from(NoteAnnotation)
                .where(NoteAnnotation.tenant_id == tenant)
            )
            if note_scope:
                statement = statement.where(NoteAnnotation.note_id == note)
            if (session.scalar(statement) or 0) >= maximum:
                raise HTTPException(429, detail={"code": "ANNOTATION_QUOTA_EXCEEDED"})
        row = NoteAnnotation(
            id=uuid4(),
            tenant_id=tenant,
            note_id=note,
            snapshot_id=source.snapshot_id,
            revision_id=revision,
            created_by=identity.user_id,
            idempotency_key=body.idempotency_key,
            request_sha256=request_hash,
            kind=body.kind,
            item_index=body.item_index,
            quote=quote,
            body=body.body,
        )
        session.add(row)
        session.flush()
        record(session, tenant, identity.user_id, AuditAction.NOTE_ANNOTATED, row.id)
        return annotation_data(row)


def annotation_page(
    session: Session, tenant: UUID, note: UUID, revision: UUID, cursor: UUID | None, limit: int
) -> dict[str, Any]:
    require_revision(session, tenant, note, revision)
    statement = select(NoteAnnotation).where(
        NoteAnnotation.tenant_id == tenant,
        NoteAnnotation.note_id == note,
        NoteAnnotation.revision_id == revision,
    )
    if cursor is not None:
        anchor = session.scalar(statement.where(NoteAnnotation.id == cursor))
        if anchor is None:
            raise HTTPException(404, detail={"code": "ANNOTATION_CURSOR_NOT_FOUND"})
        statement = statement.where(
            tuple_(NoteAnnotation.created_at, NoteAnnotation.id) < (anchor.created_at, anchor.id)
        )
    rows = list(
        session.scalars(
            statement.order_by(NoteAnnotation.created_at.desc(), NoteAnnotation.id.desc()).limit(
                limit + 1
            )
        )
    )
    page = rows[:limit]
    return {
        "data": [annotation_data(row) for row in page],
        "next_cursor": str(page[-1].id) if len(rows) > limit else None,
    }
