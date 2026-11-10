"""Account security history; stable actions only, never request data or secrets."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    select,
    text,
    tuple_,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.models import Base
from app.modules.identity.access import Actor, authenticated


class SecurityAction(StrEnum):
    ACCOUNT_REGISTERED = "account.registered"
    LOGIN_SUCCEEDED = "login.succeeded"
    SESSION_LOGGED_OUT = "session.logged_out"
    SESSION_ALL_REVOKED = "session.all_revoked"
    EMAIL_VERIFIED = "email.verified"
    PASSWORD_RESET = "password.reset"
    LOGIN_FAILED = "login.failed"
    LOGIN_UNVERIFIED = "login.unverified"


FAILURE_ACTIONS = frozenset({SecurityAction.LOGIN_FAILED, SecurityAction.LOGIN_UNVERIFIED})
BUCKET = "date_bin(interval '15 minutes', now(), timestamptz '2000-01-01 00:00:00+00')"


class AuthSecurityEvent(Base):
    __tablename__ = "auth_security_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('account.registered','login.succeeded','session.logged_out',"
            "'session.all_revoked','email.verified','password.reset','login.failed','login.unverified')",
            name="ck_auth_security_action",
        ),
        CheckConstraint(
            "(action IN ('login.failed','login.unverified') AND bucket_start IS NOT NULL) OR "
            "(action NOT IN ('login.failed','login.unverified') AND bucket_start IS NULL)",
            name="ck_auth_security_bucket_kind",
        ),
        CheckConstraint(
            "user_id IS NOT NULL OR action='login.failed'", name="ck_auth_security_subject"
        ),
        CheckConstraint(
            "bucket_start IS NULL OR bucket_start=date_bin(interval '15 minutes',created_at,"
            "timestamptz '2000-01-01 00:00:00+00')",
            name="ck_auth_security_bucket_time",
        ),
        Index("ix_auth_security_user_cursor", "user_id", "created_at", "id"),
        Index(
            "uq_auth_security_failure_bucket",
            "user_id",
            "action",
            "bucket_start",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("bucket_start IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    bucket_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def record_security_event(session: Session, user_id: UUID, action: SecurityAction) -> None:
    """Join a successful identity transaction; callers never commit independently."""
    if not isinstance(user_id, UUID) or not isinstance(action, SecurityAction):
        raise ValueError("Invalid account security event")
    if action in FAILURE_ACTIONS:
        raise ValueError("Login failures require a deduplicated observation")
    session.add(AuthSecurityEvent(id=uuid4(), user_id=user_id, action=action.value))


def record_login_failure(session: Session, user_id: UUID | None, action: SecurityAction) -> None:
    """One observation per subject/action/server window, not an exact attempt count."""
    if not isinstance(action, SecurityAction) or action not in FAILURE_ACTIONS:
        raise ValueError("Invalid login failure observation")
    if user_id is not None and not isinstance(user_id, UUID):
        raise ValueError("Invalid account security subject")
    if user_id is None and action != SecurityAction.LOGIN_FAILED:
        raise ValueError("Anonymous observation must be a failed login")
    # No RETURNING or explicit conflict target: the partial index predicate
    # would require SELECT RLS for an anonymous row that must stay write-only.
    # The server-generated UUID and failure-bucket index are the only uniques.
    statement = (
        insert(AuthSecurityEvent)
        .inline()
        .values(id=uuid4(), user_id=user_id, action=action.value, bucket_start=text(BUCKET))
        .on_conflict_do_nothing()
    )
    session.execute(statement)


router = APIRouter(prefix="/api/v1/auth/security-events", tags=["identity-security"])


@router.get("")
def list_security_events(
    actor: Annotated[Actor, Depends(authenticated, scope="function")],
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict[str, Any]:
    statement = select(AuthSecurityEvent).where(AuthSecurityEvent.user_id == actor.user.id)
    if cursor is not None:
        anchor = actor.session.scalar(statement.where(AuthSecurityEvent.id == cursor))
        if anchor is None:
            raise HTTPException(404, detail={"code": "SECURITY_CURSOR_NOT_FOUND"})
        statement = statement.where(
            tuple_(AuthSecurityEvent.created_at, AuthSecurityEvent.id)
            < (anchor.created_at, anchor.id)
        )
    rows = list(
        actor.session.scalars(
            statement.order_by(
                AuthSecurityEvent.created_at.desc(), AuthSecurityEvent.id.desc()
            ).limit(limit + 1)
        )
    )
    page = rows[:limit]
    return {
        "data": [
            {
                "id": str(event.id),
                "action": event.action,
                "created_at": event.created_at.isoformat(),
            }
            for event in page
        ],
        "next_cursor": str(page[-1].id) if len(rows) > limit else None,
    }
