"""Append-only human concept cards; evidence binding is not factual verification."""

from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.identity.access import ActorIdentity, authorized_transaction, recheck_identity
from app.modules.notes.annotations import require_revision
from app.modules.notes.models import EvidenceItem
from app.modules.organization.concept_content import ConceptInput
from app.modules.organization.models import KnowledgeConcept

MAX_REVISION_CONCEPTS = 20
MAX_SPACE_CONCEPTS = 1000


def concept_data(row: KnowledgeConcept, quote: str) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "note_id": str(row.note_id),
        "revision_id": str(row.revision_id),
        "snapshot_id": str(row.snapshot_id),
        "name": row.name,
        "definition": row.definition,
        "evidence_key": row.evidence_key,
        "quote": quote,
        "method": "manual",
        "confidence": None,
        "created_by": str(row.created_by),
        "created_at": row.created_at.isoformat(),
    }


def evidence_for(
    session: Session, tenant: UUID, revision: UUID, snapshot: UUID, key: str
) -> EvidenceItem:
    row = session.scalar(
        select(EvidenceItem).where(
            EvidenceItem.tenant_id == tenant,
            EvidenceItem.revision_id == revision,
            EvidenceItem.snapshot_id == snapshot,
            EvidenceItem.evidence_key == key,
        )
    )
    if row is None:
        raise HTTPException(409, detail={"code": "CONCEPT_EVIDENCE_NOT_FOUND"})
    return row


def create_concept(
    identity: ActorIdentity, tenant: UUID, note: UUID, revision: UUID, body: ConceptInput
) -> dict[str, Any]:
    request_hash = body.digest(note, revision)
    with authorized_transaction(identity, tenant) as (session, _role):
        recheck_identity(session, identity)
        source = require_revision(session, tenant, note, revision)
        existing = session.scalar(
            select(KnowledgeConcept).where(
                KnowledgeConcept.tenant_id == tenant,
                KnowledgeConcept.idempotency_key == body.idempotency_key,
            )
        )
        if existing is not None:
            if existing.created_by != identity.user_id or existing.request_sha256 != request_hash:
                raise HTTPException(409, detail={"code": "CONCEPT_IDEMPOTENCY_CONFLICT"})
            quote = evidence_for(
                session, tenant, revision, source.snapshot_id, existing.evidence_key
            ).quote
            return concept_data(existing, quote)
        evidence = evidence_for(session, tenant, revision, source.snapshot_id, body.evidence_key)
        if evidence.quote != body.expected_quote:
            raise HTTPException(409, detail={"code": "CONCEPT_QUOTE_MISMATCH"})
        for scope, maximum in ((False, MAX_SPACE_CONCEPTS), (True, MAX_REVISION_CONCEPTS)):
            statement = (
                select(func.count())
                .select_from(KnowledgeConcept)
                .where(KnowledgeConcept.tenant_id == tenant)
            )
            if scope:
                statement = statement.where(KnowledgeConcept.revision_id == revision)
            if (session.scalar(statement) or 0) >= maximum:
                raise HTTPException(429, detail={"code": "CONCEPT_QUOTA_EXCEEDED"})
        row = KnowledgeConcept(
            id=uuid4(),
            tenant_id=tenant,
            note_id=note,
            snapshot_id=source.snapshot_id,
            revision_id=revision,
            created_by=identity.user_id,
            idempotency_key=body.idempotency_key,
            request_sha256=request_hash,
            name=body.name,
            definition=body.definition,
            evidence_key=body.evidence_key,
        )
        session.add(row)
        session.flush()
        record(session, tenant, identity.user_id, AuditAction.CONCEPT_CREATED, row.id)
        return concept_data(row, evidence.quote)


def revision_concepts(session: Session, tenant: UUID, note: UUID, revision: UUID) -> list[dict]:
    require_revision(session, tenant, note, revision)
    rows = session.execute(
        select(KnowledgeConcept, EvidenceItem.quote)
        .join(
            EvidenceItem,
            (EvidenceItem.tenant_id == KnowledgeConcept.tenant_id)
            & (EvidenceItem.revision_id == KnowledgeConcept.revision_id)
            & (EvidenceItem.snapshot_id == KnowledgeConcept.snapshot_id)
            & (EvidenceItem.evidence_key == KnowledgeConcept.evidence_key),
        )
        .where(
            KnowledgeConcept.tenant_id == tenant,
            KnowledgeConcept.note_id == note,
            KnowledgeConcept.revision_id == revision,
        )
        .order_by(KnowledgeConcept.created_at, KnowledgeConcept.id)
        .limit(MAX_REVISION_CONCEPTS + 1)
    ).all()
    if len(rows) > MAX_REVISION_CONCEPTS:
        raise HTTPException(409, detail={"code": "CONCEPT_INVENTORY_LIMIT"})
    return [concept_data(row, quote) for row, quote in rows]
