from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import Tenant
from app.integrations.ai import DeepSeekProvider, OllamaProvider, OpenAIProvider, ProviderError
from app.integrations.credentials import decrypt, encrypt
from app.modules.ai.models import AIProvider, AIUsage, UserSettings
from app.modules.ai.service import finish_attempt, provider_choices, reserve_attempt
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record_event
from app.modules.identity.access import (
    ActorIdentity,
    SpaceAccess,
    lock_access,
    member_access,
    require_admin,
)

router = APIRouter(prefix="/api/v1/spaces/{tenant_id}", tags=["model-settings"])
Access = Annotated[SpaceAccess, Depends(member_access)]


class ProviderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["openai", "deepseek", "ollama"]
    model: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_./:-]+$")
    endpoint_name: str | None = Field(default=None, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    api_key: SecretStr | None = None
    priority: int = Field(default=10, ge=0, le=100)
    enabled: bool = False
    consent: bool = False


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    theme: Literal["system", "light", "dark"] = "system"
    allow_cloud_fallback: bool = False


class Invocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=4000, repr=False)
    consent: bool


def public_config(provider: AIProvider) -> dict[str, Any]:
    return {
        "id": str(provider.id),
        "kind": provider.kind,
        "model": provider.model,
        "endpoint_name": provider.endpoint_name,
        "enabled": provider.enabled,
        "priority": provider.priority,
        "consent": provider.consent,
        "has_key": provider.encrypted_key is not None,
        "adapter_verified": True,
    }


@router.get("/model-options")
def options(access: Access) -> dict[str, Any]:
    require_admin(access)
    return {
        "data": {
            "ollama_endpoints": list(get_settings().ollama_endpoints),
            "cloud_adapter_verified": True,
            "cloud_enabled": get_settings().openai_enabled,
            "deepseek_enabled": get_settings().deepseek_enabled,
            "real_model_verified": False,
        }
    }


@router.get("/providers")
def providers(access: Access) -> dict[str, Any]:
    require_admin(access)
    rows = access.actor.session.scalars(
        select(AIProvider)
        .where(AIProvider.tenant_id == access.tenant.id)
        .order_by(AIProvider.priority, AIProvider.id)
    )
    return {"data": [public_config(row) for row in rows]}


@router.post("/providers", status_code=201)
def create_provider(body: ProviderInput, access: Access) -> dict[str, Any]:
    lock_access(access)
    require_admin(access)
    access.actor.session.execute(
        select(Tenant).where(Tenant.id == access.tenant.id).with_for_update(key_share=True)
    )
    count = access.actor.session.scalar(
        select(func.count()).select_from(AIProvider).where(AIProvider.tenant_id == access.tenant.id)
    )
    if (count or 0) >= 5:
        raise HTTPException(429, detail={"code": "PROVIDER_CONFIG_LIMIT"})
    validate_provider_input(body, has_key=False)
    provider_id = uuid4()
    version, ciphertext = None, None
    if body.api_key is not None:
        version, ciphertext = encrypt(
            body.api_key.get_secret_value(), access.tenant.id, provider_id
        )
    provider = AIProvider(
        id=provider_id,
        tenant_id=access.tenant.id,
        kind=body.kind,
        model=body.model,
        endpoint_name=body.endpoint_name,
        encrypted_key=ciphertext,
        key_version=version,
        priority=body.priority,
        enabled=body.enabled,
        consent=body.consent,
    )
    access.actor.session.add(provider)
    record_event(access, AuditAction.PROVIDER_CREATED, provider.id)
    return {"data": public_config(provider)}


def validate_provider_input(body: ProviderInput, *, has_key: bool) -> None:
    if body.enabled and not body.consent:
        raise HTTPException(400, detail={"code": "EXPLICIT_MODEL_CONSENT_REQUIRED"})
    if body.kind in {"openai", "deepseek"}:
        enabled = (
            get_settings().openai_enabled
            if body.kind == "openai"
            else get_settings().deepseek_enabled
        )
        if body.enabled and not enabled:
            raise HTTPException(409, detail={"code": "CLOUD_DISABLED_BY_OPERATOR"})
        if body.enabled and body.api_key is None and not has_key:
            raise HTTPException(400, detail={"code": "PROVIDER_KEY_REQUIRED"})
        if body.endpoint_name is not None:
            raise HTTPException(400, detail={"code": "CLOUD_ENDPOINT_FIXED"})
    if body.kind == "ollama" and body.endpoint_name not in get_settings().ollama_endpoints:
        raise HTTPException(400, detail={"code": "LOCAL_ENDPOINT_NOT_AUTHORIZED"})
    if body.api_key is not None:
        secret = body.api_key.get_secret_value()
        if len(secret) < 16 or len(secret) > 4096:
            raise HTTPException(400, detail={"code": "INVALID_PROVIDER_KEY"})


def find_provider(access: SpaceAccess, provider_id: UUID) -> AIProvider:
    provider = access.actor.session.scalar(
        select(AIProvider)
        .where(AIProvider.tenant_id == access.tenant.id, AIProvider.id == provider_id)
        .with_for_update()
    )
    if provider is None:
        raise HTTPException(404, detail={"code": "PROVIDER_NOT_FOUND"})
    return provider


@router.post("/providers/{provider_id}/update")
def update_provider(provider_id: UUID, body: ProviderInput, access: Access) -> dict[str, Any]:
    lock_access(access)
    require_admin(access)
    provider = find_provider(access, provider_id)
    # A provider ID keeps its supplier identity and historical usage references.
    if body.kind != provider.kind:
        raise HTTPException(400, detail={"code": "PROVIDER_KIND_IMMUTABLE"})
    validate_provider_input(body, has_key=bool(provider.encrypted_key and provider.key_version))
    if body.api_key is not None:
        provider.key_version, provider.encrypted_key = encrypt(
            body.api_key.get_secret_value(), access.tenant.id, provider.id
        )
    provider.model = body.model
    provider.endpoint_name = body.endpoint_name
    provider.priority = body.priority
    provider.enabled = body.enabled
    provider.consent = body.consent
    record_event(access, AuditAction.PROVIDER_UPDATED, provider.id)
    return {"data": public_config(provider)}


@router.post("/providers/{provider_id}/disable")
def disable_provider(provider_id: UUID, access: Access) -> dict[str, Any]:
    lock_access(access)
    require_admin(access)
    provider = find_provider(access, provider_id)
    provider.enabled = False
    provider.encrypted_key, provider.key_version = None, None
    record_event(access, AuditAction.PROVIDER_DISABLED, provider.id)
    return {"data": public_config(provider)}


@router.post("/providers/{provider_id}/rotate-encryption")
def rotate_provider(provider_id: UUID, access: Access) -> dict[str, Any]:
    lock_access(access)
    require_admin(access)
    provider = find_provider(access, provider_id)
    if provider.encrypted_key and provider.key_version:
        secret = decrypt(
            provider.key_version, provider.encrypted_key, access.tenant.id, provider.id
        )
        provider.key_version, provider.encrypted_key = encrypt(
            secret, access.tenant.id, provider.id
        )
        record_event(access, AuditAction.PROVIDER_REENCRYPTED, provider.id)
    return {"data": public_config(provider)}


@router.get("/settings")
def preferences(access: Access) -> dict[str, Any]:
    row = access.actor.session.get(UserSettings, (access.tenant.id, access.actor.user.id))
    return {"data": row.preferences if row else Preferences().model_dump()}


@router.post("/settings")
def save_preferences(body: Preferences, access: Access) -> dict[str, Any]:
    lock_access(access)
    row = access.actor.session.get(UserSettings, (access.tenant.id, access.actor.user.id))
    if row is None:
        row = UserSettings(
            tenant_id=access.tenant.id, user_id=access.actor.user.id, preferences=body.model_dump()
        )
        access.actor.session.add(row)
    else:
        row.preferences = body.model_dump()
    record_event(access, AuditAction.SETTINGS_UPDATED, access.actor.user.id)
    return {"data": row.preferences}


@router.get("/ai-usage")
def usage(access: Access) -> dict[str, Any]:
    rows = access.actor.session.scalars(
        select(AIUsage)
        .where(AIUsage.tenant_id == access.tenant.id, AIUsage.user_id == access.actor.user.id)
        .order_by(AIUsage.created_at.desc())
        .limit(100)
    )
    return {
        "data": [
            {
                "id": str(row.id),
                "model": row.model,
                "status": row.status,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "cost_usd": str(row.cost_usd) if row.cost_usd is not None else None,
                "error_code": row.error_code,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    }


@router.post("/ai-test")
async def test_connection(body: Invocation, access: Access) -> dict[str, Any]:
    require_admin(access)
    if not body.consent:
        raise HTTPException(400, detail={"code": "EXPLICIT_MODEL_CONSENT_REQUIRED"})
    identity = ActorIdentity(access.actor.user.id, access.actor.token_hash)
    tenant_id = access.tenant.id
    # Do not keep an authorization transaction/row locks across model I/O.
    # Every durable phase below revalidates the session, membership AND admin role.
    access.actor.session.rollback()
    choices = await run_in_threadpool(provider_choices, identity, tenant_id)
    errors = []
    first_kind = choices[0][1]
    for provider_id, _ in choices:
        usage_id = uuid4()
        config = await run_in_threadpool(
            reserve_attempt, identity, tenant_id, provider_id, usage_id, first_kind
        )
        if config is None:
            continue
        provider = (
            OpenAIProvider(config.model, config.api_key)
            if config.kind == "openai"
            else DeepSeekProvider(config.model, config.api_key)
            if config.kind == "deepseek"
            else OllamaProvider(config.model, config.endpoint_name or "")
        )
        try:
            result = await provider.generate(body.prompt)
        except ProviderError as exc:
            await run_in_threadpool(
                finish_attempt, identity, tenant_id, usage_id, None, exc.code, exc.outcome_unknown
            )
            errors.append({"provider_id": str(provider_id), "code": exc.code})
            if exc.outcome_unknown:
                raise HTTPException(
                    503, detail={"code": "MODEL_OUTCOME_UNKNOWN", "attempts": errors}
                ) from None
        else:
            await run_in_threadpool(
                finish_attempt, identity, tenant_id, usage_id, result, None, False
            )
            return {
                "data": {
                    "text": result.text,
                    "provider_id": str(provider_id),
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "cost_usd": None,
                    "fallback_errors": errors,
                }
            }
    raise HTTPException(503, detail={"code": "ALL_PROVIDERS_UNAVAILABLE", "attempts": errors})
