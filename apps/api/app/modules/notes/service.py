"""Explicit, idempotent AI generation with durable unknown-outcome protection."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError
from sqlalchemy import func, select

from app.integrations.ai import ProviderError
from app.integrations.summary import DeepSeekSummaryProvider
from app.modules.ai.service import finish_attempt, provider_choices, reserve_attempt
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.identity.access import ActorIdentity, authorized_transaction
from app.modules.notes import checkpoints
from app.modules.notes.content import (
    SummaryDocument,
    build_evidence,
    build_prompt,
    document_items,
    parse_summary,
    render_markdown,
)
from app.modules.notes.long_content import (
    batch_prompt,
    batches,
    final_document,
    parse_batch,
    synthesis_prompt,
)
from app.modules.notes.models import EvidenceItem, Note, NoteRevision, NoteRun, NoteSection
from app.modules.sources.models import SourceSnapshot


def run_result(session: Any, run: NoteRun) -> dict[str, Any]:
    revision = session.scalar(
        select(NoteRevision).where(
            NoteRevision.tenant_id == run.tenant_id, NoteRevision.run_id == run.id
        )
    )
    return {
        "run_id": str(run.id),
        "status": run.status,
        "error_code": run.error_code,
        "note_id": str(revision.note_id) if revision else None,
        "revision_id": str(revision.id) if revision else None,
        **checkpoints.progress(session, run),
    }


def read_run(identity: ActorIdentity, tenant_id: UUID, run_id: UUID) -> dict[str, Any]:
    with authorized_transaction(identity, tenant_id) as (session, _role):
        run = session.get(NoteRun, run_id)
        if run is None or run.created_by != identity.user_id:
            raise HTTPException(404, detail={"code": "SUMMARY_RUN_NOT_FOUND"})
        return run_result(session, run)


def snapshot_scope(
    document: SummaryDocument, content_kind: str, provenance: str = "platform_collection"
) -> SummaryDocument:
    """The source scope is a server fact, never delegated to model compliance."""

    if provenance == "user_import":
        limitation = (
            "本笔记依据用户提供的导入内容，未验证其与关联平台或视频一致；"
            "如含字幕时间，时间亦未对照音视频核验。"
        )
    elif content_kind == "metadata":
        limitation = (
            "本笔记仅依据已取得的标题、作者、简介等元数据；"
            "未取得视频字幕或音视频，不是视频内容总结。"
        )
    else:
        return document
    values = document.model_dump()
    prefix = "用户导入笔记：" if provenance == "user_import" else "元数据笔记："
    values["title"] = (
        document.title if document.title.startswith(prefix) else prefix + document.title
    )[:200]
    values["limitations"] = [
        limitation,
        *[item for item in document.limitations if item != limitation],
    ][:8]
    return type(document).model_validate(values)


def begin_run(
    identity: ActorIdentity, tenant_id: UUID, snapshot_id: UUID, key: UUID
) -> tuple[UUID, bool]:
    with authorized_transaction(identity, tenant_id) as (session, role):
        if role not in ("owner", "admin"):
            raise HTTPException(403, detail={"code": "FORBIDDEN"})
        existing = session.scalar(
            select(NoteRun).where(NoteRun.tenant_id == tenant_id, NoteRun.idempotency_key == key)
        )
        if existing:
            if existing.snapshot_id != snapshot_id or existing.created_by != identity.user_id:
                raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
            return existing.id, False
        snapshot = session.scalar(
            select(SourceSnapshot).where(
                SourceSnapshot.tenant_id == tenant_id, SourceSnapshot.id == snapshot_id
            )
        )
        if snapshot is None:
            raise HTTPException(404, detail={"code": "SNAPSHOT_NOT_FOUND"})
        daily_runs = (
            session.scalar(
                select(func.count())
                .select_from(NoteRun)
                .where(
                    NoteRun.tenant_id == tenant_id,
                    NoteRun.created_at
                    >= datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0),
                )
            )
            or 0
        )
        if daily_runs >= 20:
            raise HTTPException(429, detail={"code": "SUMMARY_DAILY_QUOTA"})
        total = (
            session.scalar(
                select(func.count())
                .select_from(NoteRevision)
                .where(NoteRevision.tenant_id == tenant_id)
            )
            or 0
        )
        if total >= 1000:
            raise HTTPException(429, detail={"code": "NOTE_QUOTA_EXCEEDED"})
        previous = session.scalar(
            select(NoteRun).where(
                NoteRun.tenant_id == tenant_id,
                NoteRun.snapshot_id == snapshot_id,
                NoteRun.created_by == identity.user_id,
                NoteRun.status.in_(["pending", "unknown"]),
            )
        )
        if previous:
            raise HTTPException(409, detail={"code": "SUMMARY_OUTCOME_UNRESOLVED"})
        run = NoteRun(
            tenant_id=tenant_id,
            snapshot_id=snapshot_id,
            created_by=identity.user_id,
            idempotency_key=key,
            status="pending",
        )
        session.add(run)
        session.flush()
        return run.id, True


def load_input(identity: ActorIdentity, tenant_id: UUID, run_id: UUID) -> tuple[str, str]:
    with authorized_transaction(identity, tenant_id) as (session, _role):
        run = session.scalar(
            select(NoteRun).where(NoteRun.tenant_id == tenant_id, NoteRun.id == run_id)
        )
        if run is None or run.created_by != identity.user_id or run.status != "pending":
            raise HTTPException(409, detail={"code": "SUMMARY_RUN_CHANGED"})
        snapshot = session.scalar(
            select(SourceSnapshot).where(
                SourceSnapshot.tenant_id == tenant_id, SourceSnapshot.id == run.snapshot_id
            )
        )
        if snapshot is None:
            raise HTTPException(404, detail={"code": "SNAPSHOT_NOT_FOUND"})
        total = (
            session.scalar(
                select(func.count())
                .select_from(NoteRevision)
                .where(NoteRevision.tenant_id == tenant_id)
            )
            or 0
        )
        if total >= 1000:
            raise HTTPException(429, detail={"code": "NOTE_QUOTA_EXCEEDED"})
        # Evidence remains an exact substring of the immutable snapshot. Scope
        # belongs in the title/context, never in invented quote material.
        prefix = (
            "用户提供的导入内容，未与关联平台核验："
            if snapshot.provenance == "user_import"
            else "仅元数据/简介，未取得视频字幕："
            if snapshot.content_kind == "metadata"
            else ""
        )
        title = (prefix + (snapshot.title or "来源内容"))[:300]
        return title, snapshot.body_text


def close_run(
    identity: ActorIdentity, tenant_id: UUID, run_id: UUID, status: str, code: str | None
) -> dict[str, Any]:
    with authorized_transaction(identity, tenant_id) as (session, _role):
        run = session.scalar(
            select(NoteRun)
            .where(NoteRun.tenant_id == tenant_id, NoteRun.id == run_id)
            .with_for_update()
        )
        if run is None or run.created_by != identity.user_id:
            raise HTTPException(404, detail={"code": "SUMMARY_RUN_NOT_FOUND"})
        if run.status == "pending":
            run.status, run.error_code = status, code
        return run_result(session, run)


def save_document(
    identity: ActorIdentity,
    tenant_id: UUID,
    run_id: UUID,
    raw: str,
    text: str,
    stages: list[dict] | None = None,
) -> dict[str, Any]:
    evidence = build_evidence(text)
    document = final_document(raw, evidence, stages) if stages else parse_summary(raw, evidence)
    with authorized_transaction(identity, tenant_id) as (session, role):
        if role not in ("owner", "admin"):
            raise HTTPException(403, detail={"code": "FORBIDDEN"})
        run = session.scalar(
            select(NoteRun)
            .where(NoteRun.tenant_id == tenant_id, NoteRun.id == run_id)
            .with_for_update()
        )
        if run is None or run.created_by != identity.user_id or run.status != "pending":
            raise HTTPException(409, detail={"code": "SUMMARY_RUN_CHANGED"})
        snapshot = session.scalar(
            select(SourceSnapshot).where(
                SourceSnapshot.tenant_id == tenant_id, SourceSnapshot.id == run.snapshot_id
            )
        )
        if snapshot is None:
            raise HTTPException(404, detail={"code": "SNAPSHOT_NOT_FOUND"})
        if snapshot.body_text != text:
            raise ValueError("SUMMARY_SNAPSHOT_MISMATCH")
        document = snapshot_scope(document, snapshot.content_kind, snapshot.provenance)
        markdown = render_markdown(document, evidence)
        if (
            session.scalar(
                select(func.count())
                .select_from(NoteRevision)
                .where(NoteRevision.tenant_id == tenant_id)
            )
            or 0
        ) >= 1000:
            raise HTTPException(429, detail={"code": "NOTE_QUOTA_EXCEEDED"})
        note = session.scalar(
            select(Note).where(Note.tenant_id == tenant_id, Note.source_id == snapshot.source_id)
        )
        if note is None:
            note = Note(
                tenant_id=tenant_id, source_id=snapshot.source_id, created_by=identity.user_id
            )
            session.add(note)
            session.flush()
        version = (
            session.scalar(
                select(func.max(NoteRevision.version)).where(
                    NoteRevision.tenant_id == tenant_id, NoteRevision.note_id == note.id
                )
            )
            or 0
        ) + 1
        revision = NoteRevision(
            tenant_id=tenant_id,
            note_id=note.id,
            snapshot_id=snapshot.id,
            run_id=run.id,
            created_by=identity.user_id,
            version=version,
            title=document.title,
            content_md=markdown,
            document=document.model_dump(),
        )
        session.add(revision)
        session.flush()
        sections = [
            {"kind": "overview", "items": [document.overview.model_dump()]},
            *[section.model_dump() for section in document.sections],
        ]
        for section in sections:
            session.add(NoteSection(tenant_id=tenant_id, revision_id=revision.id, **section))
        cited = {key for item in document_items(document) for key in item.evidence_keys}
        for item in evidence:
            if item.key in cited:
                session.add(
                    EvidenceItem(
                        tenant_id=tenant_id,
                        revision_id=revision.id,
                        snapshot_id=snapshot.id,
                        evidence_key=item.key,
                        quote=item.text,
                    )
                )
        record(session, tenant_id, identity.user_id, AuditAction.NOTE_GENERATED, revision.id)
        run.status, run.error_code = "succeeded", None
        session.flush()
        return run_result(session, run)


async def summarize(
    identity: ActorIdentity, tenant_id: UUID, snapshot_id: UUID, key: UUID
) -> dict[str, Any]:
    run_id, fresh = await run_in_threadpool(begin_run, identity, tenant_id, snapshot_id, key)
    if not fresh:
        return await run_in_threadpool(read_run, identity, tenant_id, run_id)
    submitted = False
    try:
        choices = await run_in_threadpool(provider_choices, identity, tenant_id)
        if not choices:
            return await run_in_threadpool(
                close_run, identity, tenant_id, run_id, "failed", "NO_SUMMARY_PROVIDER"
            )
        if choices[0][1] != "deepseek":
            return await run_in_threadpool(
                close_run, identity, tenant_id, run_id, "failed", "SUMMARY_PROVIDER_UNSUPPORTED"
            )
        choices = [choice for choice in choices if choice[1] == "deepseek"]
        if not choices:
            return await run_in_threadpool(
                close_run, identity, tenant_id, run_id, "failed", "NO_SUMMARY_PROVIDER"
            )
        title, text = await run_in_threadpool(load_input, identity, tenant_id, run_id)
        evidence = build_evidence(text)
        groups = batches(evidence)
        if len(groups) > 1:
            return await continue_long(identity, tenant_id, run_id, title, text, choices[0][0])
        prompt = build_prompt(title, evidence)
        for provider_id, first_kind in choices:
            usage_id = uuid4()
            config = await run_in_threadpool(
                reserve_attempt, identity, tenant_id, provider_id, usage_id, first_kind
            )
            if config is None:
                continue
            try:
                submitted = True
                result = await DeepSeekSummaryProvider(config.model, config.api_key).generate(
                    prompt
                )
            except ProviderError as error:
                # A status number is safe diagnostic data; provider response
                # bodies, prompts and credentials are never persisted.
                usage_code = (
                    f"PROVIDER_HTTP_{error.http_status}"
                    if error.http_status is not None
                    else error.code
                )
                await run_in_threadpool(
                    finish_attempt,
                    identity,
                    tenant_id,
                    usage_id,
                    None,
                    usage_code,
                    error.outcome_unknown,
                )
                if error.outcome_unknown:
                    return await run_in_threadpool(
                        close_run,
                        identity,
                        tenant_id,
                        run_id,
                        "unknown",
                        f"MODEL_HTTP_{error.http_status}_UNKNOWN"
                        if error.http_status is not None
                        else "MODEL_OUTCOME_UNKNOWN",
                    )
                submitted = False
            else:
                # Record true usage even if structural/citation validation fails.
                await run_in_threadpool(
                    finish_attempt, identity, tenant_id, usage_id, result, None, False
                )
                try:
                    return await run_in_threadpool(
                        save_document, identity, tenant_id, run_id, result.text, text
                    )
                except ValueError:
                    return await run_in_threadpool(
                        close_run,
                        identity,
                        tenant_id,
                        run_id,
                        "failed",
                        "SUMMARY_VALIDATION_FAILED",
                    )
        return await run_in_threadpool(
            close_run, identity, tenant_id, run_id, "failed", "ALL_PROVIDERS_UNAVAILABLE"
        )
    except ValueError:
        return await run_in_threadpool(
            close_run,
            identity,
            tenant_id,
            run_id,
            "unknown" if submitted else "failed",
            "MODEL_OUTCOME_UNKNOWN" if submitted else "SUMMARY_VALIDATION_FAILED",
        )
    except HTTPException as error:
        if error.status_code == 409 and error.detail == {"code": "SUMMARY_STAGE_ALREADY_CLAIMED"}:
            return await run_in_threadpool(read_run, identity, tenant_id, run_id)
        if error.status_code in (401, 403, 404):
            # Lost permission means no privileged cleanup or note commit. Durable
            # pending state blocks automatic retries, preserving charge uncertainty.
            raise
        return await run_in_threadpool(
            close_run,
            identity,
            tenant_id,
            run_id,
            "unknown" if submitted else "failed",
            "MODEL_OUTCOME_UNKNOWN" if submitted else "SUMMARY_NOT_AVAILABLE",
        )


async def continue_long(
    identity: ActorIdentity,
    tenant_id: UUID,
    run_id: UUID,
    title: str,
    text: str,
    provider_id: UUID,
    saved: list[Any] | None = None,
) -> dict[str, Any]:
    """One durable claim per position; completed synthesis can commit without I/O."""
    evidence = build_evidence(text)
    groups = batches(evidence)
    stages: list[dict] = []
    saved = saved or []
    if len(groups) < 2 or len(saved) > len(groups) + 1:
        raise HTTPException(409, detail={"code": "SUMMARY_CHECKPOINT_INVALID"})
    model = saved[0].model if saved else None
    for index in range(len(groups) + 1):
        prompt = (
            batch_prompt(title, groups[index])
            if index < len(groups)
            else synthesis_prompt(title, stages)[0]
        )
        if index < len(saved):
            checkpoint = saved[index]
            if (
                checkpoint.position != index
                or checkpoint.protocol != checkpoints.PROTOCOL
                or checkpoint.provider_id != provider_id
                or checkpoint.model != model
                or checkpoint.prompt_sha256 != hashlib.sha256(prompt.encode()).hexdigest()
            ):
                raise HTTPException(409, detail={"code": "SUMMARY_CHECKPOINT_INVALID"})
            raw = json.dumps(checkpoint.output, ensure_ascii=False)
            if index < len(groups):
                stages.append(parse_batch(raw, groups[index]))
                continue
            final_document(raw, evidence, stages)
            return await run_in_threadpool(
                save_document, identity, tenant_id, run_id, raw, text, stages
            )
        try:
            _, config = await run_in_threadpool(
                checkpoints.claim, identity, tenant_id, run_id, index, prompt, provider_id, model
            )
        except HTTPException as error:
            if error.status_code in (409, 429):
                # Another executor may own this call, or quota/provider config
                # may block future calls. Never close a run another owns.
                return await run_in_threadpool(read_run, identity, tenant_id, run_id)
            raise
        model = config.model
        try:
            result = await DeepSeekSummaryProvider(config.model, config.api_key).generate(prompt)
        except ProviderError as error:
            await run_in_threadpool(
                checkpoints.complete,
                identity,
                tenant_id,
                run_id,
                index,
                None,
                None,
                error.code,
                error.outcome_unknown,
            )
            return await run_in_threadpool(
                close_run,
                identity,
                tenant_id,
                run_id,
                "unknown" if error.outcome_unknown else "failed",
                "MODEL_OUTCOME_UNKNOWN" if error.outcome_unknown else "SUMMARY_STAGE_FAILED",
            )
        try:
            output = (
                parse_batch(result.text, groups[index])
                if index < len(groups)
                else json.loads(result.text)
            )
            if index == len(groups):
                final_document(result.text, evidence, stages)
        except ValueError as error:
            code = (
                str(error)
                if str(error) in {"SUMMARY_PROCESSING_COUNT_CLAIM", "SUMMARY_NUMBER_UNSUPPORTED"}
                else "SUMMARY_VALIDATION_FAILED"
            )
            if isinstance(error, ValidationError):
                code = "SUMMARY_SCHEMA_INVALID"
            await run_in_threadpool(
                checkpoints.complete, identity, tenant_id, run_id, index, result, None, code
            )
            return await run_in_threadpool(close_run, identity, tenant_id, run_id, "failed", code)
        await run_in_threadpool(
            checkpoints.complete, identity, tenant_id, run_id, index, result, output, None
        )
        if index < len(groups):
            stages.append(output)
        else:
            return await run_in_threadpool(
                save_document, identity, tenant_id, run_id, result.text, text, stages
            )
    raise RuntimeError("SUMMARY_STAGE_INVALID")


async def resume_summary(identity: ActorIdentity, tenant_id: UUID, run_id: UUID) -> dict[str, Any]:
    saved = await run_in_threadpool(checkpoints.load, identity, tenant_id, run_id)
    title, text = await run_in_threadpool(load_input, identity, tenant_id, run_id)
    try:
        return await continue_long(
            identity, tenant_id, run_id, title, text, saved[0].provider_id, saved
        )
    except ValueError:
        return await run_in_threadpool(
            close_run, identity, tenant_id, run_id, "failed", "SUMMARY_VALIDATION_FAILED"
        )
