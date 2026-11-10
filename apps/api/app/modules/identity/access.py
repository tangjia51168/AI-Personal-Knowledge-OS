"""Authenticate first, then validate membership; context is transaction-local only."""

import hashlib
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import bearer
from app.db.models import Tenant, TenantMember, User
from app.db.session import get_engine
from app.modules.identity.models import AuthSession


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def set_user(session: Session, user_id: UUID) -> None:
    session.execute(text("SELECT set_config('app.user_id', :id, true)"), {"id": str(user_id)})


def set_tenant(session: Session, tenant_id: UUID) -> None:
    session.execute(text("SELECT set_config('app.tenant_id', :id, true)"), {"id": str(tenant_id)})


@dataclass
class Actor:
    session: Session
    user: User
    token_hash: str


@dataclass(frozen=True)
class ActorIdentity:
    user_id: UUID
    token_hash: str


def authenticated(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Iterator[Actor]:
    if credentials is None or len(credentials.credentials) != 43:
        raise HTTPException(401, detail={"code": "UNAUTHORIZED"})
    with Session(get_engine()) as session, session.begin():
        yield authenticate_session(session, credentials.credentials)


def authenticate_session(session: Session, token: str) -> Actor:
    """Reuse the normal session checks inside an existing caller-owned transaction."""
    if len(token) != 43:
        raise HTTPException(401, detail={"code": "UNAUTHORIZED"})
    digest = token_digest(token)
    login = session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == digest, AuthSession.expires_at > func.now()
        )
    )
    if login is None:
        raise HTTPException(401, detail={"code": "UNAUTHORIZED"})
    set_user(session, login.user_id)
    user = session.get(User, login.user_id)
    if (
        user is None
        or user.status != "active"
        or (get_settings().email_verification_required and not user.email_verified)
    ):
        raise HTTPException(401, detail={"code": "UNAUTHORIZED"})
    return Actor(session, user, digest)


@dataclass
class SpaceAccess:
    actor: Actor
    tenant: Tenant
    role: str


def member_access(
    tenant_id: UUID,
    actor: Annotated[Actor, Depends(authenticated, scope="function")],
) -> SpaceAccess:
    # Query the verified user's own membership BEFORE setting tenant context.
    membership = actor.session.scalar(
        select(TenantMember).where(
            TenantMember.tenant_id == tenant_id,
            TenantMember.user_id == actor.user.id,
            TenantMember.active.is_(True),
        )
    )
    tenant = actor.session.get(Tenant, tenant_id) if membership else None
    if membership is None or tenant is None or tenant.status != "active":
        raise HTTPException(404, detail={"code": "SPACE_NOT_FOUND"})
    set_tenant(actor.session, tenant_id)
    return SpaceAccess(actor, tenant, membership.role)


def require_admin(access: SpaceAccess) -> None:
    if access.role not in ("owner", "admin"):
        raise HTTPException(403, detail={"code": "FORBIDDEN"})


def locked_membership(session: Session, tenant_id: UUID, user_id: UUID) -> tuple[Tenant, str]:
    tenant = session.scalar(
        select(Tenant)
        .where(Tenant.id == tenant_id)
        # Membership/status changes never modify the referenced primary keys.
        # NO KEY UPDATE serializes writers while allowing FK KEY SHARE checks.
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    member = session.scalar(
        select(TenantMember)
        .where(
            TenantMember.tenant_id == tenant_id,
            TenantMember.user_id == user_id,
            TenantMember.active.is_(True),
        )
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if tenant is None or tenant.status != "active" or member is None:
        raise HTTPException(404, detail={"code": "SPACE_NOT_FOUND"})
    return tenant, member.role


def lock_access(access: SpaceAccess) -> None:
    # Call only for a single DB phase; never hold these locks across external I/O.
    access.tenant, access.role = locked_membership(
        access.actor.session, access.tenant.id, access.actor.user.id
    )


def recheck_identity(session: Session, identity: ActorIdentity) -> None:
    """Revalidate login and user after a writer's tenant lock may have waited."""
    login = session.scalar(
        select(AuthSession)
        .where(
            AuthSession.token_hash == identity.token_hash,
            AuthSession.user_id == identity.user_id,
            AuthSession.expires_at > func.clock_timestamp(),
        )
        .execution_options(populate_existing=True)
    )
    user = session.scalar(
        select(User).where(User.id == identity.user_id).execution_options(populate_existing=True)
    )
    if (
        login is None
        or user is None
        or user.status != "active"
        or (get_settings().email_verification_required and not user.email_verified)
    ):
        raise HTTPException(401, detail={"code": "UNAUTHORIZED"})


@contextmanager
def authorized_transaction(
    actor: Actor | ActorIdentity, tenant_id: UUID
) -> Iterator[tuple[Session, str]]:
    user_id = actor.user_id if isinstance(actor, ActorIdentity) else actor.user.id
    with Session(get_engine()) as session, session.begin():
        login = session.get(AuthSession, actor.token_hash)
        if login is None or login.user_id != user_id or login.expires_at.timestamp() <= time.time():
            raise HTTPException(401, detail={"code": "UNAUTHORIZED"})
        set_user(session, user_id)
        user = session.get(User, user_id)
        member = session.scalar(
            select(TenantMember).where(
                TenantMember.tenant_id == tenant_id,
                TenantMember.user_id == user_id,
                TenantMember.active.is_(True),
            )
        )
        if (
            user is None
            or user.status != "active"
            or member is None
            or (get_settings().email_verification_required and not user.email_verified)
        ):
            raise HTTPException(404, detail={"code": "SPACE_NOT_FOUND"})
        set_tenant(session, tenant_id)
        _, role = locked_membership(session, tenant_id, user_id)
        # Membership and session may have changed while the tenant lock was
        # waiting. Re-read both after the lock so a revoked actor cannot commit
        # a write that started with a valid request.
        recheck_identity(session, ActorIdentity(user_id, actor.token_hash))
        yield session, role
