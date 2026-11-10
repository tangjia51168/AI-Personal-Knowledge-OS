from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.integrations.url_safety import MAX_CANDIDATES, URLCandidate, extract_urls
from app.modules.identity.access import ActorIdentity, SpaceAccess, member_access
from app.modules.identity.rate_limit import rate_limit
from app.modules.sources import schedules
from app.modules.sources.collection import queue_collection, represent_snapshot, source_for
from app.modules.sources.imports import ContentImportInput, queue_import
from app.modules.sources.listing import SourceListQuery, list_page
from app.modules.sources.models import SourceSnapshot
from app.modules.sources.service import save_batch

router = APIRouter(prefix="/api/v1/spaces/{tenant_id}/sources", tags=["sources"])
Access = Annotated[SpaceAccess, Depends(member_access)]


class SourceInspectInput(BaseModel):
    text: str = Field(min_length=1, max_length=16 * 1024)

    @field_validator("text")
    @classmethod
    def bounded_utf8_text(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 16 * 1024:
            raise ValueError("SOURCE_TEXT_LIMIT")
        return value


class SourceImportInput(SourceInspectInput):
    idempotency_key: UUID


class CollectionInput(BaseModel):
    idempotency_key: UUID
    consent: bool


def schedule_identity(access: SpaceAccess) -> tuple[ActorIdentity, UUID]:
    identity, tenant = (
        ActorIdentity(access.actor.user.id, access.actor.token_hash),
        access.tenant.id,
    )
    access.actor.session.rollback()
    return identity, tenant


def schedule_query(request: Request) -> None:
    if request.query_params:
        raise HTTPException(422, detail={"code": "INVALID_SCHEDULE_QUERY"})


@router.get("/schedules")
def list_schedules(request: Request, access: Access) -> dict[str, Any]:
    schedule_query(request)
    return {"data": schedules.list_own(*schedule_identity(access))}


@router.post("/schedules", status_code=201)
def create_schedule(
    body: schedules.ScheduleInput, request: Request, access: Access
) -> dict[str, Any]:
    schedule_query(request)
    rate_limit(request, "source_schedule", str(access.actor.user.id))
    return {"data": schedules.create(*schedule_identity(access), body)}


@router.patch("/schedules/{schedule_id}")
def update_schedule(
    schedule_id: UUID, body: schedules.ScheduleUpdate, request: Request, access: Access
) -> dict[str, Any]:
    schedule_query(request)
    rate_limit(request, "source_schedule", str(access.actor.user.id))
    return {"data": schedules.update(*schedule_identity(access), schedule_id, body)}


def candidates_response(candidates: list[URLCandidate]) -> list[dict[str, Any]]:
    return [
        {
            "original_url": item.original_url,
            "canonical_url": item.canonical_url,
            "accepted": item.accepted,
            "reason": item.reason,
        }
        for item in candidates
    ]


@router.post("/inspect")
def inspect_sources(body: SourceInspectInput, request: Request, access: Access) -> dict[str, Any]:
    """Identify pasted links before persistence or network access."""

    # Inspection is intentionally bounded even though it performs no network I/O;
    # this prevents a public member from using the parser as an unbounded service.
    rate_limit(request, "source_inspect", str(access.actor.user.id))
    candidates = extract_urls(body.text, limit=MAX_CANDIDATES + 1, preserve_variants=True)
    return {
        "data": {
            "space_id": str(access.tenant.id),
            "candidates": candidates_response(candidates[:MAX_CANDIDATES]),
            "truncated": len(candidates) > MAX_CANDIDATES,
        }
    }


@router.get("")
def list_sources(access: Access, params: Annotated[SourceListQuery, Query()]) -> dict[str, Any]:
    return list_page(access, params)


@router.post("", status_code=201)
def import_sources(body: SourceImportInput, request: Request, access: Access) -> dict[str, Any]:
    """Persist bounded URL candidates; never fetches or calls a platform adapter."""

    rate_limit(request, "source_import", str(access.actor.user.id))
    return {"data": save_batch(access, body.text, body.idempotency_key)}


@router.get("/capabilities")
def collection_capabilities(access: Access) -> dict[str, Any]:
    from app.core.config import get_settings
    from app.integrations.platforms import capabilities

    return {"data": {"enabled": get_settings().collector_enabled, "platforms": capabilities()}}


@router.post("/{source_id}/collect", status_code=202)
def collect_source(
    source_id: UUID, body: CollectionInput, request: Request, access: Access
) -> dict[str, Any]:
    if not body.consent:
        raise HTTPException(400, detail={"code": "COLLECTION_CONSENT_REQUIRED"})
    rate_limit(request, "source_collect", str(access.actor.user.id))
    return {"data": queue_collection(access, source_id, body.idempotency_key)}


@router.get("/{source_id}/snapshots")
def source_snapshots(source_id: UUID, access: Access) -> dict[str, Any]:
    source_for(access.actor.session, access.tenant.id, source_id)
    rows = access.actor.session.scalars(
        select(SourceSnapshot)
        .where(SourceSnapshot.tenant_id == access.tenant.id, SourceSnapshot.source_id == source_id)
        .order_by(SourceSnapshot.version.desc())
        .limit(20)
    )
    return {"data": [represent_snapshot(row) for row in rows]}


@router.post("/{source_id}/imports", status_code=202)
def import_content(
    source_id: UUID, body: ContentImportInput, request: Request, access: Access
) -> dict[str, Any]:
    rate_limit(request, "source_content_import", str(access.actor.user.id))
    return {"data": queue_import(access, source_id, body)}


@router.get("/{source_id}/snapshots/{snapshot_id}")
def get_snapshot(source_id: UUID, snapshot_id: UUID, access: Access) -> dict[str, Any]:
    source_for(access.actor.session, access.tenant.id, source_id)
    row = access.actor.session.scalar(
        select(SourceSnapshot).where(
            SourceSnapshot.tenant_id == access.tenant.id,
            SourceSnapshot.source_id == source_id,
            SourceSnapshot.id == snapshot_id,
        )
    )
    if row is None:
        raise HTTPException(404, detail={"code": "SNAPSHOT_NOT_FOUND"})
    return {"data": represent_snapshot(row, include_body=True)}
