"""Explicit lexical RAG with durable idempotency and no uncertain-outcome retry."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select

from app.db.models import Tenant, User
from app.integrations.ai import ProviderError
from app.integrations.summary import DeepSeekSummaryProvider
from app.modules.ai.service import finish_attempt, provider_choices, reserve_attempt
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.identity.access import (
    Actor,
    ActorIdentity,
    SpaceAccess,
    authorized_transaction,
)
from app.modules.notes.exports import export_data
from app.modules.notes.models import ResearchCitation, ResearchRun
from app.modules.notes.research_content import (
    ResearchInput,
    answer_prompt,
    fingerprint,
    parse_answer,
)
from app.modules.notes.search import SearchQuery, search_page


def represent(run: ResearchRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "question": run.question,
        "query": run.query,
        "status": run.status,
        "answer": run.answer,
        "error_code": run.error_code,
        "citations": [
            {
                "id": row.key,
                "note_id": str(row.note_id),
                "revision_id": str(row.revision_id),
                "snapshot_id": str(row.snapshot_id),
                "evidence_key": row.evidence_key,
                "version": row.version,
                "title": row.title,
                "provenance": row.provenance,
                "content_kind": row.content_kind,
                "quote": row.quote,
            }
            for row in run.citations
        ],
        "retrieval": "literal_latest_revisions",
    }


def own_run(session: Any, tenant: UUID, actor: UUID, run_id: UUID) -> ResearchRun:
    row = session.scalar(
        select(ResearchRun).where(
            ResearchRun.tenant_id == tenant,
            ResearchRun.created_by == actor,
            ResearchRun.id == run_id,
        )
    )
    if row is None:
        raise HTTPException(404, detail={"code": "RESEARCH_NOT_FOUND"})
    return row


def read_history(identity: ActorIdentity, tenant: UUID, run_id: UUID | None = None) -> dict | list:
    with authorized_transaction(identity, tenant) as (session, _role):
        if run_id is not None:
            return represent(own_run(session, tenant, identity.user_id, run_id))
        return [
            represent(row)
            for row in session.scalars(
                select(ResearchRun)
                .where(ResearchRun.tenant_id == tenant, ResearchRun.created_by == identity.user_id)
                .order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc())
                .limit(20)
            )
        ]


def begin(identity: ActorIdentity, tenant: UUID, body: ResearchInput) -> tuple[dict, bool]:
    digest = fingerprint(body)
    with authorized_transaction(identity, tenant) as (session, role):
        if role not in ("owner", "admin"):
            raise HTTPException(403, detail={"code": "FORBIDDEN"})
        previous = session.scalar(
            select(ResearchRun).where(
                ResearchRun.tenant_id == tenant,
                ResearchRun.idempotency_key == body.idempotency_key,
            )
        )
        if previous is not None:
            if previous.created_by != identity.user_id or previous.request_sha256 != digest:
                raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
            return represent(previous), False
        unresolved = session.scalar(
            select(ResearchRun.id)
            .where(
                ResearchRun.tenant_id == tenant,
                ResearchRun.created_by == identity.user_id,
                ResearchRun.status.in_(["pending", "unknown"]),
            )
            .limit(1)
        )
        if unresolved is not None:
            raise HTTPException(409, detail={"code": "RESEARCH_OUTCOME_UNRESOLVED"})
        count = session.scalar(
            select(func.count())
            .select_from(ResearchRun)
            .where(
                ResearchRun.tenant_id == tenant,
                ResearchRun.created_at
                >= datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0),
            )
        )
        if (count or 0) >= 20:
            raise HTTPException(429, detail={"code": "RESEARCH_DAILY_QUOTA"})
        # Reuse member/tenant-bound latest-revision search and exact export joins.
        # Actor comes only from the already revalidated session, not request fields.
        user = session.get(User, identity.user_id)
        space = session.get(Tenant, tenant)
        if user is None or space is None:
            raise HTTPException(404, detail={"code": "SPACE_NOT_FOUND"})
        access = SpaceAccess(Actor(session, user, identity.token_hash), space, role)
        hits = search_page(access, SearchQuery(q=body.query, scope="all", limit=8))["data"]
        citations: list[dict] = []
        for hit in hits:
            data = export_data(access, UUID(hit["note_id"]), UUID(hit["revision_id"]))
            for evidence in data["evidence"][:2]:
                citations.append(
                    {
                        "id": f"R{len(citations) + 1:03d}",
                        "note_id": data["note_id"],
                        "revision_id": data["revision"]["id"],
                        "version": data["revision"]["version"],
                        "snapshot_id": data["snapshot"]["id"],
                        "title": data["revision"]["title"],
                        "provenance": data["snapshot"]["provenance"],
                        "content_kind": data["snapshot"]["content_kind"],
                        "evidence_key": evidence["key"],
                        "quote": evidence["quote"],
                    }
                )
                if len(citations) == 8:
                    break
            if len(citations) == 8:
                break
        answer_prompt(body, citations)  # Validate full bound before creating a charge claim.
        run = ResearchRun(
            tenant_id=tenant,
            created_by=identity.user_id,
            idempotency_key=body.idempotency_key,
            request_sha256=digest,
            question=body.question,
            query=body.query,
            status="pending",
        )
        session.add(run)
        session.flush()
        for citation in citations:
            session.add(
                ResearchCitation(
                    tenant_id=tenant,
                    run_id=run.id,
                    key=citation["id"],
                    note_id=UUID(citation["note_id"]),
                    revision_id=UUID(citation["revision_id"]),
                    snapshot_id=UUID(citation["snapshot_id"]),
                    evidence_key=citation["evidence_key"],
                    version=citation["version"],
                    title=citation["title"],
                    provenance=citation["provenance"],
                    content_kind=citation["content_kind"],
                    quote=citation["quote"],
                )
            )
        session.flush()
        session.expire(run, ["citations"])
        record(session, tenant, identity.user_id, AuditAction.RESEARCH_CREATED, run.id)
        return represent(run), True


def finish(
    identity: ActorIdentity,
    tenant: UUID,
    run_id: UUID,
    status: str,
    answer: dict | None = None,
    code: str | None = None,
) -> dict:
    with authorized_transaction(identity, tenant) as (session, role):
        if role not in ("owner", "admin"):
            raise HTTPException(403, detail={"code": "FORBIDDEN"})
        run = own_run(session, tenant, identity.user_id, run_id)
        if run.status == "pending":
            run.status, run.answer, run.error_code = status, answer, code
            session.flush()
            record(session, tenant, identity.user_id, AuditAction.RESEARCH_FINISHED, run.id)
        return represent(run)


async def generate(identity: ActorIdentity, tenant: UUID, body: ResearchInput) -> dict:
    run, fresh = await run_in_threadpool(begin, identity, tenant, body)
    if not fresh:
        return run
    run_id = UUID(run["id"])
    if not run["citations"]:
        return await run_in_threadpool(
            finish,
            identity,
            tenant,
            run_id,
            "succeeded",
            {
                "insufficient_evidence": True,
                "statements": [],
                "limitations": ["关键词没有找到可引用的已保存证据；未调用模型。"],
            },
        )
    submitted = False
    try:
        choices = await run_in_threadpool(provider_choices, identity, tenant)
        if choices[0][1] != "deepseek":
            return await run_in_threadpool(
                finish, identity, tenant, run_id, "failed", None, "RESEARCH_PROVIDER_UNSUPPORTED"
            )
        provider_id, kind = choices[0]
        usage_id = uuid4()
        config = await run_in_threadpool(
            reserve_attempt, identity, tenant, provider_id, usage_id, kind
        )
        if config is None:
            return await run_in_threadpool(
                finish, identity, tenant, run_id, "failed", None, "NO_AUTHORIZED_PROVIDER"
            )
        try:
            submitted = True
            result = await DeepSeekSummaryProvider(config.model, config.api_key).generate(
                answer_prompt(body, run["citations"])
            )
        except ProviderError as error:
            await run_in_threadpool(
                finish_attempt, identity, tenant, usage_id, None, error.code, error.outcome_unknown
            )
            return await run_in_threadpool(
                finish,
                identity,
                tenant,
                run_id,
                "unknown" if error.outcome_unknown else "failed",
                None,
                "MODEL_OUTCOME_UNKNOWN" if error.outcome_unknown else error.code,
            )
        await run_in_threadpool(finish_attempt, identity, tenant, usage_id, result, None, False)
        try:
            answer = parse_answer(result.text, run["citations"])
        except ValueError:
            return await run_in_threadpool(
                finish, identity, tenant, run_id, "failed", None, "RESEARCH_VALIDATION_FAILED"
            )
        return await run_in_threadpool(finish, identity, tenant, run_id, "succeeded", answer)
    except HTTPException as error:
        if error.status_code in (401, 403, 404):
            raise
        return await run_in_threadpool(
            finish,
            identity,
            tenant,
            run_id,
            "unknown" if submitted else "failed",
            None,
            "MODEL_OUTCOME_UNKNOWN" if submitted else "RESEARCH_UNAVAILABLE",
        )
