"""Append-only intake records; an accepted URL has not been fetched."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class SourceBatch(Base):
    __tablename__ = "source_batches"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "idempotency_key"),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        CheckConstraint(
            "accepted_count >= 0 AND rejected_count >= 0 AND "
            "accepted_count + rejected_count BETWEEN 1 AND 20"
        ),
        CheckConstraint("input_sha256 ~ '^[0-9a-f]{64}$'"),
        Index("ix_source_batches_tenant_created", "tenant_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    created_by: Mapped[UUID]
    idempotency_key: Mapped[UUID]
    input_sha256: Mapped[str] = mapped_column(String(64))
    accepted_count: Mapped[int]
    rejected_count: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        # A SHA-256 index avoids PostgreSQL's btree tuple limit on Unicode URLs.
        UniqueConstraint("tenant_id", "canonical_sha256"),
        ForeignKeyConstraint(
            ["tenant_id", "batch_id"], ["source_batches.tenant_id", "source_batches.id"]
        ),
        CheckConstraint("status IN ('accepted', 'rejected')"),
        CheckConstraint(
            "(status='accepted' AND canonical_url IS NOT NULL AND "
            "canonical_sha256 IS NOT NULL AND canonical_sha256 ~ '^[0-9a-f]{64}$' "
            "AND rejection_code IS NULL) OR "
            "(status='rejected' AND canonical_url IS NULL AND canonical_sha256 IS NULL "
            "AND rejection_code IS NOT NULL)"
        ),
        Index("ix_sources_tenant_created", "tenant_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    batch_id: Mapped[UUID]
    original_url: Mapped[str] = mapped_column(String(2048))
    canonical_url: Mapped[str | None] = mapped_column(String(2048))
    canonical_sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    rejection_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceSnapshot(Base):
    __tablename__ = "source_snapshots"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "source_id", "version"),
        UniqueConstraint("tenant_id", "job_id"),
        ForeignKeyConstraint(["tenant_id", "source_id"], ["sources.tenant_id", "sources.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"], ["processing_jobs.tenant_id", "processing_jobs.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        CheckConstraint("version >= 1"),
        CheckConstraint("content_kind IN ('article', 'metadata', 'transcript')"),
        CheckConstraint("provenance IN ('platform_collection','user_import')"),
        CheckConstraint(
            "provenance != 'user_import' OR "
            "(platform='user_import' AND content_kind IN ('article','transcript'))"
        ),
        CheckConstraint("length(body_text) BETWEEN 1 AND 60000"),
        CheckConstraint("raw_sha256 ~ '^[0-9a-f]{64}$'"),
        Index("ix_snapshots_source_created", "tenant_id", "source_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    source_id: Mapped[UUID]
    job_id: Mapped[UUID]
    created_by: Mapped[UUID]
    version: Mapped[int]
    provenance: Mapped[str] = mapped_column(
        String(32), default="platform_collection", server_default="platform_collection"
    )
    fetched_url: Mapped[str] = mapped_column(String(2048))
    platform: Mapped[str] = mapped_column(String(32))
    content_kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(300))
    author: Mapped[str] = mapped_column(String(200))
    body_text: Mapped[str] = mapped_column(Text)
    raw_sha256: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSONB)
    limitations: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
