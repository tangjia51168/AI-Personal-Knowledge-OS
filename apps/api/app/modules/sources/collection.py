"""Authorized collection jobs and immutable snapshot consumers."""

from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import ProcessingJob
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record_event
from app.modules.identity.access import SpaceAccess, lock_access
from app.modules.jobs.models import JobDispatch, JobStep
from app.modules.jobs.routes import represent as represent_job
from app.modules.jobs.routes import reserve
from app.modules.sources.models import Source, SourceSnapshot


def source_for(session: Session, tenant_id: UUID, source_id: UUID) -> Source:
    source = session.scalar(
        select(Source).where(Source.tenant_id == tenant_id, Source.id == source_id)
    )
    if source is None or source.status != "accepted" or not source.canonical_url:
        raise HTTPException(404, detail={"code": "SOURCE_NOT_FOUND"})
    return source


def queue_collection(access: SpaceAccess, source_id: UUID, key: UUID) -> dict[str, Any]:
    lock_access(access)
    session = access.actor.session
    source_for(session, access.tenant.id, source_id)
    existing = session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.tenant_id == access.tenant.id, ProcessingJob.idempotency_key == str(key)
        )
    )
    if existing:
        if (
            existing.kind != "source_collect"
            or existing.requested_by != access.actor.user.id
            or existing.payload != {"source_id": str(source_id)}
        ):
            raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
        return represent_job(existing)
    if not get_settings().collector_enabled:
        raise HTTPException(409, detail={"code": "COLLECTOR_DISABLED"})
    reserve(access)
    count = (
        session.scalar(
            select(func.count())
            .select_from(SourceSnapshot)
            .where(SourceSnapshot.tenant_id == access.tenant.id)
        )
        or 0
    )
    if count >= 1000:
        raise HTTPException(429, detail={"code": "SNAPSHOT_QUOTA_EXCEEDED"})
    job = ProcessingJob(
        tenant_id=access.tenant.id,
        requested_by=access.actor.user.id,
        kind="source_collect",
        idempotency_key=str(key),
        payload={"source_id": str(source_id)},
    )
    session.add(job)
    session.flush()
    session.add(JobStep(tenant_id=job.tenant_id, job_id=job.id, number=1, status="queued"))
    session.add(JobDispatch(tenant_id=job.tenant_id, job_id=job.id, requested_by=job.requested_by))
    record_event(access, AuditAction.JOB_CREATED, job.id)
    return represent_job(job)


def represent_snapshot(row: SourceSnapshot, *, include_body: bool = False) -> dict[str, Any]:
    result = {
        "id": str(row.id),
        "source_id": str(row.source_id),
        "job_id": str(row.job_id),
        "version": row.version,
        "provenance": row.provenance,
        "fetched_url": row.fetched_url,
        "platform": row.platform,
        "content_kind": row.content_kind,
        "title": row.title,
        "author": row.author,
        "raw_sha256": row.raw_sha256,
        "limitations": row.limitations,
        "created_at": row.created_at.isoformat(),
    }
    if include_body:
        result.update(body_text=row.body_text, details=row.details)
    return result
