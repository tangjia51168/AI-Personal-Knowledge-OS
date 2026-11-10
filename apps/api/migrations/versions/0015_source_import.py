"""Local authorized content imports with immutable, task-bound provenance."""

import sqlalchemy as sa
from alembic import op

revision = "0015_source_import"
down_revision = "0014_knowledge_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing snapshots came exclusively from the collection consumer. This
    # additive constant default preserves their history without reinterpreting it.
    op.add_column(
        "source_snapshots",
        sa.Column(
            "provenance", sa.String(32), nullable=False, server_default="platform_collection"
        ),
    )
    op.create_check_constraint(
        "ck_snapshot_provenance",
        "source_snapshots",
        "provenance IN ('platform_collection','user_import')",
    )
    op.create_check_constraint(
        "ck_snapshot_import_scope",
        "source_snapshots",
        "provenance != 'user_import' OR "
        "(platform='user_import' AND content_kind IN ('article','transcript'))",
    )
    op.execute("DROP POLICY tenant_append ON source_snapshots")
    tenant = "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid"
    actor = "created_by=NULLIF(current_setting('app.user_id',true),'')::uuid"
    # Both consumers must bind the snapshot to the source actually named by the
    # persisted task, with matching actor, current tenant and a running task.
    # Imported text cannot claim a platform response or an unrelated source URL.
    op.execute(
        "CREATE POLICY tenant_append ON source_snapshots FOR INSERT WITH CHECK "
        f"({tenant} AND {actor} "
        "AND EXISTS (SELECT 1 FROM processing_jobs j WHERE j.tenant_id=source_snapshots.tenant_id "
        "AND j.id=source_snapshots.job_id AND j.requested_by=source_snapshots.created_by "
        "AND j.payload->>'source_id'=source_snapshots.source_id::text AND j.status='running' "
        "AND ((j.kind='source_collect' AND source_snapshots.provenance='platform_collection') "
        "OR (j.kind='source_import' AND source_snapshots.provenance='user_import' "
        "AND EXISTS (SELECT 1 FROM sources s WHERE s.tenant_id=source_snapshots.tenant_id "
        "AND s.id=source_snapshots.source_id AND s.status='accepted' "
        "AND s.canonical_url=source_snapshots.fetched_url)))))"
    )
    op.drop_constraint("audit_events_action_check", "audit_events", type_="check")
    op.create_check_constraint(
        "audit_events_action_check",
        "audit_events",
        "action IN ('space.created','member.added','member.removed','job.created',"
        "'job.cancelled','job.retried','file.uploaded','file.deleted','provider.created',"
        "'provider.disabled','provider.reencrypted','settings.updated','provider.updated',"
        "'source.batch.created','source.snapshot.created','notes.generated','source.content.imported')",
    )


def downgrade() -> None:
    raise RuntimeError(
        "Imported content history rollback requires an explicitly reviewed recovery plan"
    )
