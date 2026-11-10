"""Foundation identities, tenant isolation and task/storage metadata.

This migration is intentionally frozen; never import mutable ORM metadata here.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "tenant_members",
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("role", sa.String(32), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'admin', 'member')"),
    )
    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by"],
            ["tenant_members.tenant_id", "tenant_members.user_id"],
        ),
        sa.CheckConstraint("progress >= 0 AND progress <= 100"),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')"),
    )
    op.create_index("ix_jobs_tenant_status", "processing_jobs", ["tenant_id", "status"])
    op.create_table(
        "media_assets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.UniqueConstraint("tenant_id", "storage_key"),
    )
    tenant = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    for table in ("tenants", "tenant_members", "processing_jobs", "media_assets"):
        column = "id" if table == "tenants" else "tenant_id"
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY tenant_isolation ON "{table}" '
            f"USING ({column} = {tenant}) WITH CHECK ({column} = {tenant})"
        )
    # Users are global identities; restrict to the verified session user, never all users.
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users FORCE ROW LEVEL SECURITY")
    user = "NULLIF(current_setting('app.user_id', true), '')::uuid"
    op.execute(f"CREATE POLICY self_only ON users USING (id = {user}) WITH CHECK (id = {user})")
    # Fixed role is created by the database bootstrap. Application cannot create/own tables.
    op.execute("GRANT USAGE ON SCHEMA public TO knowledge_app")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "users, tenants, tenant_members, processing_jobs, media_assets TO knowledge_app"
    )
    op.execute("GRANT SELECT ON alembic_version TO knowledge_app")


def downgrade() -> None:
    for table in ("media_assets", "processing_jobs", "tenant_members", "tenants", "users"):
        op.drop_table(table)
    # Do not remove the vector extension; later migrations or other consumers may use it.
