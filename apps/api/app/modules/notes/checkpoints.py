"""Durable per-call claims; a dispatched stage is never submitted again."""

import hashlib
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.ai import Generation
from app.modules.ai.service import (
    InvocationConfig,
    admin,
    finish_in_session,
    reserve_in_session,
)
from app.modules.identity.access import ActorIdentity, authorized_transaction
from app.modules.notes.models import NoteRun, SummaryCheckpoint

PROTOCOL = "bounded-map-synthesis-v2"


def rows(session: Session, run: NoteRun) -> list[SummaryCheckpoint]:
    return list(
        session.scalars(
            select(SummaryCheckpoint)
            .where(
                SummaryCheckpoint.tenant_id == run.tenant_id,
                SummaryCheckpoint.run_id == run.id,
            )
            .order_by(SummaryCheckpoint.position)
        )
    )


def progress(session: Session, run: NoteRun) -> dict[str, Any]:
    checkpoints = rows(session, run)
    safe = bool(checkpoints) and all(c.status == "succeeded" for c in checkpoints)
    return {
        "completed_stages": sum(c.status == "succeeded" for c in checkpoints),
        "blocked_stage": next((c.position for c in checkpoints if c.status != "succeeded"), None),
        "can_resume": run.status == "pending" and safe,
        "checkpoint_protocol": PROTOCOL if checkpoints else None,
    }


def load(identity: ActorIdentity, tenant: UUID, run_id: UUID) -> list[SummaryCheckpoint]:
    with authorized_transaction(identity, tenant) as (session, role):
        admin(role)
        run = session.get(NoteRun, run_id)
        if run is None or run.created_by != identity.user_id or run.status != "pending":
            raise HTTPException(409, detail={"code": "SUMMARY_RUN_CHANGED"})
        result = rows(session, run)
        if not result or any(c.status != "succeeded" or c.protocol != PROTOCOL for c in result):
            raise HTTPException(409, detail={"code": "SUMMARY_OUTCOME_UNRESOLVED"})
        if [c.position for c in result] != list(range(len(result))):
            raise HTTPException(409, detail={"code": "SUMMARY_CHECKPOINT_INVALID"})
        # Disable expiration before the read-only transaction commits, so the
        # detached immutable values can safely be used after process restart.
        session.expire_on_commit = False
        session.expunge_all()
        return result


def claim(
    identity: ActorIdentity,
    tenant: UUID,
    run_id: UUID,
    position: int,
    prompt: str,
    provider_id: UUID,
    model: str | None = None,
) -> tuple[UUID, InvocationConfig]:
    with authorized_transaction(identity, tenant) as (session, role):
        admin(role)
        run = session.get(NoteRun, run_id)
        if run is None or run.created_by != identity.user_id or run.status != "pending":
            raise HTTPException(409, detail={"code": "SUMMARY_RUN_CHANGED"})
        previous = rows(session, run)
        if len(previous) != position or any(c.status != "succeeded" for c in previous):
            raise HTTPException(409, detail={"code": "SUMMARY_STAGE_ALREADY_CLAIMED"})
        usage_id = uuid4()
        config = reserve_in_session(session, identity, tenant, provider_id, usage_id, "deepseek")
        if config is None or config.kind != "deepseek" or (model and config.model != model):
            raise HTTPException(409, detail={"code": "SUMMARY_PROVIDER_CHANGED"})
        session.flush()  # Insert usage before the composite checkpoint FK.
        session.add(
            SummaryCheckpoint(
                tenant_id=tenant,
                run_id=run_id,
                position=position,
                usage_id=usage_id,
                provider_id=provider_id,
                model=config.model,
                protocol=PROTOCOL,
                prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                status="dispatched",
            )
        )
        return usage_id, config


def complete(
    identity: ActorIdentity,
    tenant: UUID,
    run_id: UUID,
    position: int,
    result: Generation | None,
    output: dict | None,
    code: str | None,
    unknown: bool = False,
) -> None:
    with authorized_transaction(identity, tenant) as (session, role):
        admin(role)
        run = session.get(NoteRun, run_id)
        row = session.get(SummaryCheckpoint, (tenant, run_id, position))
        if (
            run is None
            or run.created_by != identity.user_id
            or run.status != "pending"
            or row is None
            or row.status != "dispatched"
        ):
            raise HTTPException(409, detail={"code": "SUMMARY_STAGE_CHANGED"})
        finish_in_session(session, identity, tenant, row.usage_id, result, code, unknown)
        row.status = "succeeded" if output is not None else "unknown" if unknown else "failed"
        row.output, row.error_code = output, code
