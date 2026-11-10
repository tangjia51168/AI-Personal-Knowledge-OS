"""Versioned shared label definitions and revision-independent note membership."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class KnowledgeConcept(Base):
    __tablename__ = "knowledge_concepts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "idempotency_key"),
        ForeignKeyConstraint(
            ["tenant_id", "note_id", "snapshot_id", "revision_id"],
            [
                "note_revisions.tenant_id",
                "note_revisions.note_id",
                "note_revisions.snapshot_id",
                "note_revisions.id",
            ],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "revision_id", "snapshot_id", "evidence_key"],
            [
                "evidence_items.tenant_id",
                "evidence_items.revision_id",
                "evidence_items.snapshot_id",
                "evidence_items.evidence_key",
            ],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        CheckConstraint("length(name) BETWEEN 1 AND 60 AND octet_length(name)<=240"),
        CheckConstraint(
            "length(definition) BETWEEN 1 AND 2000 AND octet_length(definition)<=8000 "
            "AND length(btrim(definition))>0"
        ),
        CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$'"),
        Index("ix_concepts_revision", "tenant_id", "note_id", "revision_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    note_id: Mapped[UUID]
    snapshot_id: Mapped[UUID]
    revision_id: Mapped[UUID]
    created_by: Mapped[UUID]
    idempotency_key: Mapped[UUID]
    request_sha256: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(60))
    definition: Mapped[str] = mapped_column(Text)
    evidence_key: Mapped[str] = mapped_column(String(4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeLabel(Base):
    __tablename__ = "knowledge_labels"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "kind", "name_key"),
        UniqueConstraint("tenant_id", "id", "kind", name="uq_label_tenant_id_kind"),
        ForeignKeyConstraint(
            ["tenant_id", "parent_id", "kind"],
            ["knowledge_labels.tenant_id", "knowledge_labels.id", "knowledge_labels.kind"],
            name="fk_label_topic_parent",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        CheckConstraint("kind IN ('topic','tag')"),
        CheckConstraint("length(name) BETWEEN 1 AND 60 AND octet_length(name)<=240"),
        CheckConstraint("length(name_key) BETWEEN 1 AND 180 AND octet_length(name_key)<=720"),
        CheckConstraint("version >= 1"),
        CheckConstraint("updated_at>=created_at"),
        CheckConstraint(
            "parent_id IS NULL OR (kind='topic' AND parent_id<>id)", name="ck_label_topic_parent"
        ),
        Index("ix_label_parent", "tenant_id", "parent_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(60))
    name_key: Mapped[str] = mapped_column(String(180))
    created_by: Mapped[UUID]
    updated_by: Mapped[UUID | None]
    parent_id: Mapped[UUID | None]
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NoteLabel(Base):
    __tablename__ = "note_labels"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "note_id", "label_id"),
        ForeignKeyConstraint(["tenant_id", "note_id"], ["notes.tenant_id", "notes.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "label_id"], ["knowledge_labels.tenant_id", "knowledge_labels.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        CheckConstraint("updated_at>=created_at"),
        Index("ix_note_labels_lookup", "tenant_id", "label_id", "active", "note_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    note_id: Mapped[UUID]
    label_id: Mapped[UUID]
    created_by: Mapped[UUID]
    updated_by: Mapped[UUID]
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
