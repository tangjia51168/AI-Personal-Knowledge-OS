"""Explicit source refresh scheduling, reusing authorization, quota and outbox."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import ProcessingJob, TenantMember, User
from app.db.session import get_engine
from app.integrations.queue import get_task_queue
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.identity.access import (
    Actor,
    ActorIdentity,
    SpaceAccess,
    authorized_transaction,
    locked_membership,
    set_tenant,
    set_user,
)
from app.modules.jobs.models import JobStep
from app.modules.sources.collection import queue_collection, source_for
from app.modules.sources.schedule_models import ScheduleJob, ScheduleTick, SourceSchedule


class ScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: UUID
    interval_minutes: int = Field(ge=360, le=10080, strict=True)
    consent: StrictBool


class ScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expected_version: int = Field(ge=1, strict=True)
    active: StrictBool
    consent: StrictBool


def represent(row: SourceSchedule) -> dict:
    return {
        "id": str(row.id),
        "source_id": str(row.source_id),
        "active": row.active,
        "version": row.version,
        "interval_minutes": row.interval_minutes,
        "next_run_at": row.next_run_at.isoformat(),
        "last_error": row.last_error,
    }


def own(session: Session, tenant: UUID, user: UUID, schedule: UUID) -> SourceSchedule:
    row = session.scalar(
        select(SourceSchedule).where(
            SourceSchedule.tenant_id == tenant,
            SourceSchedule.id == schedule,
            SourceSchedule.created_by == user,
        )
    )
    if row is None:
        raise HTTPException(404, detail={"code": "SCHEDULE_NOT_FOUND"})
    return row


def create(identity: ActorIdentity, tenant: UUID, body: ScheduleInput) -> dict:
    if not body.consent:
        raise HTTPException(400, detail={"code": "SCHEDULE_CONSENT_REQUIRED"})
    with authorized_transaction(identity, tenant) as (session, _role):
        source_for(session, tenant, body.source_id)
        previous = session.scalar(
            select(SourceSchedule).where(
                SourceSchedule.tenant_id == tenant,
                SourceSchedule.created_by == identity.user_id,
                SourceSchedule.source_id == body.source_id,
            )
        )
        if previous is not None:
            if previous.interval_minutes != body.interval_minutes:
                raise HTTPException(409, detail={"code": "SCHEDULE_ALREADY_EXISTS"})
            return represent(previous)
        count = session.scalar(
            select(func.count())
            .select_from(SourceSchedule)
            .where(
                SourceSchedule.tenant_id == tenant, SourceSchedule.created_by == identity.user_id
            )
        )
        if (count or 0) >= 20:
            raise HTTPException(429, detail={"code": "SCHEDULE_QUOTA"})
        due = datetime.now(UTC) + timedelta(minutes=body.interval_minutes)
        row = SourceSchedule(
            tenant_id=tenant,
            created_by=identity.user_id,
            source_id=body.source_id,
            interval_minutes=body.interval_minutes,
            next_run_at=due,
        )
        session.add(row)
        session.flush()
        session.add(
            ScheduleTick(
                schedule_id=row.id, tenant_id=tenant, requested_by=identity.user_id, due_at=due
            )
        )
        record(session, tenant, identity.user_id, AuditAction.SCHEDULE_CREATED, row.id)
        return represent(row)


def update(identity: ActorIdentity, tenant: UUID, schedule: UUID, body: ScheduleUpdate) -> dict:
    if body.active and not body.consent:
        raise HTTPException(400, detail={"code": "SCHEDULE_CONSENT_REQUIRED"})
    with authorized_transaction(identity, tenant) as (session, _role):
        row = own(session, tenant, identity.user_id, schedule)
        if row.version != body.expected_version:
            raise HTTPException(409, detail={"code": "SCHEDULE_VERSION_CONFLICT"})
        if row.active == body.active:
            return represent(row)
        row.active, row.version, row.last_error = body.active, row.version + 1, None
        tick = session.get(ScheduleTick, schedule)
        if tick is None or tick.tenant_id != tenant or tick.requested_by != identity.user_id:
            raise HTTPException(409, detail={"code": "SCHEDULE_INDEX_INVALID"})
        tick.enabled = row.active
        if row.active:
            row.next_run_at = tick.due_at = datetime.now(UTC) + timedelta(
                minutes=row.interval_minutes
            )
        else:
            jobs = session.scalars(
                select(ProcessingJob)
                .join(
                    ScheduleJob,
                    (ScheduleJob.tenant_id == ProcessingJob.tenant_id)
                    & (ScheduleJob.job_id == ProcessingJob.id),
                )
                .where(
                    ScheduleJob.tenant_id == tenant,
                    ScheduleJob.schedule_id == schedule,
                    ProcessingJob.status.in_(["queued", "running"]),
                )
            )
            for job in jobs:
                job.status, job.error_code = "cancelled", "SCHEDULE_PAUSED"
                step = session.scalar(
                    select(JobStep).where(
                        JobStep.tenant_id == tenant, JobStep.job_id == job.id, JobStep.number == 1
                    )
                )
                if step is not None:
                    step.status, step.error_code = "cancelled", "SCHEDULE_PAUSED"
                record(session, tenant, identity.user_id, AuditAction.JOB_CANCELLED, job.id)
        record(session, tenant, identity.user_id, AuditAction.SCHEDULE_UPDATED, row.id)
        return represent(row)


def list_own(identity: ActorIdentity, tenant: UUID) -> dict:
    with authorized_transaction(identity, tenant) as (session, _role):
        rows = session.scalars(
            select(SourceSchedule)
            .where(
                SourceSchedule.tenant_id == tenant, SourceSchedule.created_by == identity.user_id
            )
            .order_by(SourceSchedule.created_at, SourceSchedule.id)
            .limit(20)
        )
        return {
            "collector_enabled": get_settings().collector_enabled,
            "schedules": [represent(row) for row in rows],
        }


def dispatch_due() -> int:
    """Opaque discovery only. Workers independently verify every authority."""
    count, now = 0, datetime.now(UTC)
    with Session(get_engine()) as session, session.begin():
        rows = session.scalars(
            select(ScheduleTick)
            .where(ScheduleTick.enabled.is_(True), ScheduleTick.due_at <= now)
            .order_by(ScheduleTick.due_at)
            .limit(50)
            .with_for_update(skip_locked=True)
        )
        for tick in rows:
            get_task_queue().send_task("knowledge_os.source_schedule", args=[str(tick.schedule_id)])
            tick.due_at = now + timedelta(seconds=60)
            count += 1
    return count


def execute_due(value: str) -> None:
    """No HTTP/model operation: create the existing authorized collection job."""
    schedule = UUID(value)
    with Session(get_engine()) as session, session.begin():
        tick = session.get(ScheduleTick, schedule)
        if tick is None or not tick.enabled:
            return
        tenant, user_id = tick.tenant_id, tick.requested_by
        set_user(session, user_id)
        user = session.get(User, user_id)
        # Membership discovery before selecting any tenant context, including the lock.
        member = session.scalar(
            select(TenantMember).where(
                TenantMember.tenant_id == tenant,
                TenantMember.user_id == user_id,
                TenantMember.active.is_(True),
            )
        )
        if user is None or user.status != "active" or member is None:
            tick.enabled = False
            return
        set_tenant(session, tenant)
        try:
            space, role = locked_membership(session, tenant, user_id)
        except HTTPException:
            tick.enabled = False
            return
        session.refresh(user)
        if user.status != "active" or (
            get_settings().email_verification_required and not user.email_verified
        ):
            tick.enabled = False
            return
        row = session.scalar(
            select(SourceSchedule)
            .where(
                SourceSchedule.tenant_id == tenant,
                SourceSchedule.created_by == user_id,
                SourceSchedule.id == schedule,
            )
            .execution_options(populate_existing=True)
        )
        session.refresh(tick)
        if row is None or not row.active or not tick.enabled or row.next_run_at > datetime.now(UTC):
            return
        # Same deterministic occurrence key survives broker loss and duplicate tasks.
        occurrence = uuid5(row.id, row.next_run_at.isoformat())
        access = SpaceAccess(Actor(session, user, ""), space, role)
        try:
            with session.begin_nested():
                result = queue_collection(access, row.source_id, occurrence)
                if session.get(ScheduleJob, (tenant, UUID(result["id"]))) is None:
                    session.add(
                        ScheduleJob(tenant_id=tenant, job_id=UUID(result["id"]), schedule_id=row.id)
                    )
            row.last_error = None
        except HTTPException as error:
            row.last_error = (
                str(error.detail.get("code", "SCHEDULE_UNAVAILABLE"))
                if isinstance(error.detail, dict)
                else "SCHEDULE_UNAVAILABLE"
            )
        # Do not catch up missed intervals or create a burst after downtime.
        row.next_run_at = tick.due_at = datetime.now(UTC) + timedelta(minutes=row.interval_minutes)


def scheduled_job_active(session: Session, job: ProcessingJob) -> bool:
    link = session.get(ScheduleJob, (job.tenant_id, job.id))
    if link is None:
        return True
    row = session.get(SourceSchedule, link.schedule_id)
    return bool(
        row and row.tenant_id == job.tenant_id and row.created_by == job.requested_by and row.active
    )
