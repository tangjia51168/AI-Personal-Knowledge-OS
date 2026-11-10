"""Tenant-authorized snapshot summaries and versioned note reads."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.modules.identity.access import ActorIdentity, SpaceAccess, member_access, require_admin
from app.modules.identity.rate_limit import rate_limit
from app.modules.notes.annotation_content import AnnotationInput
from app.modules.notes.annotations import annotation_page, create_annotation
from app.modules.notes.edit_content import NoteCreateInput, NoteEditInput
from app.modules.notes.edits import save_edit, save_first_note, snapshot_evidence
from app.modules.notes.exports import ExportFormat, export_batch_data, export_data, render_export
from app.modules.notes.graph import local_graph
from app.modules.notes.links import links_for, set_link
from app.modules.notes.listing import NoteListQuery, list_page
from app.modules.notes.models import Note, NoteRevision, NoteRun
from app.modules.notes.obsidian import render_obsidian_batch
from app.modules.notes.research import research_page
from app.modules.notes.research_content import ResearchInput
from app.modules.notes.research_service import generate as generate_research
from app.modules.notes.research_service import read_history
from app.modules.notes.search import SearchQuery, search_page
from app.modules.notes.semantic import IndexInput, SemanticQuery
from app.modules.notes.semantic import index_revision as index_semantic_revision
from app.modules.notes.semantic import search as semantic_search
from app.modules.notes.service import resume_summary, run_result, summarize
from app.modules.notes.space_graph import space_graph

router = APIRouter(prefix="/api/v1/spaces/{tenant_id}", tags=["knowledge-notes"])
Access = Annotated[SpaceAccess, Depends(member_access)]


@router.post("/knowledge/semantic/index")
async def semantic_index(body: IndexInput, request: Request, access: Access) -> dict[str, Any]:
    require_admin(access)
    if request.query_params:
        raise HTTPException(422, detail={"code": "INVALID_SEMANTIC_QUERY"})
    rate_limit(request, "semantic_index", str(access.actor.user.id))
    identity = ActorIdentity(access.actor.user.id, access.actor.token_hash)
    tenant = access.tenant.id
    access.actor.session.rollback()
    return {"data": await index_semantic_revision(identity, tenant, body)}


@router.get("/knowledge/semantic/search")
async def semantic_knowledge(
    request: Request, access: Access, params: Annotated[SemanticQuery, Query()]
) -> dict[str, Any]:
    rate_limit(request, "semantic_search", str(access.actor.user.id))
    identity = ActorIdentity(access.actor.user.id, access.actor.token_hash)
    tenant = access.tenant.id
    access.actor.session.rollback()
    return {"data": await semantic_search(identity, tenant, params)}


@router.get("/knowledge/search")
def search_knowledge(access: Access, params: Annotated[SearchQuery, Query()]) -> dict[str, Any]:
    return search_page(access, params)


@router.get("/knowledge/research")
def research_knowledge(access: Access, params: Annotated[SearchQuery, Query()]) -> dict[str, Any]:
    """Return evidence candidates without silently invoking a paid provider."""

    return research_page(access, params)


@router.post("/knowledge/answers")
async def answer_knowledge(body: ResearchInput, request: Request, access: Access) -> dict[str, Any]:
    require_admin(access)
    if request.query_params:
        raise HTTPException(422, detail={"code": "INVALID_RESEARCH_QUERY"})
    rate_limit(request, "note_summary", str(access.actor.user.id))
    identity, tenant = (
        ActorIdentity(access.actor.user.id, access.actor.token_hash),
        access.tenant.id,
    )
    access.actor.session.rollback()
    return {"data": await generate_research(identity, tenant, body)}


@router.get("/knowledge/answers/{run_id}")
def read_research(run_id: UUID, request: Request, access: Access) -> dict[str, Any]:
    if request.query_params:
        raise HTTPException(422, detail={"code": "INVALID_RESEARCH_QUERY"})
    identity, tenant = (
        ActorIdentity(access.actor.user.id, access.actor.token_hash),
        access.tenant.id,
    )
    access.actor.session.rollback()
    return {"data": read_history(identity, tenant, run_id)}


@router.get("/knowledge/answers")
def research_history(request: Request, access: Access) -> dict[str, Any]:
    if request.query_params:
        raise HTTPException(422, detail={"code": "INVALID_RESEARCH_QUERY"})
    identity, tenant = (
        ActorIdentity(access.actor.user.id, access.actor.token_hash),
        access.tenant.id,
    )
    access.actor.session.rollback()
    return {"data": read_history(identity, tenant)}


class SpaceGraphQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.get("/knowledge/graph")
def get_space_graph(access: Access, params: Annotated[SpaceGraphQuery, Query()]) -> dict[str, Any]:
    return {"data": space_graph(access)}


def link_writer(access: SpaceAccess, request: Request) -> tuple[ActorIdentity, UUID]:
    rate_limit(request, "note_link", str(access.actor.user.id))
    identity = ActorIdentity(access.actor.user.id, access.actor.token_hash)
    tenant_id = access.tenant.id
    access.actor.session.rollback()
    return identity, tenant_id


@router.get("/notes/{note_id}/links")
def list_note_links(note_id: UUID, access: Access) -> dict[str, Any]:
    return {"data": links_for(access.actor.session, access.tenant.id, note_id)}


@router.get("/notes/{note_id}/graph")
def get_local_graph(
    note_id: UUID, access: Access, depth: int = Query(default=1, ge=1, le=2)
) -> dict[str, Any]:
    return {"data": local_graph(access.actor.session, access.tenant.id, note_id, depth)}


@router.put("/notes/{note_id}/links/{target_id}")
def link_note(note_id: UUID, target_id: UUID, request: Request, access: Access) -> dict[str, Any]:
    identity, tenant_id = link_writer(access, request)
    return {"data": set_link(identity, tenant_id, note_id, target_id, active=True)}


@router.delete("/notes/{note_id}/links/{target_id}")
def unlink_note(note_id: UUID, target_id: UUID, request: Request, access: Access) -> dict[str, Any]:
    identity, tenant_id = link_writer(access, request)
    return {"data": set_link(identity, tenant_id, note_id, target_id, active=False)}


@router.get("/notes/{note_id}/revisions/{revision_id}/annotations")
def list_annotations(
    note_id: UUID,
    revision_id: UUID,
    access: Access,
    cursor: UUID | None = None,
    limit: int = Query(default=20, ge=1, le=50),
) -> dict[str, Any]:
    return annotation_page(
        access.actor.session, access.tenant.id, note_id, revision_id, cursor, limit
    )


@router.post("/notes/{note_id}/revisions/{revision_id}/annotations")
def annotate_revision(
    note_id: UUID, revision_id: UUID, body: AnnotationInput, request: Request, access: Access
) -> dict[str, Any]:
    rate_limit(request, "note_annotation", str(access.actor.user.id))
    identity = ActorIdentity(access.actor.user.id, access.actor.token_hash)
    tenant = access.tenant.id
    access.actor.session.rollback()
    return {"data": create_annotation(identity, tenant, note_id, revision_id, body)}


class SummaryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: UUID
    consent: bool


class BatchExportItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note_id: UUID
    revision_id: UUID


class BatchExportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[BatchExportItem] = Field(min_length=1, max_length=20)


@router.post("/snapshots/{snapshot_id}/summarize")
async def generate_note(
    snapshot_id: UUID, body: SummaryInput, request: Request, access: Access
) -> dict[str, Any]:
    require_admin(access)
    if not body.consent:
        raise HTTPException(400, detail={"code": "SUMMARY_CONSENT_REQUIRED"})
    rate_limit(request, "note_summary", str(access.actor.user.id))
    identity, tenant_id = (
        ActorIdentity(access.actor.user.id, access.actor.token_hash),
        access.tenant.id,
    )
    access.actor.session.rollback()
    return {"data": await summarize(identity, tenant_id, snapshot_id, body.idempotency_key)}


@router.get("/note-runs/{run_id}")
def get_note_run(run_id: UUID, access: Access) -> dict[str, Any]:
    run = access.actor.session.scalar(
        select(NoteRun).where(
            NoteRun.tenant_id == access.tenant.id,
            NoteRun.id == run_id,
            NoteRun.created_by == access.actor.user.id,
        )
    )
    if run is None:
        raise HTTPException(404, detail={"code": "SUMMARY_RUN_NOT_FOUND"})
    return {"data": run_result(access.actor.session, run)}


@router.get("/snapshots/{snapshot_id}/note-runs")
def snapshot_runs(snapshot_id: UUID, request: Request, access: Access) -> dict[str, Any]:
    if request.query_params:
        raise HTTPException(422, detail={"code": "INVALID_SUMMARY_QUERY"})
    runs = access.actor.session.scalars(
        select(NoteRun)
        .where(
            NoteRun.tenant_id == access.tenant.id,
            NoteRun.snapshot_id == snapshot_id,
            NoteRun.created_by == access.actor.user.id,
        )
        .order_by(NoteRun.created_at.desc(), NoteRun.id.desc())
        .limit(20)
    ).all()
    return {"data": [run_result(access.actor.session, run) for run in runs]}


class ResumeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: bool


@router.post("/note-runs/{run_id}/resume")
async def resume_note_run(
    run_id: UUID, body: ResumeInput, request: Request, access: Access
) -> dict[str, Any]:
    require_admin(access)
    if request.query_params:
        raise HTTPException(422, detail={"code": "INVALID_SUMMARY_QUERY"})
    if not body.consent:
        raise HTTPException(400, detail={"code": "SUMMARY_CONSENT_REQUIRED"})
    rate_limit(request, "note_summary", str(access.actor.user.id))
    identity, tenant = (
        ActorIdentity(access.actor.user.id, access.actor.token_hash),
        access.tenant.id,
    )
    access.actor.session.rollback()
    return {"data": await resume_summary(identity, tenant, run_id)}


@router.post("/snapshots/{snapshot_id}/notes")
def create_first_note(
    snapshot_id: UUID, body: NoteCreateInput, request: Request, access: Access
) -> dict[str, Any]:
    if request.query_params:
        raise HTTPException(422, detail={"code": "INVALID_NOTE_CREATE_QUERY"})
    rate_limit(request, "note_edit", str(access.actor.user.id))
    identity, tenant_id = (
        ActorIdentity(access.actor.user.id, access.actor.token_hash),
        access.tenant.id,
    )
    access.actor.session.rollback()
    return {"data": save_first_note(identity, tenant_id, snapshot_id, body)}


@router.get("/snapshots/{snapshot_id}/note-evidence")
def first_note_evidence(snapshot_id: UUID, request: Request, access: Access) -> dict[str, Any]:
    if request.query_params:
        raise HTTPException(422, detail={"code": "INVALID_NOTE_CREATE_QUERY"})
    return {"data": snapshot_evidence(access.actor.session, access.tenant.id, snapshot_id)}


def revision_data(row: NoteRevision) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "snapshot_id": str(row.snapshot_id),
        "origin": row.origin,
        "base_revision_id": str(row.base_revision_id) if row.base_revision_id else None,
        "created_by": str(row.created_by),
        "version": row.version,
        "title": row.title,
        "content_md": row.content_md,
        "document": row.document,
        "created_at": row.created_at.isoformat(),
    }


def own_note(note_id: UUID, access: SpaceAccess) -> Note:
    note = access.actor.session.scalar(
        select(Note).where(Note.tenant_id == access.tenant.id, Note.id == note_id)
    )
    if note is None:
        raise HTTPException(404, detail={"code": "NOTE_NOT_FOUND"})
    return note


@router.post("/notes/{note_id}/revisions")
def create_revision(
    note_id: UUID, body: NoteEditInput, request: Request, access: Access
) -> dict[str, Any]:
    rate_limit(request, "note_edit", str(access.actor.user.id))
    identity, tenant_id = (
        ActorIdentity(access.actor.user.id, access.actor.token_hash),
        access.tenant.id,
    )
    access.actor.session.rollback()
    return {"data": save_edit(identity, tenant_id, note_id, body)}


@router.get("/notes")
def list_notes(access: Access, params: Annotated[NoteListQuery, Query()]) -> dict[str, Any]:
    return list_page(access, params)


@router.get("/notes/{note_id}")
def get_note(note_id: UUID, access: Access) -> dict[str, Any]:
    note = own_note(note_id, access)
    rows = list(
        access.actor.session.scalars(
            select(NoteRevision)
            .where(NoteRevision.tenant_id == access.tenant.id, NoteRevision.note_id == note.id)
            .order_by(NoteRevision.version.desc())
            .limit(20)
        )
    )
    return {
        "data": {
            "id": str(note.id),
            "source_id": str(note.source_id),
            "revision": revision_data(rows[0]) if rows else None,
            "revisions": [
                {
                    "id": str(row.id),
                    "version": row.version,
                    "title": row.title,
                    "origin": row.origin,
                    "base_revision_id": str(row.base_revision_id) if row.base_revision_id else None,
                    "created_by": str(row.created_by),
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ],
        }
    }


@router.get("/notes/{note_id}/revisions/{revision_id}")
def get_revision(note_id: UUID, revision_id: UUID, access: Access) -> dict[str, Any]:
    own_note(note_id, access)
    row = access.actor.session.scalar(
        select(NoteRevision).where(
            NoteRevision.tenant_id == access.tenant.id,
            NoteRevision.note_id == note_id,
            NoteRevision.id == revision_id,
        )
    )
    if row is None:
        raise HTTPException(404, detail={"code": "REVISION_NOT_FOUND"})
    return {"data": revision_data(row)}


@router.get("/notes/{note_id}/revisions/{revision_id}/export")
def export_revision(
    note_id: UUID, revision_id: UUID, access: Access, format: ExportFormat = "markdown"
) -> Response:
    content, media, suffix = render_export(export_data(access, note_id, revision_id), format)
    return Response(
        content,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="knowledge-{revision_id}.{suffix}"',
            "Content-Security-Policy": (
                "sandbox; default-src 'none'; base-uri 'none'; form-action 'none'"
            ),
        },
    )


@router.post("/notes/export-batch")
def export_batch(body: BatchExportInput, access: Access) -> Response:
    documents = export_batch_data(access, [(item.note_id, item.revision_id) for item in body.items])
    content = render_obsidian_batch(documents)
    return Response(
        content,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="knowledge-obsidian-batch.zip"',
            "Content-Security-Policy": (
                "sandbox; default-src 'none'; base-uri 'none'; form-action 'none'"
            ),
        },
    )
