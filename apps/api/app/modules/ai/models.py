from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKeyConstraint, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class AIProvider(Base):
    __tablename__ = "ai_providers"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(100))
    endpoint_name: Mapped[str | None] = mapped_column(String(64))
    encrypted_key: Mapped[str | None] = mapped_column(Text)
    key_version: Mapped[str | None] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(default=False)
    priority: Mapped[int]
    consent: Mapped[bool]


class AIUsage(Base):
    __tablename__ = "ai_usage"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_usage_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "provider_id"], ["ai_providers.tenant_id", "ai_providers.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    user_id: Mapped[UUID]
    provider_id: Mapped[UUID]
    model: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32))
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserSettings(Base):
    __tablename__ = "user_settings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    preferences: Mapped[dict] = mapped_column(JSONB)
