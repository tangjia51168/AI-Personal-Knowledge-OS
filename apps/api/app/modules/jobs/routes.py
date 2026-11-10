from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.db.models import ProcessingJob, Tenant
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record_event
from app.modules.identity.access import SpaceAccess, lock_access, member_access
from app.modules.jobs.models import JobAttempt, JobDispatch, JobStep

router = APIRouter(prefix="/api/v1/spaces/{tenant_id}/jobs", tags=["jobs"])
Access = Annotated[SpaceAccess, Depends(member_access)]


class JobInput(BaseModel):
    kind: Literal["space_export"] = "space_export"
    idempotency_key: str = Field(min_length=16, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")


def represent(job: ProcessingJob) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "kind": job.kind,
        "status": job.status,
        "progress": job.progress,
        "attempt_count": job.attempt_count,
        "error_code": job.error_code,
        "created_at": job.created_at.isoformat(),
    }


def owned_job(access: SpaceAccess, job_id: UUID) -> ProcessingJob:
    job = access.actor.session.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.tenant_id == access.tenant.id, ProcessingJob.id == job_id)
        .with_for_update(key_share=True)
    )
    # Jobs/results are private to requester, even within a shared space.
    if job is None or job.requested_by != access.actor.user.id:
        raise HTTPException(404, detail={"code": "JOB_NOT_FOUND"})
    return job


def reserve(access: SpaceAccess) -> None:
    session = access.actor.session
    session.execute(
        select(Tenant).where(Tenant.id == access.tenant.id).with_for_update(key_share=True)
    )
    active = session.scalar(
        select(func.count())
        .select_from(ProcessingJob)
        .where(
            ProcessingJob.tenant_id == access.tenant.id,
            ProcessingJob.status.in_(["queued", "running"]),
        )
    )
    daily = session.scalar(
        select(func.count())
        .select_from(ProcessingJob)
        .where(
            ProcessingJob.tenant_id == access.tenant.id,
            ProcessingJob.created_at
            >= datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0),
        )
    )
    if (active or 0) >= 10 or (daily or 0) >= 100:
        raise HTTPException(429, detail={"code": "JOB_QUOTA_EXCEEDED"})


@router.post("", status_code=202)
def create_job(body: JobInput, access: Access) -> dict[str, Any]:
    lock_access(access)
    # Serialize quota reservation and idempotency lookup in this tenant transaction.
    access.actor.session.execute(
        select(Tenant).where(Tenant.id == access.tenant.id).with_for_update(key_share=True)
    )
    existing = access.actor.session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.tenant_id == access.tenant.id,
            ProcessingJob.idempotency_key == body.idempotency_key,
        )
    )
    if existing:
        if existing.requested_by != access.actor.user.id or existing.kind != body.kind:
            raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
        return {"data": represent(existing)}
    reserve(access)
    job = ProcessingJob(
        tenant_id=access.tenant.id,
        requested_by=access.actor.user.id,
        kind=body.kind,
        idempotency_key=body.idempotency_key,
    )
    access.actor.session.add(job)
    access.actor.session.flush()
    access.actor.session.add(
        JobStep(tenant_id=job.tenant_id, job_id=job.id, number=1, status="queued")
    )
    access.actor.session.add(
        JobDispatch(tenant_id=job.tenant_id, job_id=job.id, requested_by=job.requested_by)
    )
    record_event(access, AuditAction.JOB_CREATED, job.id)
    return {"data": represent(job)}


@router.get("")
def list_jobs(access: Access) -> dict[str, Any]:
    rows = access.actor.session.scalars(
        select(ProcessingJob)
        .where(
            ProcessingJob.tenant_id == access.tenant.id,
            ProcessingJob.requested_by == access.actor.user.id,
        )
        .order_by(ProcessingJob.created_at.desc())
        .limit(50)
    )
    return {"data": [represent(job) for job in rows]}


@router.get("/{job_id}")
def get_job(job_id: UUID, access: Access) -> dict[str, Any]:
    job = owned_job(access, job_id)
    result = represent(job)
    records: list[tuple[str, Any]] = [("steps", JobStep), ("attempts", JobAttempt)]
    for key, model in records:
        rows: Any = access.actor.session.scalars(
            select(model)
            .where(model.tenant_id == job.tenant_id, model.job_id == job.id)
            .order_by(model.number)
        )
        result[key] = [
            {"number": row.number, "status": row.status, "error_code": row.error_code}
            for row in rows
        ]
    return {"data": result}


@router.get("/{job_id}/result")
def get_result(job_id: UUID, access: Access) -> dict[str, Any]:
    job = owned_job(access, job_id)
    if job.status != "succeeded":
        raise HTTPException(409, detail={"code": "RESULT_NOT_READY"})
    return {"data": job.result}


@router.post("/{job_id}/cancel")
def cancel_job(job_id: UUID, access: Access) -> dict[str, Any]:
    lock_access(access)
    job = owned_job(access, job_id)
    if job.status in ("queued", "running"):
        job.status, job.error_code = "cancelled", None
        step = access.actor.session.scalar(select(JobStep).where(JobStep.job_id == job.id))
        if step:
            step.status = "cancelled"
        record_event(access, AuditAction.JOB_CANCELLED, job.id)
    return {"data": represent(job)}


@router.post("/{job_id}/retry", status_code=202)
def retry_job(job_id: UUID, access: Access) -> dict[str, Any]:
    lock_access(access)
    # Same lock order as create: tenant -> job, preventing quota deadlocks.
    reserve(access)
    job = owned_job(access, job_id)
    if job.status != "failed" or job.attempt_count >= 3:
        raise HTTPException(409, detail={"code": "JOB_NOT_RETRYABLE"})
    job.status, job.error_code, job.progress, job.lease_until = "queued", None, 0, None
    dispatch = access.actor.session.scalar(select(JobDispatch).where(JobDispatch.job_id == job.id))
    if dispatch:
        dispatch.done, dispatch.due_at = False, datetime.now(UTC)
    record_event(access, AuditAction.JOB_RETRIED, job.id)
    return {"data": represent(job)}
