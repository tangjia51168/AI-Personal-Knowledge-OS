"""Atomic, idempotent append-only revisions authored by authorized members."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.identity.access import ActorIdentity, authorized_transaction
from app.modules.notes.content import Evidence, _provided_evidence, build_evidence
from app.modules.notes.edit_content import (
    EditedDocument,
    NoteCreateInput,
    NoteEditInput,
    edited_summary,
    render_edited_markdown,
    request_digest,
)
from app.modules.notes.models import EvidenceItem, Note, NoteRevision, NoteSection
from app.modules.notes.service import snapshot_scope
from app.modules.sources.models import SourceSnapshot


def snapshot_evidence(session: Session, tenant_id: UUID, snapshot_id: UUID) -> dict[str, Any]:
    snapshot = session.scalar(
        select(SourceSnapshot).where(
            SourceSnapshot.tenant_id == tenant_id, SourceSnapshot.id == snapshot_id
        )
    )
    if snapshot is None:
        raise HTTPException(404, detail={"code": "SNAPSHOT_NOT_FOUND"})
    try:
        evidence = build_evidence(snapshot.body_text)
        provided = _provided_evidence(evidence)
    except ValueError:
        raise HTTPException(422, detail={"code": "NOTE_SOURCE_INVALID"}) from None
    return {
        "snapshot_id": str(snapshot_id),
        "evidence": [item.model_dump() for item in provided],
        "total_segments": len(evidence),
        "partial": len(provided) < len(evidence),
    }


def edit_result(row: NoteRevision) -> dict[str, Any]:
    return {
        "note_id": str(row.note_id),
        "revision_id": str(row.id),
        "version": row.version,
    }


def check_edit_quota(session: Session, tenant_id: UUID) -> None:
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if (
        session.scalar(
            select(func.count())
            .select_from(NoteRevision)
            .where(
                NoteRevision.tenant_id == tenant_id,
                NoteRevision.origin == "user_edit",
                NoteRevision.created_at >= today,
            )
        )
        or 0
    ) >= 20:
        raise HTTPException(429, detail={"code": "NOTE_EDIT_DAILY_QUOTA"})
    if (
        session.scalar(
            select(func.count())
            .select_from(NoteRevision)
            .where(
                NoteRevision.tenant_id == tenant_id,
            )
        )
        or 0
    ) >= 1000:
        raise HTTPException(429, detail={"code": "NOTE_QUOTA_EXCEEDED"})


def save_first_note(
    identity: ActorIdentity, tenant_id: UUID, snapshot_id: UUID, body: NoteCreateInput
) -> dict[str, Any]:
    fingerprint = request_digest(snapshot_id, body)
    with authorized_transaction(identity, tenant_id) as (session, _role):
        existing = session.scalar(
            select(NoteRevision).where(
                NoteRevision.tenant_id == tenant_id,
                NoteRevision.edit_key == body.idempotency_key,
            )
        )
        if existing is not None:
            if (
                existing.created_by != identity.user_id
                or existing.snapshot_id != snapshot_id
                or existing.base_revision_id is not None
                or existing.request_sha256 != fingerprint
            ):
                raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
            return edit_result(existing)
        snapshot = session.scalar(
            select(SourceSnapshot).where(
                SourceSnapshot.tenant_id == tenant_id,
                SourceSnapshot.id == snapshot_id,
            )
        )
        if snapshot is None:
            raise HTTPException(404, detail={"code": "SNAPSHOT_NOT_FOUND"})
        if (
            session.scalar(
                select(Note.id).where(
                    Note.tenant_id == tenant_id,
                    Note.source_id == snapshot.source_id,
                )
            )
            is not None
        ):
            raise HTTPException(409, detail={"code": "NOTE_ALREADY_EXISTS"})
        check_edit_quota(session, tenant_id)
        try:
            evidence = build_evidence(snapshot.body_text)
            # Reuse the same document/citation validator; only the base version
            # and inherited limitations differ for an explicitly authored first note.
            summary = edited_summary(
                body,
                {
                    "limitations": [
                        "用户人工建立的笔记；未调用模型，观点与证据的语义关系未经核验。",
                        *snapshot.limitations,
                    ][:8]
                },
                evidence,
            )
            summary = snapshot_scope(summary, snapshot.content_kind, snapshot.provenance)
            document = EditedDocument(**summary.model_dump(), personal_notes=body.personal_notes)
            markdown = render_edited_markdown(document, evidence)
        except ValueError:
            raise HTTPException(422, detail={"code": "NOTE_EDIT_EVIDENCE_INVALID"}) from None
        if len(markdown.encode("utf-8")) > 131_072:
            raise HTTPException(422, detail={"code": "NOTE_EDIT_LIMIT"})
        note = Note(tenant_id=tenant_id, source_id=snapshot.source_id, created_by=identity.user_id)
        session.add(note)
        session.flush()
        revision = NoteRevision(
            tenant_id=tenant_id,
            note_id=note.id,
            snapshot_id=snapshot.id,
            run_id=None,
            origin="user_edit",
            base_revision_id=None,
            edit_key=body.idempotency_key,
            request_sha256=fingerprint,
            created_by=identity.user_id,
            version=1,
            title=document.title,
            content_md=markdown,
            document=document.model_dump(),
        )
        session.add(revision)
        session.flush()
        append_sections(session, tenant_id, revision, document, evidence)
        record(session, tenant_id, identity.user_id, AuditAction.NOTE_EDITED, revision.id)
        session.flush()
        return edit_result(revision)


def append_sections(
    session: Session,
    tenant_id: UUID,
    revision: NoteRevision,
    document: EditedDocument,
    evidence: list[Evidence],
) -> None:
    for section in [
        {"kind": "overview", "items": [document.overview.model_dump()]},
        *[section.model_dump() for section in document.sections],
    ]:
        session.add(NoteSection(tenant_id=tenant_id, revision_id=revision.id, **section))
    cited = {
        key
        for item in [
            document.overview,
            *(item for section in document.sections for item in section.items),
        ]
        for key in item.evidence_keys
    }
    for item in evidence:
        if item.key in cited:
            session.add(
                EvidenceItem(
                    tenant_id=tenant_id,
                    revision_id=revision.id,
                    snapshot_id=revision.snapshot_id,
                    evidence_key=item.key,
                    quote=item.text,
                )
            )


def save_edit(
    identity: ActorIdentity, tenant_id: UUID, note_id: UUID, body: NoteEditInput
) -> dict[str, Any]:
    fingerprint = request_digest(note_id, body)
    # authorized_transaction serializes space writers and rechecks active
    # membership after acquiring its locks, including revocation races.
    with authorized_transaction(identity, tenant_id) as (session, _role):
        existing = session.scalar(
            select(NoteRevision).where(
                NoteRevision.tenant_id == tenant_id,
                NoteRevision.edit_key == body.idempotency_key,
            )
        )
        if existing is not None:
            if (
                existing.created_by != identity.user_id
                or existing.note_id != note_id
                or existing.request_sha256 != fingerprint
            ):
                raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
            return edit_result(existing)
        note = session.scalar(select(Note).where(Note.tenant_id == tenant_id, Note.id == note_id))
        if note is None:
            raise HTTPException(404, detail={"code": "NOTE_NOT_FOUND"})
        base = session.scalar(
            select(NoteRevision)
            .where(NoteRevision.tenant_id == tenant_id, NoteRevision.note_id == note_id)
            .order_by(NoteRevision.version.desc())
            .limit(1)
        )
        if base is None or base.id != body.base_revision_id:
            raise HTTPException(409, detail={"code": "NOTE_REVISION_CONFLICT"})
        check_edit_quota(session, tenant_id)
        snapshot = session.scalar(
            select(SourceSnapshot).where(
                SourceSnapshot.tenant_id == tenant_id,
                SourceSnapshot.id == base.snapshot_id,
                SourceSnapshot.source_id == note.source_id,
            )
        )
        if snapshot is None:
            raise HTTPException(404, detail={"code": "SNAPSHOT_NOT_FOUND"})
        evidence = build_evidence(snapshot.body_text)
        try:
            summary = edited_summary(body, base.document, evidence)
            summary = snapshot_scope(summary, snapshot.content_kind, snapshot.provenance)
            document = EditedDocument(**summary.model_dump(), personal_notes=body.personal_notes)
            markdown = render_edited_markdown(document, evidence)
        except ValueError as error:
            code = (
                "NOTE_EDIT_EVIDENCE_INVALID"
                if str(error) == "SUMMARY_EVIDENCE_INVALID"
                else "NOTE_EDIT_INVALID"
            )
            raise HTTPException(422, detail={"code": code}) from None
        if len(markdown.encode("utf-8")) > 131_072:
            raise HTTPException(422, detail={"code": "NOTE_EDIT_LIMIT"})
        revision = NoteRevision(
            tenant_id=tenant_id,
            note_id=note_id,
            snapshot_id=snapshot.id,
            run_id=None,
            origin="user_edit",
            base_revision_id=base.id,
            edit_key=body.idempotency_key,
            request_sha256=fingerprint,
            created_by=identity.user_id,
            version=base.version + 1,
            title=document.title,
            content_md=markdown,
            document=document.model_dump(),
        )
        session.add(revision)
        session.flush()
        append_sections(session, tenant_id, revision, document, evidence)
        record(session, tenant_id, identity.user_id, AuditAction.NOTE_EDITED, revision.id)
        session.flush()
        return edit_result(revision)
