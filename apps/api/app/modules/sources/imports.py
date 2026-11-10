"""Authorized local-content imports through the durable independent Worker."""

from typing import Any
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ProcessingJob
from app.db.session import get_engine
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record, record_event
from app.modules.identity.access import SpaceAccess, lock_access
from app.modules.jobs.models import JobDispatch, JobStep
from app.modules.jobs.routes import represent as represent_job
from app.modules.jobs.routes import reserve
from app.modules.sources.collection import source_for
from app.modules.sources.execution import cancelled_job, finish_job
from app.modules.sources.import_content import (
    ImportFormat,
    ImportKind,
    import_text_bytes,
    import_title,
    parse_import_content,
)
from app.modules.sources.models import SourceSnapshot


class ContentImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: UUID
    consent: StrictBool
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=60_000)
    format: ImportFormat
    content_kind: ImportKind

    _title = field_validator("title")(import_title)

    @field_validator("text")
    @classmethod
    def bounded_text(cls, value: str) -> str:
        import_text_bytes(value)
        return value

    @model_validator(mode="after")
    def subtitle_kind(self) -> "ContentImportInput":
        if self.format != "text" and self.content_kind != "transcript":
            raise ValueError("IMPORT_SUBTITLE_KIND_REQUIRED")
        return self


class _ImportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: UUID
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=60_000)
    format: ImportFormat
    content_kind: ImportKind

    _title = field_validator("title")(import_title)


def queue_import(access: SpaceAccess, source_id: UUID, body: ContentImportInput) -> dict[str, Any]:
    if not body.consent:
        raise HTTPException(400, detail={"code": "IMPORT_CONSENT_REQUIRED"})
    # Validate before persistence, and again inside the Worker. No parser failure
    # can be mistaken for a successful snapshot with missing/empty content.
    try:
        parse_import_content(body.text, body.format, body.content_kind)
    except ValueError as error:
        raise HTTPException(422, detail={"code": str(error)}) from None
    lock_access(access)
    session = access.actor.session
    source_for(session, access.tenant.id, source_id)
    payload = {
        "source_id": str(source_id),
        "title": body.title,
        "text": body.text,
        "format": body.format,
        "content_kind": body.content_kind,
    }
    existing = session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.tenant_id == access.tenant.id,
            ProcessingJob.idempotency_key == str(body.idempotency_key),
        )
    )
    if existing:
        if (
            existing.kind != "source_import"
            or existing.requested_by != access.actor.user.id
            or existing.payload != payload
        ):
            raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
        return represent_job(existing)
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
        kind="source_import",
        idempotency_key=str(body.idempotency_key),
        payload=payload,
    )
    session.add(job)
    session.flush()
    session.add(JobStep(tenant_id=job.tenant_id, job_id=job.id, number=1, status="queued"))
    session.add(JobDispatch(tenant_id=job.tenant_id, job_id=job.id, requested_by=job.requested_by))
    record_event(access, AuditAction.JOB_CREATED, job.id)
    return represent_job(job)


def execute_import(dispatch_id: UUID, attempt_number: int) -> None:
    from app.modules.jobs.execution import worker_job

    # All work is bounded CPU/DB, with no external I/O. The shared tenant lock
    # serializes quota/version checks and cancellation/permission changes.
    with Session(get_engine()) as session, session.begin():
        found = worker_job(session, dispatch_id)
        if found is None:
            return
        job, entry = found
        if job.attempt_count != attempt_number or cancelled_job(
            session, job, entry, attempt_number
        ):
            return
        if job.status != "running":
            return
        try:
            if job.kind != "source_import":
                raise ValueError("IMPORT_PAYLOAD_INVALID")
            payload = _ImportPayload.model_validate(job.payload)
            content = parse_import_content(payload.text, payload.format, payload.content_kind)
            source = source_for(session, job.tenant_id, payload.source_id)
        except (ValueError, HTTPException):
            finish_job(session, job, entry, attempt_number, None, "IMPORT_PAYLOAD_INVALID")
            return
        existing = session.scalar(
            select(SourceSnapshot).where(
                SourceSnapshot.tenant_id == job.tenant_id, SourceSnapshot.job_id == job.id
            )
        )
        if existing:
            finish_job(
                session,
                job,
                entry,
                attempt_number,
                {"source_id": str(source.id), "snapshot_id": str(existing.id)},
                None,
            )
            return
        count = (
            session.scalar(
                select(func.count())
                .select_from(SourceSnapshot)
                .where(SourceSnapshot.tenant_id == job.tenant_id)
            )
            or 0
        )
        if count >= 1000:
            finish_job(session, job, entry, attempt_number, None, "SNAPSHOT_QUOTA_EXCEEDED")
            return
        version = (
            session.scalar(
                select(func.max(SourceSnapshot.version)).where(
                    SourceSnapshot.tenant_id == job.tenant_id, SourceSnapshot.source_id == source.id
                )
            )
            or 0
        ) + 1
        limitations = ["USER_PROVIDED_CONTENT", "SOURCE_CONTENT_NOT_VERIFIED"]
        if payload.content_kind == "transcript":
            limitations.append("SUBTITLE_TIMES_NOT_VERIFIED")
        snapshot = SourceSnapshot(
            tenant_id=job.tenant_id,
            source_id=source.id,
            job_id=job.id,
            created_by=job.requested_by,
            version=version,
            provenance="user_import",
            fetched_url=source.canonical_url,
            platform="user_import",
            content_kind=payload.content_kind,
            title=payload.title,
            author="",
            body_text=content.body_text,
            raw_sha256=content.raw_sha256,
            details={
                "import_format": payload.format,
                "input_utf8_bytes": content.utf8_bytes,
                "cue_count": content.cue_count,
            },
            limitations=limitations,
        )
        session.add(snapshot)
        session.flush()
        record(
            session,
            job.tenant_id,
            job.requested_by,
            AuditAction.SOURCE_CONTENT_IMPORTED,
            snapshot.id,
        )
        finish_job(
            session,
            job,
            entry,
            attempt_number,
            {
                "source_id": str(source.id),
                "snapshot_id": str(snapshot.id),
                "version": version,
                "content_kind": snapshot.content_kind,
                "provenance": snapshot.provenance,
                "limitations": limitations,
            },
            None,
        )
