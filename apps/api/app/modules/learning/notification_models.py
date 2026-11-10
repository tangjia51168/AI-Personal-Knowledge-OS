"""Personal opt-in reminders, opaque scheduling discovery and durable receipts."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKeyConstraint, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id"),
        UniqueConstraint("tenant_id", "id", "user_id"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    user_id: Mapped[UUID]
    enabled: Mapped[bool] = mapped_column(default=False)
    email: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationTick(Base):
    __tablename__ = "notification_ticks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "preference_id", "user_id"],
            [
                "notification_preferences.tenant_id",
                "notification_preferences.id",
                "notification_preferences.user_id",
            ],
        ),
    )
    preference_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID]
    user_id: Mapped[UUID]
    enabled: Mapped[bool] = mapped_column(default=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "occurrence"),
        ForeignKeyConstraint(
            ["tenant_id", "preference_id", "user_id"],
            [
                "notification_preferences.tenant_id",
                "notification_preferences.id",
                "notification_preferences.user_id",
            ],
        ),
        ForeignKeyConstraint(["tenant_id", "note_id"], ["notes.tenant_id", "notes.id"]),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    user_id: Mapped[UUID]
    preference_id: Mapped[UUID]
    preference_version: Mapped[int]
    occurrence: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(8))
    note_id: Mapped[UUID | None]
    reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_state: Mapped[str] = mapped_column(String(20))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
