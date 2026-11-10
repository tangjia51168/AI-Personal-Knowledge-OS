from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class AuthIdentity(Base):
    __tablename__ = "auth_identities"
    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), unique=True)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuthActionToken(Base):
    __tablename__ = "auth_action_tokens"
    __table_args__ = (
        CheckConstraint("purpose IN ('verify_email', 'reset_password')"),
        Index("ix_auth_actions_user_purpose", "user_id", "purpose"),
        Index("ix_auth_actions_expiry", "expires_at"),
    )

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    purpose: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
