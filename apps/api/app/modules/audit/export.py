"""Bounded audit snapshot for an authenticated administrator; no privileged connection."""

import time
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEvent
from app.modules.identity.access import authenticate_session, member_access, require_admin
from app.modules.identity.security_events import AuthSecurityEvent

MAX_EXPORT_ROWS = 10_000
MAX_EXPORT_SECONDS = 30


def audit_snapshot(session: Session, token: str, tenant_id: UUID) -> dict[str, Any]:
    """Must be the first operation in a fresh transaction owned by the caller.

    Permissions and both datasets share one read-only PostgreSQL snapshot.
    Revocation after this snapshot affects later exports, not already read data.
    """
    started = time.monotonic()
    session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    session.execute(text("SET LOCAL statement_timeout = '10s'"))
    session.execute(text("SET LOCAL idle_in_transaction_session_timeout = '10s'"))
    session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    roles = session.execute(
        text(
            "SELECT rolsuper,rolbypassrls FROM pg_roles "
            "WHERE rolname IN (current_user,session_user)"
        )
    ).all()
    if not roles or any(role.rolsuper or role.rolbypassrls for role in roles):
        raise RuntimeError("Audit export requires an unprivileged database login")
    actor = authenticate_session(session, token)
    access = member_access(tenant_id, actor)
    require_admin(access)
    snapshot_at: datetime = session.execute(text("SELECT transaction_timestamp()")).scalar_one()
    business = session.execute(
        select(
            AuditEvent.id,
            AuditEvent.actor_id,
            AuditEvent.action,
            AuditEvent.resource_id,
            AuditEvent.created_at,
        )
        .where(AuditEvent.tenant_id == tenant_id)
        .order_by(AuditEvent.created_at, AuditEvent.id)
        .limit(MAX_EXPORT_ROWS + 1)
    ).all()
    if len(business) > MAX_EXPORT_ROWS:
        raise RuntimeError("Audit export row limit exceeded")
    security = session.execute(
        select(AuthSecurityEvent.id, AuthSecurityEvent.action, AuthSecurityEvent.created_at)
        .where(AuthSecurityEvent.user_id == actor.user.id)
        .order_by(AuthSecurityEvent.created_at, AuthSecurityEvent.id)
        .limit(MAX_EXPORT_ROWS - len(business) + 1)
    ).all()
    if len(business) + len(security) > MAX_EXPORT_ROWS:
        raise RuntimeError("Audit export row limit exceeded")
    if time.monotonic() - started > MAX_EXPORT_SECONDS:
        raise RuntimeError("Audit export time limit exceeded")
    return {
        "format": 1,
        "scope": "space-business-and-own-account",
        "consistency": "postgresql-repeatable-read",
        "snapshot_at": snapshot_at.isoformat(),
        "tenant_id": str(tenant_id),
        "user_id": str(actor.user.id),
        "business_events": [
            {
                "id": str(row.id),
                "actor_id": str(row.actor_id),
                "action": row.action,
                "resource_id": str(row.resource_id),
                "created_at": row.created_at.isoformat(),
            }
            for row in business
        ],
        "account_events": [
            {"id": str(row.id), "action": row.action, "created_at": row.created_at.isoformat()}
            for row in security
        ],
    }
