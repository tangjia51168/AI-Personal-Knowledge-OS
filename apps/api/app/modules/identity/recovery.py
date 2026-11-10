"""Single-use email actions; tokens stay hashed in DB and never enter queue/logs."""

import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.session import get_engine
from app.integrations.mail import MailDeliveryError, send_account_link
from app.modules.identity.access import set_user, token_digest
from app.modules.identity.models import AuthActionToken, AuthIdentity, AuthSession
from app.modules.identity.passwords import hash_password
from app.modules.identity.security_events import SecurityAction, record_security_event

Purpose = Literal["verify_email", "reset_password"]
logger = logging.getLogger("knowledge_os.identity")


def locked_user(session: Session, user_id: UUID) -> User | None:
    set_user(session, user_id)
    return session.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )


def create_action(session: Session, user: User, purpose: Purpose) -> str:
    # Caller holds the user's NO KEY UPDATE lock (or just inserted the new user).
    session.execute(
        delete(AuthActionToken).where(
            AuthActionToken.user_id == user.id, AuthActionToken.purpose == purpose
        )
    )
    token = secrets.token_urlsafe(32)
    session.add(
        AuthActionToken(
            token_hash=token_digest(token),
            user_id=user.id,
            purpose=purpose,
            expires_at=datetime.now(UTC)
            + timedelta(minutes=30 if purpose == "verify_email" else 15),
        )
    )
    return token


def deliver_action(email: str, purpose: Purpose, token: str) -> None:
    try:
        send_account_link(email, purpose, token)
    except MailDeliveryError:
        # Same response for delivery failure and unknown account. The user can
        # request another link; no secret or provider error is logged/returned.
        logger.warning("account_mail_delivery_failed")


def request_action(email: str, purpose: Purpose) -> None:
    recipient, token = None, None
    with Session(get_engine()) as session, session.begin():
        identity = session.get(AuthIdentity, email)
        user = locked_user(session, identity.user_id) if identity else None
        if (
            user
            and user.status == "active"
            and not (purpose == "verify_email" and user.email_verified)
        ):
            recipient = user.email
            token = create_action(session, user, purpose)
    # DB commit precedes SMTP. No database locks or transactions across network I/O.
    if recipient and token:
        deliver_action(recipient, purpose, token)


def consume_action(token: str, purpose: Purpose, password: str | None = None) -> None:
    digest = token_digest(token)
    with Session(get_engine()) as session, session.begin():
        found = session.get(AuthActionToken, digest)
        user = locked_user(session, found.user_id) if found else None
        # Re-read after the user lock: concurrent consumption/resend/reset can
        # have deleted this token while this transaction waited for that lock.
        found = session.scalar(
            select(AuthActionToken)
            .where(AuthActionToken.token_hash == digest)
            .execution_options(populate_existing=True)
        )
        if (
            user is None
            or user.status != "active"
            or found is None
            or found.user_id != user.id
            or found.purpose != purpose
            or found.expires_at <= datetime.now(UTC)
        ):
            raise HTTPException(400, detail={"code": "ACTION_LINK_INVALID_OR_EXPIRED"})
        if purpose == "verify_email":
            user.email_verified = True
            record_security_event(session, user.id, SecurityAction.EMAIL_VERIFIED)
            session.execute(
                delete(AuthActionToken).where(
                    AuthActionToken.user_id == user.id, AuthActionToken.purpose == purpose
                )
            )
        else:
            if password is None:
                raise HTTPException(400, detail={"code": "PASSWORD_REQUIRED"})
            user.password_hash = hash_password(password)
            session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
            session.execute(delete(AuthActionToken).where(AuthActionToken.user_id == user.id))
            record_security_event(session, user.id, SecurityAction.PASSWORD_RESET)
