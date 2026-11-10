"""Durable authorization and quota phases for explicit provider connection tests."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.integrations.ai import Generation
from app.integrations.credentials import decrypt
from app.modules.ai.models import AIProvider, AIUsage, UserSettings
from app.modules.identity.access import ActorIdentity, authorized_transaction


@dataclass
class InvocationConfig:
    kind: str
    model: str
    endpoint_name: str | None
    api_key: SecretStr


def admin(role: str) -> None:
    if role not in ("owner", "admin"):
        raise HTTPException(403, detail={"code": "FORBIDDEN"})


def cloud_enabled(kind: str) -> bool:
    settings = get_settings()
    return (kind == "openai" and settings.openai_enabled) or (
        kind == "deepseek" and settings.deepseek_enabled
    )


def cloud_fallback_allowed(session: Session, identity: ActorIdentity, tenant_id: UUID) -> bool:
    row = session.get(UserSettings, (tenant_id, identity.user_id))
    return bool(row and row.preferences.get("allow_cloud_fallback") is True)


def provider_choices(identity: ActorIdentity, tenant_id: UUID) -> list[tuple[UUID, str]]:
    with authorized_transaction(identity, tenant_id) as (session, role):
        admin(role)
        rows = list(
            session.scalars(
                select(AIProvider)
                .where(
                    AIProvider.tenant_id == tenant_id,
                    AIProvider.enabled.is_(True),
                    AIProvider.consent.is_(True),
                )
                .order_by(AIProvider.priority, AIProvider.id)
            )
        )
        rows = [
            row for row in rows if row.kind not in {"openai", "deepseek"} or cloud_enabled(row.kind)
        ]
        if (
            rows
            and rows[0].kind == "ollama"
            and not cloud_fallback_allowed(session, identity, tenant_id)
        ):
            rows = [row for row in rows if row.kind not in {"openai", "deepseek"}]
        if not rows:
            raise HTTPException(409, detail={"code": "NO_AUTHORIZED_PROVIDER"})
        return [(row.id, row.kind) for row in rows[:2]]


def reserve_attempt(
    identity: ActorIdentity, tenant_id: UUID, provider_id: UUID, usage_id: UUID, first_kind: str
) -> InvocationConfig | None:
    with authorized_transaction(identity, tenant_id) as (session, role):
        admin(role)
        return reserve_in_session(session, identity, tenant_id, provider_id, usage_id, first_kind)


def reserve_in_session(
    session: Session,
    identity: ActorIdentity,
    tenant_id: UUID,
    provider_id: UUID,
    usage_id: UUID,
    first_kind: str,
) -> InvocationConfig | None:
    """Caller owns an authenticated, admin-checked and locked tenant transaction."""
    provider = session.scalar(
        select(AIProvider)
        .where(
            AIProvider.tenant_id == tenant_id,
            AIProvider.id == provider_id,
        )
        .with_for_update()
    )
    if provider is None or not provider.enabled or not provider.consent:
        return None
    if provider.kind in {"openai", "deepseek"}:
        if not cloud_enabled(provider.kind):
            return None
        if first_kind == "ollama" and not cloud_fallback_allowed(session, identity, tenant_id):
            return None
        if not provider.key_version or not provider.encrypted_key:
            raise HTTPException(409, detail={"code": "PROVIDER_KEY_REQUIRED"})
        key = SecretStr(
            decrypt(provider.key_version, provider.encrypted_key, tenant_id, provider.id)
        )
    else:
        key = SecretStr("")
    daily = session.scalar(
        select(func.count())
        .select_from(AIUsage)
        .where(
            AIUsage.tenant_id == tenant_id,
            AIUsage.created_at
            >= datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0),
        )
    )
    if (daily or 0) >= 20:
        raise HTTPException(429, detail={"code": "MODEL_DAILY_QUOTA"})
    session.add(
        AIUsage(
            id=usage_id,
            tenant_id=tenant_id,
            user_id=identity.user_id,
            provider_id=provider_id,
            model=provider.model,
            status="reserved",
        )
    )
    return InvocationConfig(provider.kind, provider.model, provider.endpoint_name, key)


def finish_attempt(
    identity: ActorIdentity,
    tenant_id: UUID,
    usage_id: UUID,
    result: Generation | None,
    code: str | None,
    unknown: bool,
) -> None:
    with authorized_transaction(identity, tenant_id) as (session, role):
        admin(role)
        finish_in_session(session, identity, tenant_id, usage_id, result, code, unknown)


def finish_in_session(
    session: Session,
    identity: ActorIdentity,
    tenant_id: UUID,
    usage_id: UUID,
    result: Generation | None,
    code: str | None,
    unknown: bool,
) -> None:
    """Allow checkpoints and real usage to commit in the same transaction."""
    row = session.scalar(
        select(AIUsage)
        .where(
            AIUsage.tenant_id == tenant_id,
            AIUsage.id == usage_id,
            AIUsage.user_id == identity.user_id,
        )
        .with_for_update()
    )
    if row is None or row.status != "reserved":
        raise HTTPException(409, detail={"code": "MODEL_RESERVATION_CHANGED"})
    row.error_code = code
    if result is not None:
        row.status = "succeeded"
        row.input_tokens, row.output_tokens = result.input_tokens, result.output_tokens
    elif not unknown:
        row.status = "failed"
