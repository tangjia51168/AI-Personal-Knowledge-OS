"""Durable generation claims and immutable note revisions."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models import Base


class NoteRun(Base):
    __tablename__ = "note_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "idempotency_key"),
        ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"], ["source_snapshots.tenant_id", "source_snapshots.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        CheckConstraint("status IN ('pending','succeeded','failed','unknown')"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    snapshot_id: Mapped[UUID]
    created_by: Mapped[UUID]
    idempotency_key: Mapped[UUID]
    status: Mapped[str] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SummaryCheckpoint(Base):
    __tablename__ = "summary_checkpoints"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "run_id"], ["note_runs.tenant_id", "note_runs.id"]),
        ForeignKeyConstraint(["tenant_id", "usage_id"], ["ai_usage.tenant_id", "ai_usage.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "provider_id"], ["ai_providers.tenant_id", "ai_providers.id"]
        ),
        UniqueConstraint("tenant_id", "usage_id"),
        CheckConstraint("position BETWEEN 0 AND 8"),
        CheckConstraint("prompt_sha256 ~ '^[0-9a-f]{64}$'"),
        CheckConstraint("protocol='bounded-map-synthesis-v2'"),
        CheckConstraint("status IN ('dispatched','succeeded','failed','unknown')"),
        CheckConstraint("(status='succeeded')=(output IS NOT NULL)"),
        CheckConstraint("output IS NULL OR octet_length(output::text)<=65536"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    position: Mapped[int] = mapped_column(primary_key=True)
    usage_id: Mapped[UUID]
    provider_id: Mapped[UUID]
    model: Mapped[str] = mapped_column(String(100))
    prompt_sha256: Mapped[str] = mapped_column(String(64))
    protocol: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(16))
    output: Mapped[dict | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResearchRun(Base):
    __tablename__ = "research_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "idempotency_key"),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    created_by: Mapped[UUID]
    idempotency_key: Mapped[UUID]
    request_sha256: Mapped[str] = mapped_column(String(64))
    question: Mapped[str] = mapped_column(Text)
    query: Mapped[str] = mapped_column(String(120))
    citations: Mapped[list["ResearchCitation"]] = relationship(
        lazy="selectin", order_by="ResearchCitation.key", viewonly=True
    )
    status: Mapped[str] = mapped_column(String(32))
    answer: Mapped[dict | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResearchCitation(Base):
    __tablename__ = "research_citations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "run_id"], ["research_runs.tenant_id", "research_runs.id"]
        ),
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
        CheckConstraint("key ~ '^R00[1-8]$'"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(4), primary_key=True)
    note_id: Mapped[UUID]
    revision_id: Mapped[UUID]
    snapshot_id: Mapped[UUID]
    evidence_key: Mapped[str] = mapped_column(String(4))
    version: Mapped[int]
    title: Mapped[str] = mapped_column(String(200))
    provenance: Mapped[str] = mapped_column(String(32))
    content_kind: Mapped[str] = mapped_column(String(32))
    quote: Mapped[str] = mapped_column(String(800))


class Note(Base):
    __tablename__ = "notes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "source_id"),
        ForeignKeyConstraint(["tenant_id", "source_id"], ["sources.tenant_id", "sources.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    source_id: Mapped[UUID]
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NoteRevision(Base):
    __tablename__ = "note_revisions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "note_id", "version"),
        UniqueConstraint("tenant_id", "run_id"),
        UniqueConstraint("tenant_id", "edit_key"),
        UniqueConstraint("tenant_id", "note_id", "snapshot_id", "id"),
        ForeignKeyConstraint(["tenant_id", "note_id"], ["notes.tenant_id", "notes.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"], ["source_snapshots.tenant_id", "source_snapshots.id"]
        ),
        ForeignKeyConstraint(["tenant_id", "run_id"], ["note_runs.tenant_id", "note_runs.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "note_id", "snapshot_id", "base_revision_id"],
            [
                "note_revisions.tenant_id",
                "note_revisions.note_id",
                "note_revisions.snapshot_id",
                "note_revisions.id",
            ],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        CheckConstraint("version >= 1"),
        CheckConstraint(
            "(origin='ai_generated' AND run_id IS NOT NULL AND base_revision_id IS NULL "
            "AND edit_key IS NULL AND request_sha256 IS NULL) OR "
            "(origin='user_edit' AND run_id IS NULL "
            "AND (base_revision_id IS NOT NULL OR version=1) "
            "AND edit_key IS NOT NULL AND request_sha256 IS NOT NULL)"
        ),
        CheckConstraint("request_sha256 IS NULL OR request_sha256 ~ '^[0-9a-f]{64}$'"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    note_id: Mapped[UUID]
    snapshot_id: Mapped[UUID]
    run_id: Mapped[UUID | None]
    origin: Mapped[str] = mapped_column(String(32), server_default="ai_generated")
    base_revision_id: Mapped[UUID | None]
    edit_key: Mapped[UUID | None]
    request_sha256: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[UUID]
    version: Mapped[int]
    title: Mapped[str] = mapped_column(String(200))
    content_md: Mapped[str] = mapped_column(Text)
    document: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NoteSection(Base):
    __tablename__ = "note_sections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "revision_id", "kind"),
        ForeignKeyConstraint(
            ["tenant_id", "revision_id"], ["note_revisions.tenant_id", "note_revisions.id"]
        ),
        CheckConstraint(
            "kind IN ('overview','core_claims','steps','examples','numbers','takeaways')"
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    revision_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(32))
    items: Mapped[list] = mapped_column(JSONB)


class EvidenceItem(Base):
    __tablename__ = "evidence_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "revision_id", "evidence_key"),
        UniqueConstraint(
            "tenant_id",
            "revision_id",
            "snapshot_id",
            "evidence_key",
            name="uq_evidence_revision_snapshot_key",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "revision_id"], ["note_revisions.tenant_id", "note_revisions.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"], ["source_snapshots.tenant_id", "source_snapshots.id"]
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    revision_id: Mapped[UUID]
    snapshot_id: Mapped[UUID]
    evidence_key: Mapped[str] = mapped_column(String(4))
    quote: Mapped[str] = mapped_column(String(800))


class NoteAnnotation(Base):
    __tablename__ = "note_annotations"
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
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        CheckConstraint(
            "kind IN ('overview','core_claims','steps','examples','numbers','takeaways')"
        ),
        CheckConstraint("item_index BETWEEN 0 AND 5 AND (kind<>'overview' OR item_index=0)"),
        CheckConstraint("length(quote) BETWEEN 1 AND 1200 AND octet_length(quote)<=4800"),
        CheckConstraint(
            "length(body) BETWEEN 1 AND 2000 AND octet_length(body)<=8000 AND length(btrim(body))>0"
        ),
        CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$'"),
        Index(
            "ix_note_annotations_page", "tenant_id", "note_id", "revision_id", "created_at", "id"
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    note_id: Mapped[UUID]
    snapshot_id: Mapped[UUID]
    revision_id: Mapped[UUID]
    created_by: Mapped[UUID]
    idempotency_key: Mapped[UUID]
    request_sha256: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    item_index: Mapped[int]
    quote: Mapped[str] = mapped_column(String(1200))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NoteLink(Base):
    __tablename__ = "note_links"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "left_note_id", "right_note_id"),
        ForeignKeyConstraint(["tenant_id", "left_note_id"], ["notes.tenant_id", "notes.id"]),
        ForeignKeyConstraint(["tenant_id", "right_note_id"], ["notes.tenant_id", "notes.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        CheckConstraint("left_note_id < right_note_id"),
        CheckConstraint("updated_at >= created_at"),
        Index("ix_note_links_left", "tenant_id", "left_note_id", "active", "right_note_id"),
        Index("ix_note_links_right", "tenant_id", "right_note_id", "active", "left_note_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    left_note_id: Mapped[UUID]
    right_note_id: Mapped[UUID]
    created_by: Mapped[UUID]
    updated_by: Mapped[UUID]
    active: Mapped[bool] = mapped_column(default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
