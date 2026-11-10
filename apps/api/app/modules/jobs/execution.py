"""DB-authoritative execution. Queue messages contain only an opaque dispatch UUID."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import MediaAsset, ProcessingJob, Tenant, TenantMember, User
from app.db.session import get_engine
from app.integrations.queue import get_task_queue
from app.modules.identity.access import set_tenant, set_user
from app.modules.jobs.models import JobAttempt, JobDispatch, JobStep


def dispatch_pending() -> int:
    """At-least-once publication; retain due entries until execution completes.

    Crash before DB commit or loss of broker message causes republication after 30s.
    Sending before commit is safe: Worker locks the job and rejects terminal duplicates.
    """
    count = 0
    with Session(get_engine()) as session, session.begin():
        rows = session.scalars(
            select(JobDispatch)
            .where(JobDispatch.done.is_(False), JobDispatch.due_at <= datetime.now(UTC))
            .order_by(JobDispatch.due_at)
            .limit(50)
            .with_for_update(skip_locked=True)
        )
        for entry in rows:
            get_task_queue().send_task("knowledge_os.execute", args=[str(entry.id)])
            entry.due_at = datetime.now(UTC) + timedelta(seconds=30)
            count += 1
    return count


def worker_job(session: Session, dispatch_id: UUID) -> tuple[ProcessingJob, JobDispatch] | None:
    entry = session.get(JobDispatch, dispatch_id)
    if entry is None:
        return None
    # A dispatch record is global *metadata*, never a grant. Verify the actual identity
    # and active membership before choosing tenant context or touching business data.
    set_user(session, entry.requested_by)
    user = session.get(User, entry.requested_by)
    member = session.scalar(
        select(TenantMember).where(
            TenantMember.tenant_id == entry.tenant_id,
            TenantMember.user_id == entry.requested_by,
            TenantMember.active.is_(True),
        )
    )
    tenant = session.get(Tenant, entry.tenant_id) if member else None
    if (
        user is None
        or user.status != "active"
        or tenant is None
        or tenant.status != "active"
        or (get_settings().email_verification_required and not user.email_verified)
    ):
        # Never enter a revoked tenant. Remains inspectable by an operator; no data read.
        entry.done = True
        return None
    set_tenant(session, entry.tenant_id)
    tenant = session.scalar(
        select(Tenant)
        .where(Tenant.id == entry.tenant_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if tenant is None or tenant.status != "active":
        entry.done = True
        return None
    # Lock only AFTER the initial membership check and tenant selection. PostgreSQL
    # SELECT FOR UPDATE also applies UPDATE RLS; pre-context locking hides the row.
    member = session.scalar(
        select(TenantMember)
        .where(
            TenantMember.tenant_id == entry.tenant_id,
            TenantMember.user_id == entry.requested_by,
            TenantMember.active.is_(True),
        )
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if member is None:
        entry.done = True
        return None
    job = session.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.id == entry.job_id, ProcessingJob.tenant_id == entry.tenant_id)
        .with_for_update(key_share=True)
    )
    if job is None or job.requested_by != entry.requested_by:
        entry.done = True
        return None
    return job, entry


def space_export(session: Session, job: ProcessingJob) -> dict[str, Any]:
    tenant = session.get(Tenant, job.tenant_id)
    if tenant is None:
        raise RuntimeError("Space unavailable")
    assets = list(
        session.scalars(
            select(MediaAsset)
            .where(MediaAsset.tenant_id == job.tenant_id)
            .order_by(MediaAsset.id)
            .limit(1001)
        )
    )
    if len(assets) > 1000:
        raise ValueError("Export size limit")
    # This is a real, bounded metadata export, not AI/collection or an object download.
    # Internal storage keys and other users' identities are never exposed.
    return {
        "schema_version": 1,
        "space": {"id": str(tenant.id), "name": tenant.name},
        "assets": [
            {"id": str(asset.id), "mime_type": asset.mime_type, "size": asset.size}
            for asset in assets
        ],
        "exported_at": datetime.now(UTC).isoformat(),
    }


def execute_dispatch(value: str) -> None:
    dispatch_id = UUID(value)
    # Claim in a committed transaction. A killed Worker leaves a bounded lease;
    # dispatch loop republishes and a replacement closes the interrupted attempt.
    with Session(get_engine()) as session, session.begin():
        found = worker_job(session, dispatch_id)
        if found is None:
            return
        job, entry = found
        now = datetime.now(UTC)
        if job.status in ("succeeded", "failed", "cancelled"):
            entry.done = True
            return
        if job.status == "running" and job.lease_until and job.lease_until > now:
            return
        if job.status == "running":
            previous = session.scalar(
                select(JobAttempt).where(
                    JobAttempt.job_id == job.id, JobAttempt.number == job.attempt_count
                )
            )
            if previous:
                previous.status, previous.error_code = "failed", "WORKER_INTERRUPTED"
        if job.attempt_count >= 3:
            job.status, job.error_code, entry.done = "failed", "ATTEMPTS_EXHAUSTED", True
            step = session.scalar(
                select(JobStep).where(JobStep.job_id == job.id, JobStep.number == 1)
            )
            if step:
                step.status, step.error_code = "failed", job.error_code
            return
        job.attempt_count += 1
        attempt_number = job.attempt_count
        job.status, job.progress = "running", 10
        job_kind = job.kind
        job.lease_until = now + timedelta(seconds=90)
        step = session.scalar(select(JobStep).where(JobStep.job_id == job.id, JobStep.number == 1))
        if step:
            step.status, step.error_code = "running", None
        session.add(
            JobAttempt(
                tenant_id=job.tenant_id, job_id=job.id, number=attempt_number, status="running"
            )
        )
    if job_kind == "source_collect":
        from app.modules.sources.execution import execute_collection

        execute_collection(dispatch_id, attempt_number)
        return
    if job_kind == "source_import":
        from app.modules.sources.imports import execute_import

        execute_import(dispatch_id, attempt_number)
        return
    # Recheck authorization/cancellation after claim. Work and result are atomic:
    # replay cannot create a second result. Future external side effects need their
    # own idempotency; this handler only reads DB and stores one bounded result.
    with Session(get_engine()) as session, session.begin():
        found = worker_job(session, dispatch_id)
        if found is None:
            return
        job, entry = found
        attempt = session.scalar(
            select(JobAttempt).where(
                JobAttempt.job_id == job.id, JobAttempt.number == attempt_number
            )
        )
        step = session.scalar(select(JobStep).where(JobStep.job_id == job.id, JobStep.number == 1))
        if job.status == "cancelled":
            if attempt:
                attempt.status = "cancelled"
            entry.done = True
            return
        if job.status != "running" or job.attempt_count != attempt_number:
            return
        try:
            if job.kind != "space_export":
                raise ValueError("Unsupported job kind")
            result = space_export(session, job)
        except Exception:
            # No provider/DB exception text is persisted or returned to users.
            job.status, job.error_code = "failed", "EXECUTION_FAILED"
            if attempt:
                attempt.status, attempt.error_code = "failed", job.error_code
            if step:
                step.status, step.error_code = "failed", job.error_code
        else:
            job.result, job.status, job.progress, job.error_code = result, "succeeded", 100, None
            if attempt:
                attempt.status = "succeeded"
            if step:
                step.status, step.error_code = "succeeded", None
        job.lease_until, entry.done = None, True
