from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class NoteLearning(Base):
    __tablename__ = "note_learning"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "note_id", "user_id"),
        ForeignKeyConstraint(["tenant_id", "note_id"], ["notes.tenant_id", "notes.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        CheckConstraint("status IN ('unseen','seen','skimmed','deep_read')"),
        Index("ix_note_learning_due", "tenant_id", "user_id", "reminder_at", "status"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    note_id: Mapped[UUID]
    user_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(String(16), default="unseen", server_default="unseen")
    reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
