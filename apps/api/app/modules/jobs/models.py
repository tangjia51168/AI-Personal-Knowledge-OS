from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKeyConstraint, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class JobDispatch(Base):
    __tablename__ = "job_dispatches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "job_id"], ["processing_jobs.tenant_id", "processing_jobs.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "requested_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    job_id: Mapped[UUID] = mapped_column(unique=True)
    requested_by: Mapped[UUID]
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    done: Mapped[bool] = mapped_column(default=False)


class JobRecord:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    job_id: Mapped[UUID]
    number: Mapped[int]
    status: Mapped[str] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobStep(JobRecord, Base):
    __tablename__ = "job_steps"
    __table_args__ = (
        UniqueConstraint("tenant_id", "job_id", "number"),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"], ["processing_jobs.tenant_id", "processing_jobs.id"]
        ),
    )


class JobAttempt(JobRecord, Base):
    __tablename__ = "job_attempts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "job_id", "number"),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"], ["processing_jobs.tenant_id", "processing_jobs.id"]
        ),
    )
