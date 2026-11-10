"""Collect outside authorization transactions, then revalidate before committing."""

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import ProcessingJob
from app.db.session import get_engine
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.jobs.models import JobAttempt, JobDispatch, JobStep
from app.modules.sources.collection import source_for
from app.modules.sources.models import SourceSnapshot
from app.modules.sources.schedules import scheduled_job_active


def finish_job(
    session: Session,
    job: ProcessingJob,
    entry: JobDispatch,
    attempt_number: int,
    result: dict[str, Any] | None,
    code: str | None,
) -> None:
    attempt = session.scalar(
        select(JobAttempt).where(
            JobAttempt.tenant_id == job.tenant_id,
            JobAttempt.job_id == job.id,
            JobAttempt.number == attempt_number,
        )
    )
    step = session.scalar(
        select(JobStep).where(
            JobStep.tenant_id == job.tenant_id, JobStep.job_id == job.id, JobStep.number == 1
        )
    )
    status = "succeeded" if result is not None else "failed"
    job.status, job.error_code, job.result = status, code, result
    job.progress = 100 if result is not None else job.progress
    job.lease_until, entry.done = None, True
    if attempt:
        attempt.status, attempt.error_code = status, code
    if step:
        step.status, step.error_code = status, code


def cancelled_job(
    session: Session, job: ProcessingJob, entry: JobDispatch, attempt_number: int
) -> bool:
    if job.status != "cancelled":
        return False
    attempt = session.scalar(
        select(JobAttempt).where(
            JobAttempt.tenant_id == job.tenant_id,
            JobAttempt.job_id == job.id,
            JobAttempt.number == attempt_number,
        )
    )
    if attempt:
        attempt.status, attempt.error_code = "cancelled", job.error_code
    job.lease_until, entry.done = None, True
    return True


def execute_collection(dispatch_id: UUID, attempt_number: int) -> None:
    from app.integrations.platforms import CollectionError, collect
    from app.modules.jobs.execution import worker_job

    code, collected = None, None
    try:
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
            if not scheduled_job_active(session, job):
                job.status, job.error_code = "cancelled", "SCHEDULE_PAUSED"
                cancelled_job(session, job, entry, attempt_number)
                return
            if job.kind != "source_collect" or not get_settings().collector_enabled:
                raise CollectionError("COLLECTOR_DISABLED")
            source = source_for(session, job.tenant_id, UUID(job.payload["source_id"]))
            url = source.canonical_url
            assert url is not None
        # No database transaction, lock or secrets cross this network boundary.
        collected = collect(url)
    except CollectionError as error:
        code = error.code
    except Exception:
        code = "COLLECTION_FAILED"
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
        if not scheduled_job_active(session, job):
            job.status, job.error_code = "cancelled", "SCHEDULE_PAUSED"
            cancelled_job(session, job, entry, attempt_number)
            return
        if collected is None:
            finish_job(session, job, entry, attempt_number, None, code)
            return
        source = source_for(session, job.tenant_id, UUID(job.payload["source_id"]))
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
        # Compare the latest version, not any historical match: A -> B -> A
        # must preserve that final change. Metadata and limitations are content.
        latest = session.scalar(
            select(SourceSnapshot)
            .where(
                SourceSnapshot.tenant_id == job.tenant_id,
                SourceSnapshot.source_id == source.id,
            )
            .order_by(SourceSnapshot.version.desc())
            .limit(1)
        )
        if latest is not None and (
            latest.provenance == "platform_collection"
            and latest.raw_sha256 == collected.raw_sha256
            and latest.body_text == collected.text
            and latest.content_kind == collected.content_kind
            and latest.title == collected.title
            and latest.author == (collected.author or "")
            and latest.platform == collected.platform
            and latest.fetched_url == collected.final_url
            and latest.details == collected.metadata
            and latest.limitations == list(collected.limitations)
        ):
            finish_job(
                session,
                job,
                entry,
                attempt_number,
                {"source_id": str(source.id), "snapshot_id": str(latest.id), "unchanged": True},
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
        snapshot = SourceSnapshot(
            tenant_id=job.tenant_id,
            source_id=source.id,
            job_id=job.id,
            created_by=job.requested_by,
            version=version,
            provenance="platform_collection",
            fetched_url=collected.final_url,
            platform=collected.platform,
            content_kind=collected.content_kind,
            title=collected.title,
            author=collected.author or "",
            body_text=collected.text,
            raw_sha256=collected.raw_sha256,
            details=collected.metadata,
            limitations=list(collected.limitations),
        )
        session.add(snapshot)
        session.flush()
        record(
            session,
            job.tenant_id,
            job.requested_by,
            AuditAction.SOURCE_SNAPSHOT_CREATED,
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
                "limitations": snapshot.limitations,
            },
            None,
        )
