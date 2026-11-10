"""Private durable summary stages, atomically bound to actual usage reservations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028_summary_checkpoints"
down_revision = "0027_source_schedules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_usage_tenant_id", "ai_usage", ["tenant_id", "id"])
    op.create_table(
        "summary_checkpoints",
        sa.Column("tenant_id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("usage_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_sha256", sa.String(64), nullable=False),
        sa.Column("protocol", sa.String(40), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("output", postgresql.JSONB()),
        sa.Column("error_code", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["tenant_id", "run_id"], ["note_runs.tenant_id", "note_runs.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "usage_id"], ["ai_usage.tenant_id", "ai_usage.id"]),
        sa.ForeignKeyConstraint(
            ["tenant_id", "provider_id"], ["ai_providers.tenant_id", "ai_providers.id"]
        ),
        sa.UniqueConstraint("tenant_id", "usage_id"),
        sa.CheckConstraint("position BETWEEN 0 AND 8"),
        sa.CheckConstraint("prompt_sha256 ~ '^[0-9a-f]{64}$'"),
        sa.CheckConstraint("protocol='bounded-map-synthesis-v2'"),
        sa.CheckConstraint("status IN ('dispatched','succeeded','failed','unknown')"),
        sa.CheckConstraint("(status='succeeded')=(output IS NOT NULL)"),
        sa.CheckConstraint("output IS NULL OR octet_length(output::text)<=65536"),
    )
    op.execute("ALTER TABLE summary_checkpoints ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE summary_checkpoints FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON summary_checkpoints FROM PUBLIC,knowledge_app")
    op.execute("GRANT SELECT,INSERT ON summary_checkpoints TO knowledge_app")
    op.execute("GRANT UPDATE(status,output,error_code) ON summary_checkpoints TO knowledge_app")
    own = (
        "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid AND EXISTS "
        "(SELECT 1 FROM note_runs r JOIN tenant_members m ON m.tenant_id=r.tenant_id "
        "AND m.user_id=r.created_by AND m.active WHERE r.id=summary_checkpoints.run_id "
        "AND r.tenant_id=summary_checkpoints.tenant_id "
        "AND r.created_by=NULLIF(current_setting('app.user_id',true),'')::uuid"
    )
    op.execute(f"CREATE POLICY own_read ON summary_checkpoints FOR SELECT USING ({own}))")
    usage = (
        "EXISTS (SELECT 1 FROM ai_usage u WHERE u.tenant_id=summary_checkpoints.tenant_id "
        "AND u.id=summary_checkpoints.usage_id AND u.provider_id=summary_checkpoints.provider_id "
        "AND u.model=summary_checkpoints.model "
        "AND u.user_id=NULLIF(current_setting('app.user_id',true),'')::uuid "
        "AND u.status='reserved')"
    )
    op.execute(
        f"CREATE POLICY own_append ON summary_checkpoints FOR INSERT WITH CHECK "
        f"({own} AND r.status='pending') AND {usage} "
        "AND status='dispatched' AND output IS NULL AND error_code IS NULL)"
    )
    op.execute(
        f"CREATE POLICY own_finish ON summary_checkpoints FOR UPDATE USING "
        f"({own} AND r.status='pending') AND status='dispatched') "
        f"WITH CHECK ({own} AND r.status='pending') "
        "AND status IN ('succeeded','failed','unknown'))"
    )


def downgrade() -> None:
    raise RuntimeError("Durable paid stages require reviewed recovery, no destructive downgrade")
