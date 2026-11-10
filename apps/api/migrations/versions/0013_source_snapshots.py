"""Immutable collected source versions, with actor and composite ownership checks."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_source_snapshots"
down_revision = "0012_source_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("fetched_url", sa.String(2048), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("content_kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("author", sa.String(200), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("raw_sha256", sa.String(64), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("limitations", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.UniqueConstraint("tenant_id", "source_id", "version"),
        sa.UniqueConstraint("tenant_id", "job_id"),
        sa.ForeignKeyConstraint(["tenant_id", "source_id"], ["sources.tenant_id", "sources.id"]),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"], ["processing_jobs.tenant_id", "processing_jobs.id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        sa.CheckConstraint("version >= 1", name="ck_snapshot_version"),
        sa.CheckConstraint(
            "content_kind IN ('article','metadata','transcript')", name="ck_snapshot_kind"
        ),
        sa.CheckConstraint("length(body_text) BETWEEN 1 AND 60000", name="ck_snapshot_body"),
        sa.CheckConstraint("raw_sha256 ~ '^[0-9a-f]{64}$'", name="ck_snapshot_digest"),
        sa.CheckConstraint(
            "jsonb_typeof(details)='object' AND octet_length(details::text)<=16384",
            name="ck_snapshot_details",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(limitations)='array' AND jsonb_array_length(limitations)<=20",
            name="ck_snapshot_limitations",
        ),
    )
    op.create_index(
        "ix_snapshots_source_created",
        "source_snapshots",
        ["tenant_id", "source_id", "created_at", "id"],
    )
    op.execute("ALTER TABLE source_snapshots ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE source_snapshots FORCE ROW LEVEL SECURITY")
    tenant = "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid"
    actor = "created_by=NULLIF(current_setting('app.user_id',true),'')::uuid"
    op.execute(f"CREATE POLICY tenant_read ON source_snapshots FOR SELECT USING ({tenant})")
    op.execute(
        "CREATE POLICY tenant_append ON source_snapshots FOR INSERT WITH CHECK "
        f"({tenant} AND {actor} "
        "AND EXISTS (SELECT 1 FROM processing_jobs j WHERE j.tenant_id=source_snapshots.tenant_id "
        "AND j.id=source_snapshots.job_id AND j.requested_by=source_snapshots.created_by "
        "AND j.kind='source_collect' AND j.status='running'))"
    )
    op.execute("REVOKE ALL ON source_snapshots FROM PUBLIC, knowledge_app")
    op.execute("GRANT SELECT, INSERT ON source_snapshots TO knowledge_app")
    op.drop_constraint("audit_events_action_check", "audit_events", type_="check")
    op.create_check_constraint(
        "audit_events_action_check",
        "audit_events",
        "action IN ('space.created','member.added','member.removed','job.created',"
        "'job.cancelled','job.retried','file.uploaded','file.deleted','provider.created',"
        "'provider.disabled','provider.reencrypted','settings.updated','provider.updated',"
        "'source.batch.created','source.snapshot.created')",
    )


def downgrade() -> None:
    raise RuntimeError("Source history rollback requires an explicitly reviewed recovery plan")
