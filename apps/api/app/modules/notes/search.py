"""Literal search over latest saved revisions and their evidence/annotations."""

from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import ConfigDict, Field, field_validator
from sqlalchemy import and_, case, func, or_, select, text, tuple_

from app.core.list_filters import ContentListQuery
from app.modules.identity.access import ActorIdentity, SpaceAccess, recheck_identity
from app.modules.notes.models import EvidenceItem, Note, NoteAnnotation, NoteRevision
from app.modules.organization.models import NoteLabel
from app.modules.sources.listing import source_platform
from app.modules.sources.models import Source


class SearchQuery(ContentListQuery):
    model_config = ConfigDict(extra="forbid")

    q: str = Field(min_length=1, max_length=120)
    scope: Literal["all", "title", "body", "evidence", "annotation"] = "all"
    origin: Literal["ai_generated", "user_edit"] | None = None
    label_id: UUID | None = None

    @field_validator("q")
    @classmethod
    def required_query(cls, value: str) -> str:
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("SEARCH_QUERY_REQUIRED")
        return value


def search_page(access: SpaceAccess, params: SearchQuery) -> dict[str, Any]:
    session, tenant = access.actor.session, access.tenant.id
    session.execute(text("SET LOCAL statement_timeout='5s'"))

    def contains(column: Any) -> Any:
        # No regex, wildcards, tokenization or generated SQL from the query.
        return func.strpos(func.lower(column), func.lower(params.q)) > 0

    latest = (
        select(func.max(NoteRevision.version))
        .where(NoteRevision.tenant_id == tenant, NoteRevision.note_id == Note.id)
        .correlate(Note)
        .scalar_subquery()
    )
    evidence = (
        select(EvidenceItem.quote)
        .where(
            EvidenceItem.tenant_id == tenant,
            EvidenceItem.revision_id == NoteRevision.id,
            EvidenceItem.snapshot_id == NoteRevision.snapshot_id,
            contains(EvidenceItem.quote),
        )
        .order_by(EvidenceItem.evidence_key)
        .limit(1)
        .correlate(NoteRevision)
        .scalar_subquery()
    )
    annotation = (
        select(NoteAnnotation.body)
        .where(
            NoteAnnotation.tenant_id == tenant,
            NoteAnnotation.note_id == Note.id,
            NoteAnnotation.revision_id == NoteRevision.id,
            NoteAnnotation.snapshot_id == NoteRevision.snapshot_id,
            contains(NoteAnnotation.body),
        )
        .order_by(NoteAnnotation.created_at, NoteAnnotation.id)
        .limit(1)
        .correlate(Note, NoteRevision)
        .scalar_subquery()
    )
    candidates = {
        "title": NoteRevision.title,
        "body": NoteRevision.content_md,
        "evidence": evidence,
        "annotation": annotation,
    }
    selected = [
        (kind, value) for kind, value in candidates.items() if params.scope in ("all", kind)
    ]
    matched = case(*[(contains(value), kind) for kind, value in selected])
    content = case(*[(contains(value), value) for _kind, value in selected])
    statement = (
        select(
            Note.id,
            Note.created_at,
            NoteRevision.id.label("revision_id"),
            NoteRevision.version,
            NoteRevision.title,
            NoteRevision.origin,
            matched.label("matched_field"),
            func.substr(
                content,
                func.greatest(1, func.strpos(func.lower(content), func.lower(params.q)) - 60),
                240,
            ).label("snippet"),
        )
        .join(
            NoteRevision,
            and_(
                NoteRevision.tenant_id == tenant,
                NoteRevision.note_id == Note.id,
                NoteRevision.version == latest,
            ),
        )
        .join(Source, and_(Source.tenant_id == tenant, Source.id == Note.source_id))
        .where(Note.tenant_id == tenant, or_(*[contains(value) for _, value in selected]))
    )
    if params.platform is not None:
        statement = statement.where(source_platform() == params.platform)
    if params.origin is not None:
        statement = statement.where(NoteRevision.origin == params.origin)
    if params.label_id is not None:
        statement = statement.where(
            select(NoteLabel.id)
            .where(
                NoteLabel.tenant_id == tenant,
                NoteLabel.note_id == Note.id,
                NoteLabel.label_id == params.label_id,
                NoteLabel.active.is_(True),
            )
            .exists()
        )
    if params.cursor is not None:
        anchor = session.execute(statement.where(Note.id == params.cursor)).one_or_none()
        if anchor is None:
            raise HTTPException(404, detail={"code": "SEARCH_CURSOR_NOT_FOUND"})
        statement = statement.where(
            tuple_(Note.created_at, Note.id) < (anchor.created_at, anchor.id)
        )
    rows = list(
        session.execute(
            statement.order_by(Note.created_at.desc(), Note.id.desc()).limit(params.limit + 1)
        )
    )
    recheck_identity(session, ActorIdentity(access.actor.user.id, access.actor.token_hash))
    page = rows[: params.limit]
    return {
        "data": [
            {
                "note_id": str(row.id),
                "revision_id": str(row.revision_id),
                "version": row.version,
                "title": row.title,
                "origin": row.origin,
                "matched_field": row.matched_field,
                "snippet": row.snippet,
            }
            for row in page
        ],
        "next_cursor": str(page[-1].id) if len(rows) > params.limit else None,
    }
