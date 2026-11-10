"""Bounded DB-only intake; no collector or network side effects."""

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select

from app.integrations.url_safety import MAX_CANDIDATES, extract_urls
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record_event
from app.modules.identity.access import SpaceAccess, lock_access
from app.modules.sources.models import Source, SourceBatch

MAX_DAILY_BATCHES = 100
MAX_SPACE_SOURCES = 10_000


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def represent(source: Source) -> dict[str, Any]:
    return {
        "id": str(source.id),
        "batch_id": str(source.batch_id),
        "original_url": source.original_url,
        "canonical_url": source.canonical_url,
        "status": source.status,
        "rejection_code": source.rejection_code,
        "created_at": source.created_at.isoformat(),
    }


def batch_result(access: SpaceAccess, batch: SourceBatch) -> dict[str, Any]:
    rows = access.actor.session.scalars(
        select(Source)
        .where(Source.tenant_id == access.tenant.id, Source.batch_id == batch.id)
        .order_by(Source.id)
    )
    return {
        "batch_id": str(batch.id),
        "accepted_count": batch.accepted_count,
        "rejected_count": batch.rejected_count,
        "sources": [represent(row) for row in rows],
    }


def save_batch(access: SpaceAccess, text: str, idempotency_key: UUID) -> dict[str, Any]:
    # This tenant lock uses NO KEY UPDATE and serializes writers before all quota,
    # replay and duplicate reads. No external I/O is performed while it is held.
    lock_access(access)
    session = access.actor.session
    input_sha256 = digest(text)
    existing = session.scalar(
        select(SourceBatch).where(
            SourceBatch.tenant_id == access.tenant.id,
            SourceBatch.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.created_by != access.actor.user.id or existing.input_sha256 != input_sha256:
            raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
        return batch_result(access, existing)

    # Read one beyond the limit and reject instead of silently dropping input.
    candidates = extract_urls(text, limit=MAX_CANDIDATES + 1, preserve_variants=True)
    if not candidates:
        raise HTTPException(422, detail={"code": "NO_SOURCE_URLS"})
    if len(candidates) > MAX_CANDIDATES:
        raise HTTPException(422, detail={"code": "SOURCE_CANDIDATE_LIMIT"})
    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    daily = (
        session.scalar(
            select(func.count())
            .select_from(SourceBatch)
            .where(SourceBatch.tenant_id == access.tenant.id, SourceBatch.created_at >= day_start)
        )
        or 0
    )
    total = (
        session.scalar(
            select(func.count()).select_from(Source).where(Source.tenant_id == access.tenant.id)
        )
        or 0
    )
    if daily >= MAX_DAILY_BATCHES or total + len(candidates) > MAX_SPACE_SOURCES:
        raise HTTPException(429, detail={"code": "SOURCE_BATCH_QUOTA_EXCEEDED"})

    batch_id = uuid4()
    rows = []
    seen: set[str] = set()
    for item in candidates:
        canonical = item.canonical_url
        canonical_hash = digest(canonical) if canonical is not None else None
        rejection = item.reason
        if canonical is not None:
            previous = session.scalar(
                select(Source).where(
                    Source.tenant_id == access.tenant.id, Source.canonical_sha256 == canonical_hash
                )
            )
            if previous is not None and previous.canonical_url != canonical:
                raise HTTPException(409, detail={"code": "SOURCE_IDENTITY_CONFLICT"})
            if previous is not None or canonical in seen:
                rejection = "DUPLICATE_CANONICAL_URL"
            seen.add(canonical)
        accepted = canonical is not None and rejection is None
        rows.append(
            Source(
                id=uuid4(),
                tenant_id=access.tenant.id,
                batch_id=batch_id,
                # Rejects can contain embedded passwords or oversized input; keep only
                # their closed reason. Valid original URLs remain exact and immutable.
                original_url=item.original_url if item.accepted else "[blocked]",
                canonical_url=canonical if accepted else None,
                canonical_sha256=canonical_hash if accepted else None,
                status="accepted" if accepted else "rejected",
                rejection_code=None if accepted else rejection,
            )
        )
    batch = SourceBatch(
        id=batch_id,
        tenant_id=access.tenant.id,
        created_by=access.actor.user.id,
        idempotency_key=idempotency_key,
        input_sha256=input_sha256,
        accepted_count=sum(row.status == "accepted" for row in rows),
        rejected_count=sum(row.status == "rejected" for row in rows),
    )
    session.add(batch)
    session.flush()  # Persist parent first; no implicit ORM relationship ordering.
    session.add_all(rows)
    record_event(access, AuditAction.SOURCE_BATCH_CREATED, batch_id)
    session.flush()
    return batch_result(access, batch)
