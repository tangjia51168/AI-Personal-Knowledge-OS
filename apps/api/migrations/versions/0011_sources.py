"""Tenant-scoped source intake batches and URL candidates."""

import sqlalchemy as sa
from alembic import op

revision = "0011_sources"
down_revision = "0010_auth_security_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        sa.CheckConstraint(
            "accepted_count >= 0 AND rejected_count >= 0 AND "
            "accepted_count + rejected_count BETWEEN 1 AND 20",
            name="ck_source_batch_counts",
        ),
        sa.CheckConstraint("input_sha256 ~ '^[0-9a-f]{64}$'", name="ck_source_batch_digest"),
    )
    op.create_index(
        "ix_source_batches_tenant_created", "source_batches", ["tenant_id", "created_at", "id"]
    )
    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("original_url", sa.String(2048), nullable=False),
        sa.Column("canonical_url", sa.String(2048)),
        sa.Column("canonical_sha256", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("rejection_code", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.UniqueConstraint("tenant_id", "canonical_sha256"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "batch_id"], ["source_batches.tenant_id", "source_batches.id"]
        ),
        sa.CheckConstraint("status IN ('accepted','rejected')", name="ck_source_status"),
        sa.CheckConstraint(
            "(status='accepted' AND canonical_url IS NOT NULL AND canonical_sha256 IS NOT NULL "
            "AND canonical_sha256 ~ '^[0-9a-f]{64}$' AND rejection_code IS NULL) OR "
            "(status='rejected' AND canonical_url IS NULL AND canonical_sha256 IS NULL "
            "AND rejection_code IS NOT NULL)",
            name="ck_source_result",
        ),
    )
    op.create_index("ix_sources_tenant_created", "sources", ["tenant_id", "created_at", "id"])
    tenant = "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid"
    actor = "NULLIF(current_setting('app.user_id',true),'')::uuid"
    for table in ("source_batches", "sources"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_read ON {table} FOR SELECT USING ({tenant})")
        subject = (
            f"created_by={actor}"
            if table == "source_batches"
            else (
                "EXISTS (SELECT 1 FROM source_batches b WHERE b.tenant_id=sources.tenant_id "
                f"AND b.id=sources.batch_id AND b.created_by={actor})"
            )
        )
        op.execute(
            f"CREATE POLICY tenant_append ON {table} FOR INSERT WITH CHECK ({tenant} AND {subject})"
        )
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC, knowledge_app")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO knowledge_app")


def downgrade() -> None:
    raise RuntimeError("Source retention rollback requires an explicitly reviewed recovery plan")
