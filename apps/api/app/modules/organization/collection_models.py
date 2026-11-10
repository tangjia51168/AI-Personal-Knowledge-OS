"""Shared source grouping; never duplicates source content or implies platform sync."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class Collection(Base):
    __tablename__ = "collections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "kind", "name_key"),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        CheckConstraint("kind IN ('folder','collection')"),
        CheckConstraint("length(name) BETWEEN 1 AND 60 AND octet_length(name)<=240"),
        CheckConstraint("length(name_key) BETWEEN 1 AND 180 AND octet_length(name_key)<=720"),
        CheckConstraint("version>=1 AND updated_at>=created_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(60))
    name_key: Mapped[str] = mapped_column(String(180))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(default=1)
    created_by: Mapped[UUID]
    updated_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CollectionItem(Base):
    __tablename__ = "collection_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "collection_id"], ["collections.tenant_id", "collections.id"]
        ),
        ForeignKeyConstraint(["tenant_id", "source_id"], ["sources.tenant_id", "sources.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        CheckConstraint("position BETWEEN 0 AND 199 AND updated_at>=created_at"),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    collection_id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int]
    created_by: Mapped[UUID]
    updated_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
