"""Reversible human-created, undirected note links with bounded reads."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.identity.access import ActorIdentity, authorized_transaction, recheck_identity
from app.modules.notes.models import Note, NoteLink, NoteRevision

MAX_NOTE_LINKS = 50
MAX_SPACE_LINKS = 1000


def canonical_pair(note_id: UUID, target_id: UUID) -> tuple[UUID, UUID]:
    if note_id == target_id:
        raise HTTPException(409, detail={"code": "NOTE_LINK_SELF"})
    return (note_id, target_id) if note_id.int < target_id.int else (target_id, note_id)


def require_notes(session: Session, tenant_id: UUID, *note_ids: UUID) -> None:
    found = set(
        session.scalars(select(Note.id).where(Note.tenant_id == tenant_id, Note.id.in_(note_ids)))
    )
    if found != set(note_ids):
        raise HTTPException(404, detail={"code": "NOTE_NOT_FOUND"})


def links_for(session: Session, tenant_id: UUID, note_id: UUID) -> list[dict[str, Any]]:
    require_notes(session, tenant_id, note_id)
    rows = list(
        session.scalars(
            select(NoteLink)
            .where(
                NoteLink.tenant_id == tenant_id,
                NoteLink.active.is_(True),
                or_(NoteLink.left_note_id == note_id, NoteLink.right_note_id == note_id),
            )
            .order_by(NoteLink.updated_at.desc(), NoteLink.id.desc())
            .limit(MAX_NOTE_LINKS + 1)
        )
    )
    if len(rows) > MAX_NOTE_LINKS:
        raise HTTPException(409, detail={"code": "NOTE_LINK_INVENTORY_LIMIT"})
    target_ids = {
        row.right_note_id if row.left_note_id == note_id else row.left_note_id for row in rows
    }
    latest_versions = (
        select(NoteRevision.note_id, func.max(NoteRevision.version).label("version"))
        .where(NoteRevision.tenant_id == tenant_id, NoteRevision.note_id.in_(target_ids))
        .group_by(NoteRevision.note_id)
        .subquery()
    )
    titles = {
        row.note_id: (row.title, row.version)
        for row in session.execute(
            select(NoteRevision.note_id, NoteRevision.title, NoteRevision.version)
            .join(
                latest_versions,
                (latest_versions.c.note_id == NoteRevision.note_id)
                & (latest_versions.c.version == NoteRevision.version),
            )
            .where(NoteRevision.tenant_id == tenant_id)
        )
    }
    return [
        {
            "id": str(row.id),
            "note_id": str(
                target := (row.right_note_id if row.left_note_id == note_id else row.left_note_id)
            ),
            "title": titles[target][0],
            "version": titles[target][1],
            "created_by": str(row.created_by),
            "updated_by": str(row.updated_by),
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
            "method": "manual",
            "confidence": None,
        }
        for row in rows
    ]


def set_link(
    identity: ActorIdentity,
    tenant_id: UUID,
    note_id: UUID,
    target_id: UUID,
    *,
    active: bool,
) -> list[dict[str, Any]]:
    left, right = canonical_pair(note_id, target_id)
    with authorized_transaction(identity, tenant_id) as (session, _role):
        recheck_identity(session, identity)
        require_notes(session, tenant_id, left, right)
        row = session.scalar(
            select(NoteLink).where(
                NoteLink.tenant_id == tenant_id,
                NoteLink.left_note_id == left,
                NoteLink.right_note_id == right,
            )
        )
        if (row is not None and row.active == active) or (row is None and not active):
            return links_for(session, tenant_id, note_id)
        if active:
            space_count = session.scalar(
                select(func.count())
                .select_from(NoteLink)
                .where(NoteLink.tenant_id == tenant_id, NoteLink.active.is_(True))
            )
            if (space_count or 0) >= MAX_SPACE_LINKS:
                raise HTTPException(429, detail={"code": "SPACE_LINK_QUOTA_EXCEEDED"})
            for endpoint in (left, right):
                count = session.scalar(
                    select(func.count())
                    .select_from(NoteLink)
                    .where(
                        NoteLink.tenant_id == tenant_id,
                        NoteLink.active.is_(True),
                        or_(
                            NoteLink.left_note_id == endpoint,
                            NoteLink.right_note_id == endpoint,
                        ),
                    )
                )
                if (count or 0) >= MAX_NOTE_LINKS:
                    raise HTTPException(429, detail={"code": "NOTE_LINK_QUOTA_EXCEEDED"})
        if row is None:
            row = NoteLink(
                id=uuid4(),
                tenant_id=tenant_id,
                left_note_id=left,
                right_note_id=right,
                created_by=identity.user_id,
                updated_by=identity.user_id,
                active=True,
            )
            session.add(row)
        else:
            row.active = active
            row.updated_by = identity.user_id
            row.updated_at = datetime.now(UTC)
        session.flush()
        record(
            session,
            tenant_id,
            identity.user_id,
            AuditAction.NOTE_LINKED if active else AuditAction.NOTE_UNLINKED,
            row.id,
        )
        return links_for(session, tenant_id, note_id)
