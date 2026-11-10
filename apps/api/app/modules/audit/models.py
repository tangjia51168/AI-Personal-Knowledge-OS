from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class AuditAction(StrEnum):
    SPACE_CREATED = "space.created"
    MEMBER_ADDED = "member.added"
    MEMBER_REMOVED = "member.removed"
    JOB_CREATED = "job.created"
    JOB_CANCELLED = "job.cancelled"
    JOB_RETRIED = "job.retried"
    FILE_UPLOADED = "file.uploaded"
    FILE_DELETED = "file.deleted"
    PROVIDER_CREATED = "provider.created"
    PROVIDER_DISABLED = "provider.disabled"
    PROVIDER_REENCRYPTED = "provider.reencrypted"
    PROVIDER_UPDATED = "provider.updated"
    SETTINGS_UPDATED = "settings.updated"
    SOURCE_BATCH_CREATED = "source.batch.created"
    SOURCE_SNAPSHOT_CREATED = "source.snapshot.created"
    SOURCE_CONTENT_IMPORTED = "source.content.imported"
    NOTE_GENERATED = "notes.generated"
    NOTE_EDITED = "notes.edited"
    NOTE_INDEXED = "notes.indexed"
    NOTE_ANNOTATED = "notes.annotated"
    NOTE_LINKED = "notes.linked"
    NOTE_UNLINKED = "notes.unlinked"
    LABEL_CREATED = "organization.label_created"
    LABEL_ATTACHED = "organization.label_attached"
    LABEL_REMOVED = "organization.label_removed"
    LABEL_UPDATED = "organization.label_updated"
    CONCEPT_CREATED = "organization.concept_created"
    LEARNING_UPDATED = "learning.updated"
    RESEARCH_CREATED = "research.created"
    RESEARCH_FINISHED = "research.finished"
    SCHEDULE_CREATED = "source.schedule_created"
    SCHEDULE_UPDATED = "source.schedule_updated"
    NOTIFICATION_PREFERENCE = "notification.preference"
    NOTIFICATION_CREATED = "notification.created"
    NOTIFICATION_READ = "notification.read"
    NOTIFICATION_DISPATCH = "notification.dispatch"
    NOTIFICATION_FINISHED = "notification.finished"
    COLLECTION_CREATED = "collection.created"
    COLLECTION_UPDATED = "collection.updated"
    COLLECTION_ITEM_UPDATED = "collection.item_updated"


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "actor_id"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        CheckConstraint("action IN (" + ",".join(repr(a.value) for a in AuditAction) + ")"),
        Index("ix_audit_tenant_cursor", "tenant_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    actor_id: Mapped[UUID]
    action: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
