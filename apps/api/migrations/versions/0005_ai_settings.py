"""Encrypted provider settings and durable usage journal, frozen tenant DDL."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_ai_settings"
down_revision = "0004_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_providers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("endpoint_name", sa.String(64)),
        sa.Column("encrypted_key", sa.Text()),
        sa.Column("key_version", sa.String(32)),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("consent", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.CheckConstraint("kind IN ('openai', 'ollama')"),
    )
    op.create_table(
        "ai_usage",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("cost_usd", sa.Numeric(18, 8)),
        sa.Column("error_code", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "provider_id"], ["ai_providers.tenant_id", "ai_providers.id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
    )
    op.create_table(
        "user_settings",
        sa.Column("tenant_id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("preferences", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
    )
    for name in ("ai_providers", "ai_usage", "user_settings"):
        op.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY")
        own = (
            " AND user_id=NULLIF(current_setting('app.user_id',true),'')::uuid"
            if name == "user_settings"
            else ""
        )
        expr = "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid" + own
        op.execute(f"CREATE POLICY tenant_isolation ON {name} USING ({expr}) WITH CHECK ({expr})")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {name} TO knowledge_app")


def downgrade() -> None:
    raise RuntimeError("Credential rollback requires an explicitly reviewed recovery plan")
