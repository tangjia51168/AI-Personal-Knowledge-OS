"""Member-authorized human topics and tags; no model or collector calls."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from app.modules.identity.access import ActorIdentity, SpaceAccess, member_access, recheck_identity
from app.modules.identity.rate_limit import rate_limit
from app.modules.organization import collections
from app.modules.organization.concept_content import ConceptInput
from app.modules.organization.concepts import create_concept, revision_concepts
from app.modules.organization.content import LabelInput, LabelUpdateInput
from app.modules.organization.service import (
    create_label,
    labels_for,
    managed_labels,
    set_note_label,
    update_label,
)

router = APIRouter(prefix="/api/v1/spaces/{tenant_id}", tags=["knowledge-organization"])
Access = Annotated[SpaceAccess, Depends(member_access)]


def collection_query(request: Request) -> None:
    if request.query_params:
        raise HTTPException(422, detail={"code": "INVALID_COLLECTION_QUERY"})


@router.get("/collections")
def get_collections(request: Request, access: Access) -> dict[str, Any]:
    collection_query(request)
    rows = collections.inventory(access.actor.session, access.tenant.id)
    recheck_identity(
        access.actor.session, ActorIdentity(access.actor.user.id, access.actor.token_hash)
    )
    return {"data": rows}


@router.post("/collections")
def add_collection(
    body: collections.CollectionInput, request: Request, access: Access
) -> dict[str, Any]:
    collection_query(request)
    identity, tenant = writer_identity(access, request)
    return {"data": collections.create(identity, tenant, body)}


@router.patch("/collections/{collection_id}")
def edit_collection(
    collection_id: UUID, body: collections.CollectionUpdate, request: Request, access: Access
) -> dict[str, Any]:
    collection_query(request)
    identity, tenant = writer_identity(access, request)
    return {"data": collections.update(identity, tenant, collection_id, body)}


@router.get("/collections/{collection_id}/items")
def get_collection_items(collection_id: UUID, request: Request, access: Access) -> dict[str, Any]:
    collection_query(request)
    result = collections.items(access.actor.session, access.tenant.id, collection_id)
    recheck_identity(
        access.actor.session, ActorIdentity(access.actor.user.id, access.actor.token_hash)
    )
    return {"data": result}


@router.put("/collections/{collection_id}/items/{source_id}")
def edit_collection_item(
    collection_id: UUID,
    source_id: UUID,
    body: collections.ItemInput,
    request: Request,
    access: Access,
) -> dict[str, Any]:
    collection_query(request)
    identity, tenant = writer_identity(access, request)
    return {"data": collections.set_item(identity, tenant, collection_id, source_id, body)}


def writer_identity(access: SpaceAccess, request: Request) -> tuple[ActorIdentity, UUID]:
    rate_limit(request, "organization_write", str(access.actor.user.id))
    identity = ActorIdentity(access.actor.user.id, access.actor.token_hash)
    tenant_id = access.tenant.id
    # The service owns the write transaction and repeats authentication/member
    # checks after the serialized tenant lock, as in human revision writes.
    access.actor.session.rollback()
    return identity, tenant_id


@router.get("/notes/{note_id}/revisions/{revision_id}/concepts")
def get_concepts(
    note_id: UUID, revision_id: UUID, request: Request, access: Access
) -> dict[str, Any]:
    if request.query_params:
        raise HTTPException(422, detail={"code": "INVALID_CONCEPT_QUERY"})
    rows = revision_concepts(access.actor.session, access.tenant.id, note_id, revision_id)
    recheck_identity(
        access.actor.session, ActorIdentity(access.actor.user.id, access.actor.token_hash)
    )
    return {"data": rows}


@router.post("/notes/{note_id}/revisions/{revision_id}/concepts")
def add_concept(
    note_id: UUID, revision_id: UUID, body: ConceptInput, request: Request, access: Access
) -> dict[str, Any]:
    if request.query_params:
        raise HTTPException(422, detail={"code": "INVALID_CONCEPT_QUERY"})
    identity, tenant_id = writer_identity(access, request)
    return {"data": create_concept(identity, tenant_id, note_id, revision_id, body)}


@router.get("/labels")
def list_labels(access: Access) -> dict[str, Any]:
    return {"data": labels_for(access.actor.session, access.tenant.id)}


@router.post("/labels")
def add_label(body: LabelInput, request: Request, access: Access) -> dict[str, Any]:
    identity, tenant_id = writer_identity(access, request)
    return {"data": create_label(identity, tenant_id, body)}


@router.get("/labels/manage")
def manage_labels(request: Request, access: Access) -> dict[str, Any]:
    if request.query_params:
        raise HTTPException(422, detail={"code": "INVALID_LABEL_QUERY"})
    return {"data": managed_labels(access.actor.session, access.tenant.id)}


@router.patch("/labels/{label_id:uuid}")
def edit_label(
    label_id: UUID, body: LabelUpdateInput, request: Request, access: Access
) -> dict[str, Any]:
    if request.query_params:
        raise HTTPException(422, detail={"code": "INVALID_LABEL_QUERY"})
    identity, tenant_id = writer_identity(access, request)
    return {"data": update_label(identity, tenant_id, label_id, body)}


@router.get("/notes/{note_id}/labels")
def note_labels(note_id: UUID, access: Access) -> dict[str, Any]:
    return {"data": labels_for(access.actor.session, access.tenant.id, note_id)}


@router.put("/notes/{note_id}/labels/{label_id}")
def attach_label(note_id: UUID, label_id: UUID, request: Request, access: Access) -> dict[str, Any]:
    identity, tenant_id = writer_identity(access, request)
    return {"data": set_note_label(identity, tenant_id, note_id, label_id, active=True)}


@router.delete("/notes/{note_id}/labels/{label_id}")
def remove_label(note_id: UUID, label_id: UUID, request: Request, access: Access) -> dict[str, Any]:
    identity, tenant_id = writer_identity(access, request)
    return {"data": set_note_label(identity, tenant_id, note_id, label_id, active=False)}
