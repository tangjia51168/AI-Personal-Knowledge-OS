"""Atomic manual classification, serialized with other authorized space writers."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import User
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.identity.access import ActorIdentity, authorized_transaction
from app.modules.identity.models import AuthSession
from app.modules.notes.models import Note
from app.modules.organization.content import LabelInput, LabelUpdateInput
from app.modules.organization.models import KnowledgeLabel, NoteLabel

MAX_SPACE_LABELS = 100
MAX_NOTE_LABELS = 20


def recheck_actor(session: Session, identity: ActorIdentity) -> None:
    # The space writer lock can wait. Do not retain a previously read login or
    # user state across that wait; logout/reset or deactivation may have committed.
    # Use current time, not PostgreSQL now() (which is the transaction start).
    login = session.scalar(
        select(AuthSession)
        .where(
            AuthSession.token_hash == identity.token_hash,
            AuthSession.user_id == identity.user_id,
            AuthSession.expires_at > datetime.now(UTC),
        )
        .execution_options(populate_existing=True)
    )
    user = session.scalar(
        select(User).where(User.id == identity.user_id).execution_options(populate_existing=True)
    )
    if (
        login is None
        or user is None
        or user.status != "active"
        or (get_settings().email_verification_required and not user.email_verified)
    ):
        raise HTTPException(401, detail={"code": "UNAUTHORIZED"})


def label_data(row: KnowledgeLabel) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "name": row.name,
        "created_at": row.created_at.isoformat(),
    }


def managed_label_data(row: KnowledgeLabel) -> dict[str, Any]:
    return {
        **label_data(row),
        "active": row.active,
        "version": row.version,
        "updated_at": row.updated_at.isoformat(),
        "parent_id": str(row.parent_id) if row.parent_id else None,
    }


def require_note(session: Session, tenant_id: UUID, note_id: UUID) -> None:
    if (
        session.scalar(select(Note.id).where(Note.tenant_id == tenant_id, Note.id == note_id))
        is None
    ):
        raise HTTPException(404, detail={"code": "NOTE_NOT_FOUND"})


def labels_for(
    session: Session, tenant_id: UUID, note_id: UUID | None = None
) -> list[dict[str, Any]]:
    statement = select(KnowledgeLabel).where(KnowledgeLabel.tenant_id == tenant_id)
    if note_id is not None:
        require_note(session, tenant_id, note_id)
        statement = statement.where(
            select(NoteLabel.id)
            .where(
                NoteLabel.tenant_id == tenant_id,
                NoteLabel.note_id == note_id,
                NoteLabel.label_id == KnowledgeLabel.id,
                NoteLabel.active.is_(True),
            )
            .exists()
        )
    # The writer enforces the same quota. Read one beyond it to refuse silent
    # truncation if an operator has populated inconsistent over-quota data.
    limit = MAX_NOTE_LABELS if note_id is not None else MAX_SPACE_LABELS
    rows = list(
        session.scalars(
            statement.order_by(
                KnowledgeLabel.kind, KnowledgeLabel.name_key, KnowledgeLabel.id
            ).limit(limit + 1)
        )
    )
    if len(rows) > limit:
        raise HTTPException(409, detail={"code": "LABEL_INVENTORY_LIMIT"})
    return [label_data(row) for row in rows]


def managed_labels(session: Session, tenant_id: UUID) -> list[dict[str, Any]]:
    rows = list(
        session.scalars(
            select(KnowledgeLabel)
            .where(KnowledgeLabel.tenant_id == tenant_id)
            .order_by(KnowledgeLabel.kind, KnowledgeLabel.name_key, KnowledgeLabel.id)
            .limit(MAX_SPACE_LABELS + 1)
        )
    )
    if len(rows) > MAX_SPACE_LABELS:
        raise HTTPException(409, detail={"code": "LABEL_INVENTORY_LIMIT"})
    return [managed_label_data(row) for row in rows]


def update_label(
    identity: ActorIdentity, tenant_id: UUID, label_id: UUID, body: LabelUpdateInput
) -> dict[str, Any]:
    if body.name is None and body.active is None and "parent_id" not in body.model_fields_set:
        raise HTTPException(422, detail={"code": "LABEL_UPDATE_EMPTY"})
    with authorized_transaction(identity, tenant_id) as (session, _role):
        recheck_actor(session, identity)
        row = session.scalar(
            select(KnowledgeLabel)
            .where(KnowledgeLabel.tenant_id == tenant_id, KnowledgeLabel.id == label_id)
            .with_for_update()
        )
        if row is None:
            raise HTTPException(404, detail={"code": "LABEL_NOT_FOUND"})
        if row.version != body.expected_version:
            raise HTTPException(409, detail={"code": "LABEL_VERSION_CONFLICT"})
        parent_changed = "parent_id" in body.model_fields_set and body.parent_id != row.parent_id
        if "parent_id" in body.model_fields_set and row.kind != "topic":
            raise HTTPException(422, detail={"code": "TOPIC_PARENT_ONLY"})
        if parent_changed:
            require_parent(session, tenant_id, row.id, body.parent_id)
        if (
            (body.name is None or body.name == row.name)
            and (body.active is None or body.active == row.active)
            and not parent_changed
        ):
            return managed_label_data(row)
        if body.name_key is not None and (body.name_key != row.name_key or body.name != row.name):
            duplicate = session.scalar(
                select(KnowledgeLabel.id).where(
                    KnowledgeLabel.tenant_id == tenant_id,
                    KnowledgeLabel.kind == row.kind,
                    KnowledgeLabel.name_key == body.name_key,
                    KnowledgeLabel.id != row.id,
                )
            )
            if duplicate is not None:
                raise HTTPException(409, detail={"code": "LABEL_NAME_CONFLICT"})
            assert body.name is not None
            row.name = body.name
            row.name_key = body.name_key
        if body.active is not None:
            row.active = body.active
        if parent_changed:
            row.parent_id = body.parent_id
        row.updated_by = identity.user_id
        row.version += 1
        row.updated_at = datetime.now(UTC)
        session.flush()
        record(session, tenant_id, identity.user_id, AuditAction.LABEL_UPDATED, row.id)
        return managed_label_data(row)


def require_parent(
    session: Session, tenant_id: UUID, label_id: UUID, parent_id: UUID | None
) -> None:
    """Validate one bounded tenant inventory under the existing tenant write lock."""
    if parent_id is None:
        return
    rows = list(
        session.scalars(
            select(KnowledgeLabel)
            .where(KnowledgeLabel.tenant_id == tenant_id)
            .limit(MAX_SPACE_LABELS + 1)
        )
    )
    if len(rows) > MAX_SPACE_LABELS:
        raise HTTPException(409, detail={"code": "LABEL_INVENTORY_LIMIT"})
    by_id = {item.id: item for item in rows}
    parent = by_id.get(parent_id)
    if parent is None or parent.kind != "topic":
        raise HTTPException(404, detail={"code": "TOPIC_PARENT_NOT_FOUND"})
    if not parent.active:
        raise HTTPException(409, detail={"code": "TOPIC_PARENT_ARCHIVED"})
    seen = {label_id}
    current: UUID | None = parent_id
    while current is not None:
        if current in seen:
            raise HTTPException(409, detail={"code": "TOPIC_PARENT_CYCLE"})
        seen.add(current)
        ancestor = by_id.get(current)
        if ancestor is None or ancestor.kind != "topic":
            raise HTTPException(409, detail={"code": "TOPIC_HIERARCHY_INVALID"})
        current = ancestor.parent_id


def create_label(identity: ActorIdentity, tenant_id: UUID, body: LabelInput) -> dict[str, Any]:
    with authorized_transaction(identity, tenant_id) as (session, _role):
        recheck_actor(session, identity)
        existing = session.scalar(
            select(KnowledgeLabel).where(
                KnowledgeLabel.tenant_id == tenant_id,
                KnowledgeLabel.kind == body.kind,
                KnowledgeLabel.name_key == body.name_key,
            )
        )
        if existing is not None:
            if not existing.active:
                raise HTTPException(409, detail={"code": "LABEL_ARCHIVED"})
            return label_data(existing)
        count = session.scalar(
            select(func.count())
            .select_from(KnowledgeLabel)
            .where(KnowledgeLabel.tenant_id == tenant_id)
        )
        if count is not None and count >= MAX_SPACE_LABELS:
            raise HTTPException(429, detail={"code": "LABEL_QUOTA_EXCEEDED"})
        row = KnowledgeLabel(
            id=uuid4(),
            tenant_id=tenant_id,
            kind=body.kind,
            name=body.name,
            name_key=body.name_key,
            created_by=identity.user_id,
        )
        session.add(row)
        session.flush()
        record(session, tenant_id, identity.user_id, AuditAction.LABEL_CREATED, row.id)
        return label_data(row)


def set_note_label(
    identity: ActorIdentity, tenant_id: UUID, note_id: UUID, label_id: UUID, *, active: bool
) -> list[dict[str, Any]]:
    with authorized_transaction(identity, tenant_id) as (session, _role):
        recheck_actor(session, identity)
        require_note(session, tenant_id, note_id)
        label = session.scalar(
            select(KnowledgeLabel).where(
                KnowledgeLabel.tenant_id == tenant_id, KnowledgeLabel.id == label_id
            )
        )
        if label is None:
            raise HTTPException(404, detail={"code": "LABEL_NOT_FOUND"})
        if active and not label.active:
            raise HTTPException(409, detail={"code": "LABEL_ARCHIVED"})
        association = session.scalar(
            select(NoteLabel).where(
                NoteLabel.tenant_id == tenant_id,
                NoteLabel.note_id == note_id,
                NoteLabel.label_id == label_id,
            )
        )
        if (association is not None and association.active == active) or (
            association is None and not active
        ):
            return labels_for(session, tenant_id, note_id)
        if active:
            count = session.scalar(
                select(func.count())
                .select_from(NoteLabel)
                .where(
                    NoteLabel.tenant_id == tenant_id,
                    NoteLabel.note_id == note_id,
                    NoteLabel.active.is_(True),
                )
            )
            if count is not None and count >= MAX_NOTE_LABELS:
                raise HTTPException(429, detail={"code": "NOTE_LABEL_QUOTA_EXCEEDED"})
        if association is None:
            association = NoteLabel(
                id=uuid4(),
                tenant_id=tenant_id,
                note_id=note_id,
                label_id=label_id,
                created_by=identity.user_id,
                updated_by=identity.user_id,
                active=True,
            )
            session.add(association)
        else:
            association.active = active
            association.updated_by = identity.user_id
            association.updated_at = datetime.now(UTC)
        session.flush()
        record(
            session,
            tenant_id,
            identity.user_id,
            AuditAction.LABEL_ATTACHED if active else AuditAction.LABEL_REMOVED,
            association.id,
        )
        return labels_for(session, tenant_id, note_id)
