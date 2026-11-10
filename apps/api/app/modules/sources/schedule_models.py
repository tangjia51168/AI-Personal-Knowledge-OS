"""Tenant business plans and a payload-free global scheduling index."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKeyConstraint, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class SourceSchedule(Base):
    __tablename__ = "source_schedules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "id", "created_by"),
        UniqueConstraint("tenant_id", "created_by", "source_id"),
        ForeignKeyConstraint(["tenant_id", "source_id"], ["sources.tenant_id", "sources.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    source_id: Mapped[UUID]
    created_by: Mapped[UUID]
    interval_minutes: Mapped[int]
    active: Mapped[bool] = mapped_column(default=True)
    version: Mapped[int] = mapped_column(default=1)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScheduleTick(Base):
    __tablename__ = "source_schedule_ticks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "schedule_id", "requested_by"],
            ["source_schedules.tenant_id", "source_schedules.id", "source_schedules.created_by"],
        ),
    )
    schedule_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID]
    requested_by: Mapped[UUID]
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(default=True)


class ScheduleJob(Base):
    __tablename__ = "source_schedule_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "schedule_id"], ["source_schedules.tenant_id", "source_schedules.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"], ["processing_jobs.tenant_id", "processing_jobs.id"]
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    job_id: Mapped[UUID] = mapped_column(primary_key=True)
    schedule_id: Mapped[UUID]
