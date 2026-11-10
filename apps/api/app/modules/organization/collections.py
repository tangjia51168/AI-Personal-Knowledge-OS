"""Bounded collection transactions, serialized by the existing tenant writer lock."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.identity.access import ActorIdentity, authorized_transaction
from app.modules.organization.collection_models import Collection, CollectionItem
from app.modules.organization.content import normalize_name
from app.modules.sources.models import Source

MAX_COLLECTIONS = 100
MAX_ITEMS = 200


class CollectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["folder", "collection"]
    name: str = Field(min_length=1, max_length=1024)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return normalize_name(value)


class CollectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1, le=2_147_483_646, strict=True)
    name: str = Field(min_length=1, max_length=1024)
    active: bool = Field(strict=True)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return normalize_name(value)


class ItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1, le=2_147_483_646, strict=True)
    active: bool = Field(strict=True)
    position: int = Field(ge=0, lt=MAX_ITEMS, strict=True)


def fail(code: str, status: int = 409) -> None:
    raise HTTPException(status, detail={"code": code})


def data(row: Collection) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "name": row.name,
        "active": row.active,
        "version": row.version,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def require(session: Session, tenant: UUID, collection: UUID) -> Collection:
    row = session.scalar(
        select(Collection).where(Collection.tenant_id == tenant, Collection.id == collection)
    )
    if row is None:
        raise HTTPException(404, detail={"code": "COLLECTION_NOT_FOUND"})
    return row


def inventory(session: Session, tenant: UUID) -> list[dict[str, Any]]:
    rows = list(
        session.scalars(
            select(Collection)
            .where(Collection.tenant_id == tenant)
            .order_by(Collection.kind, Collection.name_key, Collection.id)
            .limit(MAX_COLLECTIONS + 1)
        )
    )
    if len(rows) > MAX_COLLECTIONS:
        fail("COLLECTION_INVENTORY_LIMIT")
    return [data(row) for row in rows]


def create(identity: ActorIdentity, tenant: UUID, body: CollectionInput) -> dict[str, Any]:
    with authorized_transaction(identity, tenant) as (session, _role):
        existing = session.scalar(
            select(Collection).where(
                Collection.tenant_id == tenant,
                Collection.kind == body.kind,
                Collection.name_key == body.name.casefold(),
            )
        )
        if existing:
            fail("COLLECTION_NAME_CONFLICT")
        total = (
            session.scalar(
                select(func.count()).select_from(Collection).where(Collection.tenant_id == tenant)
            )
            or 0
        )
        if total >= MAX_COLLECTIONS:
            fail("COLLECTION_QUOTA", 429)
        row = Collection(
            tenant_id=tenant,
            kind=body.kind,
            name=body.name,
            name_key=body.name.casefold(),
            created_by=identity.user_id,
            updated_by=identity.user_id,
        )
        session.add(row)
        session.flush()
        record(session, tenant, identity.user_id, AuditAction.COLLECTION_CREATED, row.id)
        return data(row)


def update(
    identity: ActorIdentity, tenant: UUID, collection: UUID, body: CollectionUpdate
) -> dict[str, Any]:
    with authorized_transaction(identity, tenant) as (session, _role):
        row = require(session, tenant, collection)
        if row.version != body.expected_version:
            fail("COLLECTION_VERSION_CONFLICT")
        if row.name == body.name and row.active == body.active:
            return data(row)
        duplicate = session.scalar(
            select(Collection.id).where(
                Collection.tenant_id == tenant,
                Collection.kind == row.kind,
                Collection.name_key == body.name.casefold(),
                Collection.id != row.id,
            )
        )
        if duplicate:
            fail("COLLECTION_NAME_CONFLICT")
        row.name, row.name_key, row.active = body.name, body.name.casefold(), body.active
        touch(row, identity)
        session.flush()
        record(session, tenant, identity.user_id, AuditAction.COLLECTION_UPDATED, row.id)
        return data(row)


def touch(row: Collection, identity: ActorIdentity) -> None:
    if row.version >= 2_147_483_646:
        fail("COLLECTION_VERSION_LIMIT")
    row.version += 1
    row.updated_at, row.updated_by = datetime.now(UTC), identity.user_id


def items(session: Session, tenant: UUID, collection: UUID) -> dict[str, Any]:
    row = require(session, tenant, collection)
    records = session.execute(
        select(CollectionItem, Source)
        .join(
            Source,
            (Source.tenant_id == CollectionItem.tenant_id)
            & (Source.id == CollectionItem.source_id),
        )
        .where(
            CollectionItem.tenant_id == tenant,
            CollectionItem.collection_id == collection,
            CollectionItem.active.is_(True),
        )
        .order_by(CollectionItem.position, CollectionItem.source_id)
        .limit(MAX_ITEMS + 1)
    ).all()
    if len(records) > MAX_ITEMS:
        fail("COLLECTION_ITEM_INVENTORY_LIMIT")
    return {
        "collection": data(row),
        "items": [
            {
                "source_id": str(item.source_id),
                "position": item.position,
                "url": source.canonical_url,
            }
            for item, source in records
        ],
    }


def set_item(
    identity: ActorIdentity, tenant: UUID, collection: UUID, source: UUID, body: ItemInput
) -> dict[str, Any]:
    with authorized_transaction(identity, tenant) as (session, _role):
        row = require(session, tenant, collection)
        if row.version != body.expected_version:
            fail("COLLECTION_VERSION_CONFLICT")
        content = session.scalar(
            select(Source).where(
                Source.tenant_id == tenant, Source.id == source, Source.status == "accepted"
            )
        )
        if content is None:
            fail("SOURCE_NOT_FOUND", 404)
        item = session.get(CollectionItem, (tenant, collection, source))
        if (
            item is not None
            and item.active == body.active
            and (not body.active or item.position == body.position)
        ):
            return data(row)
        if item is None and not body.active:
            return data(row)
        if body.active and not row.active:
            fail("COLLECTION_ARCHIVED")
        total = (
            session.scalar(
                select(func.count())
                .select_from(CollectionItem)
                .where(
                    CollectionItem.tenant_id == tenant, CollectionItem.collection_id == collection
                )
            )
            or 0
        )
        if item is None and total >= MAX_ITEMS:
            fail("COLLECTION_ITEM_QUOTA", 429)
        if item is None:
            item = CollectionItem(
                tenant_id=tenant,
                collection_id=collection,
                source_id=source,
                created_by=identity.user_id,
                updated_by=identity.user_id,
                active=body.active,
                position=body.position,
            )
            session.add(item)
        else:
            item.active, item.position = body.active, body.position
            item.updated_by, item.updated_at = identity.user_id, datetime.now(UTC)
        touch(row, identity)
        session.flush()
        record(session, tenant, identity.user_id, AuditAction.COLLECTION_ITEM_UPDATED, row.id)
        return data(row)
