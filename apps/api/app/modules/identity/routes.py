import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import ProcessingJob, Tenant, TenantMember, User
from app.db.session import get_engine
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record, record_event
from app.modules.identity.access import (
    Actor,
    SpaceAccess,
    authenticated,
    lock_access,
    member_access,
    require_admin,
    set_tenant,
    set_user,
    token_digest,
)
from app.modules.identity.models import AuthIdentity, AuthSession
from app.modules.identity.passwords import hash_password, verify_password
from app.modules.identity.rate_limit import rate_limit
from app.modules.identity.recovery import create_action, deliver_action, locked_user
from app.modules.identity.security_events import (
    SecurityAction,
    record_login_failure,
    record_security_event,
)
from app.modules.jobs.models import JobDispatch, JobStep

router = APIRouter(prefix="/api/v1", tags=["identity"])
DUMMY_HASH = hash_password("not-a-real-account-password")


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=256, repr=False)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Invalid email address")
        return value


class SpaceName(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class MemberInput(BaseModel):
    user_id: UUID
    role: Literal["admin", "member"] = "member"


def issue_session(session: Session, user_id: UUID) -> dict[str, Any]:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(hours=12)
    session.add(AuthSession(token_hash=token_digest(token), user_id=user_id, expires_at=expires))
    return {"access_token": token, "token_type": "bearer", "expires_at": expires.isoformat()}


@router.post("/auth/register", status_code=201)
def register(
    body: Credentials, request: Request, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    if not get_settings().registration_enabled:
        raise HTTPException(403, detail={"code": "REGISTRATION_DISABLED"})
    rate_limit(request, "register", body.email)
    user_id, tenant_id = uuid4(), uuid4()
    try:
        with Session(get_engine()) as session, session.begin():
            set_user(session, user_id)
            set_tenant(session, tenant_id)
            user = User(id=user_id, email=body.email, password_hash=hash_password(body.password))
            session.add(user)
            session.flush()
            session.add(AuthIdentity(email=body.email, user_id=user_id))
            session.add(Tenant(id=tenant_id, name="个人空间"))
            session.flush()
            session.add(TenantMember(tenant_id=tenant_id, user_id=user_id, role="owner"))
            session.flush()
            record(session, tenant_id, user_id, AuditAction.SPACE_CREATED, tenant_id)
            record_security_event(session, user_id, SecurityAction.ACCOUNT_REGISTERED)
            verification_token = (
                create_action(session, user, "verify_email")
                if get_settings().email_verification_required
                else None
            )
            result: dict[str, Any] = (
                {"verification_required": True}
                if verification_token
                else issue_session(session, user_id)
            )
            result.update(user_id=str(user_id), tenant_id=str(tenant_id))
        if verification_token:
            background_tasks.add_task(
                deliver_action, body.email, "verify_email", verification_token
            )
        return {"data": result}
    except IntegrityError:
        raise HTTPException(409, detail={"code": "REGISTRATION_UNAVAILABLE"}) from None


@router.post("/auth/login")
def login(body: Credentials, request: Request) -> dict[str, Any]:
    rate_limit(request, "login", body.email)
    failure: tuple[int, str] | None = None
    result: dict[str, Any] = {}
    with Session(get_engine()) as session, session.begin():
        identity = session.get(AuthIdentity, body.email)
        # Serialize session issuance with password reset/revocation. Reading an
        # old password before reset must not create a session after reset commits.
        user = locked_user(session, identity.user_id) if identity else None
        valid = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
        if not valid or user is None or user.status != "active":
            record_login_failure(session, user.id if user else None, SecurityAction.LOGIN_FAILED)
            failure = (401, "INVALID_CREDENTIALS")
        elif get_settings().email_verification_required and not user.email_verified:
            record_login_failure(session, user.id, SecurityAction.LOGIN_UNVERIFIED)
            failure = (403, "EMAIL_VERIFICATION_REQUIRED")
        else:
            result = issue_session(session, user.id)
            record_security_event(session, user.id, SecurityAction.LOGIN_SUCCEEDED)
    # Commit bounded failure observations before returning the original rejection.
    # Raising inside session.begin() would roll back the security history too.
    if failure is not None:
        raise HTTPException(failure[0], detail={"code": failure[1]})
    return {"data": result}


@router.post("/auth/logout", status_code=200)
def logout(actor: Annotated[Actor, Depends(authenticated, scope="function")]) -> dict[str, Any]:
    actor.session.execute(delete(AuthSession).where(AuthSession.token_hash == actor.token_hash))
    record_security_event(actor.session, actor.user.id, SecurityAction.SESSION_LOGGED_OUT)
    return {"data": {"logged_out": True}}


@router.post("/auth/logout-all")
def logout_all(actor: Annotated[Actor, Depends(authenticated, scope="function")]) -> dict[str, Any]:
    actor.session.execute(delete(AuthSession).where(AuthSession.user_id == actor.user.id))
    record_security_event(actor.session, actor.user.id, SecurityAction.SESSION_ALL_REVOKED)
    return {"data": {"logged_out": True}}


@router.get("/auth/me")
def me(actor: Annotated[Actor, Depends(authenticated, scope="function")]) -> dict[str, Any]:
    login = actor.session.get(AuthSession, actor.token_hash)
    if login is None:
        raise HTTPException(401, detail={"code": "UNAUTHORIZED"})
    return {
        "data": {
            "id": str(actor.user.id),
            "email": actor.user.email,
            "expires_at": login.expires_at.isoformat(),
        }
    }


@router.get("/spaces")
def spaces(actor: Annotated[Actor, Depends(authenticated, scope="function")]) -> dict[str, Any]:
    rows = actor.session.execute(
        select(Tenant, TenantMember.role)
        .join(TenantMember, Tenant.id == TenantMember.tenant_id)
        .where(
            TenantMember.user_id == actor.user.id,
            TenantMember.active.is_(True),
            Tenant.status == "active",
        )
    )
    return {"data": [{"id": str(t.id), "name": t.name, "role": role} for t, role in rows]}


@router.post("/spaces", status_code=201)
def create_space(
    body: SpaceName, actor: Annotated[Actor, Depends(authenticated, scope="function")]
) -> dict[str, Any]:
    actor.session.execute(
        select(User).where(User.id == actor.user.id).with_for_update(key_share=True)
    )
    owned = actor.session.scalar(
        select(func.count())
        .select_from(TenantMember)
        .where(
            TenantMember.user_id == actor.user.id,
            TenantMember.role == "owner",
            TenantMember.active.is_(True),
        )
    )
    if (owned or 0) >= 5:
        raise HTTPException(429, detail={"code": "SPACE_QUOTA_EXCEEDED"})
    tenant_id = uuid4()
    set_tenant(actor.session, tenant_id)
    actor.session.add(Tenant(id=tenant_id, name=body.name))
    actor.session.flush()
    actor.session.add(TenantMember(tenant_id=tenant_id, user_id=actor.user.id, role="owner"))
    actor.session.flush()
    record(actor.session, tenant_id, actor.user.id, AuditAction.SPACE_CREATED, tenant_id)
    return {"data": {"id": str(tenant_id), "name": body.name, "role": "owner"}}


@router.get("/spaces/{tenant_id}")
def get_space(access: Annotated[SpaceAccess, Depends(member_access)]) -> dict[str, Any]:
    return {"data": {"id": str(access.tenant.id), "name": access.tenant.name, "role": access.role}}


@router.post("/spaces/{tenant_id}/members", status_code=201)
def add_member(
    body: MemberInput, access: Annotated[SpaceAccess, Depends(member_access)]
) -> dict[str, Any]:
    lock_access(access)
    require_admin(access)
    # The target UUID is an explicit admin action, not a public user directory.
    existing = access.actor.session.get(TenantMember, (access.tenant.id, body.user_id))
    if existing is not None and existing.active:
        raise HTTPException(409, detail={"code": "ALREADY_MEMBER"})
    active = access.actor.session.scalar(
        select(func.count())
        .select_from(TenantMember)
        .where(
            TenantMember.tenant_id == access.tenant.id,
            TenantMember.active.is_(True),
        )
    )
    if (active or 0) >= 100:
        raise HTTPException(429, detail={"code": "MEMBER_QUOTA_EXCEEDED"})
    if existing is not None:
        existing.active, existing.role = True, body.role
    else:
        access.actor.session.add(
            TenantMember(tenant_id=access.tenant.id, user_id=body.user_id, role=body.role)
        )
    try:
        access.actor.session.flush()
    except IntegrityError:
        raise HTTPException(400, detail={"code": "INVALID_MEMBER"}) from None
    record_event(access, AuditAction.MEMBER_ADDED, body.user_id)
    return {"data": {"user_id": str(body.user_id), "role": body.role}}


@router.delete("/spaces/{tenant_id}/members/{user_id}")
def remove_member(
    user_id: UUID, access: Annotated[SpaceAccess, Depends(member_access)]
) -> dict[str, Any]:
    lock_access(access)
    require_admin(access)
    member = access.actor.session.get(TenantMember, (access.tenant.id, user_id))
    if member is None or not member.active:
        raise HTTPException(404, detail={"code": "MEMBER_NOT_FOUND"})
    if member.role == "owner":
        raise HTTPException(403, detail={"code": "OWNER_PROTECTED"})
    # Keep historical composite FKs intact while revoking future API/Worker access.
    member.active = False
    jobs = list(
        access.actor.session.scalars(
            select(ProcessingJob)
            .where(
                ProcessingJob.tenant_id == access.tenant.id,
                ProcessingJob.requested_by == user_id,
                ProcessingJob.status.in_(["queued", "running"]),
            )
            .with_for_update()
        )
    )
    for job in jobs:
        job.status, job.error_code = "cancelled", "MEMBERSHIP_REVOKED"
        access.actor.session.execute(
            update(JobStep)
            .where(JobStep.job_id == job.id)
            .values(status="cancelled", error_code="MEMBERSHIP_REVOKED")
        )
        access.actor.session.execute(
            update(JobDispatch).where(JobDispatch.job_id == job.id).values(done=True)
        )
    record_event(access, AuditAction.MEMBER_REMOVED, user_id)
    return {"data": {"removed": True}}
