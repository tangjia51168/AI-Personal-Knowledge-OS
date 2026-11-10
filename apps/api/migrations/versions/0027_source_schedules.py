"""Explicit public-source refresh plans; no platform accounts or AI automation."""

import sqlalchemy as sa
from alembic import op

revision = "0027_source_schedules"
down_revision = "0026_research_answers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_schedules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.UniqueConstraint("tenant_id", "id", "created_by"),
        sa.UniqueConstraint("tenant_id", "created_by", "source_id"),
        sa.ForeignKeyConstraint(["tenant_id", "source_id"], ["sources.tenant_id", "sources.id"]),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        sa.CheckConstraint("interval_minutes BETWEEN 360 AND 10080 AND version>=1"),
    )
    op.create_table(
        "source_schedule_ticks",
        sa.Column("schedule_id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "schedule_id", "requested_by"],
            ["source_schedules.tenant_id", "source_schedules.id", "source_schedules.created_by"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
    )
    op.create_index("ix_source_schedule_due", "source_schedule_ticks", ["enabled", "due_at"])
    # UUID/clock-only discovery, exactly like job_dispatches; never a grant.
    op.execute("REVOKE ALL ON source_schedule_ticks FROM PUBLIC,knowledge_app")
    op.execute("GRANT SELECT,INSERT,UPDATE ON source_schedule_ticks TO knowledge_app")
    op.create_table(
        "source_schedule_jobs",
        sa.Column("tenant_id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), primary_key=True),
        sa.Column("schedule_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "schedule_id"], ["source_schedules.tenant_id", "source_schedules.id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"], ["processing_jobs.tenant_id", "processing_jobs.id"]
        ),
    )
    tenant = "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid"
    actor = "NULLIF(current_setting('app.user_id',true),'')::uuid"
    member = (
        "EXISTS (SELECT 1 FROM tenant_members m WHERE m.tenant_id=source_schedules.tenant_id "
        f"AND m.user_id={actor} AND m.active)"
    )
    for table in ("source_schedules", "source_schedule_jobs"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC,knowledge_app")
        op.execute(f"GRANT SELECT,INSERT ON {table} TO knowledge_app")
    own = f"{tenant} AND created_by={actor} AND {member}"
    op.execute(f"CREATE POLICY own_read ON source_schedules FOR SELECT USING ({own})")
    op.execute(f"CREATE POLICY own_append ON source_schedules FOR INSERT WITH CHECK ({own})")
    op.execute(
        f"CREATE POLICY own_update ON source_schedules FOR UPDATE USING ({own}) WITH CHECK ({own})"
    )
    op.execute(
        "GRANT UPDATE(active,version,next_run_at,last_error) ON source_schedules TO knowledge_app"
    )
    allowed = (
        "EXISTS (SELECT 1 FROM source_schedules s WHERE s.tenant_id=source_schedule_jobs.tenant_id "
        "AND s.id=source_schedule_jobs.schedule_id)"
    )
    bound_job = (
        "EXISTS (SELECT 1 FROM processing_jobs j JOIN source_schedules s ON "
        "s.tenant_id=j.tenant_id AND s.created_by=j.requested_by WHERE "
        "s.id=source_schedule_jobs.schedule_id AND j.id=source_schedule_jobs.job_id "
        "AND j.tenant_id=source_schedule_jobs.tenant_id AND j.kind='source_collect' "
        "AND j.payload->>'source_id'=s.source_id::text)"
    )
    op.execute(
        f"CREATE POLICY own_read ON source_schedule_jobs FOR SELECT USING ({tenant} AND {allowed})"
    )
    op.execute(
        "CREATE POLICY own_append ON source_schedule_jobs FOR INSERT WITH CHECK "
        f"({tenant} AND {allowed} AND {bound_job})"
    )
    op.drop_constraint("audit_events_action_check", "audit_events", type_="check")
    actions = (
        "'space.created','member.added','member.removed','job.created','job.cancelled',"
        "'job.retried','file.uploaded','file.deleted','provider.created','provider.disabled',"
        "'provider.reencrypted','settings.updated','provider.updated','source.batch.created',"
        "'source.snapshot.created','notes.generated','source.content.imported','notes.edited',"
        "'organization.label_created','organization.label_attached','organization.label_removed',"
        "'organization.label_updated','notes.annotated','notes.linked','notes.unlinked',"
        "'learning.updated','organization.concept_created','research.created','research.finished',"
        "'source.schedule_created','source.schedule_updated'"
    )
    op.create_check_constraint(
        "audit_events_action_check", "audit_events", f"action IN ({actions})"
    )


def downgrade() -> None:
    raise RuntimeError("Schedule rollback requires a reviewed recovery plan")
