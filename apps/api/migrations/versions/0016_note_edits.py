"""Append-only human note revisions, without fabricated model generation claims."""

import sqlalchemy as sa
from alembic import op

revision = "0016_note_edits"
down_revision = "0015_source_import"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows were created by the generation consumer; retain that origin.
    op.add_column(
        "note_revisions",
        sa.Column("origin", sa.String(32), nullable=False, server_default="ai_generated"),
    )
    op.add_column("note_revisions", sa.Column("base_revision_id", sa.Uuid()))
    op.add_column("note_revisions", sa.Column("edit_key", sa.Uuid()))
    op.add_column("note_revisions", sa.Column("request_sha256", sa.String(64)))
    op.alter_column("note_revisions", "run_id", existing_type=sa.Uuid(), nullable=True)
    op.create_unique_constraint("uq_note_edit_key", "note_revisions", ["tenant_id", "edit_key"])
    op.create_unique_constraint(
        "uq_note_revision_base_scope",
        "note_revisions",
        ["tenant_id", "note_id", "snapshot_id", "id"],
    )
    op.create_foreign_key(
        "fk_note_revision_base_scope",
        "note_revisions",
        "note_revisions",
        ["tenant_id", "note_id", "snapshot_id", "base_revision_id"],
        ["tenant_id", "note_id", "snapshot_id", "id"],
    )
    op.create_check_constraint(
        "ck_note_revision_origin_binding",
        "note_revisions",
        "(origin='ai_generated' AND run_id IS NOT NULL AND base_revision_id IS NULL "
        "AND edit_key IS NULL AND request_sha256 IS NULL) OR "
        "(origin='user_edit' AND run_id IS NULL AND base_revision_id IS NOT NULL "
        "AND edit_key IS NOT NULL AND request_sha256 IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_note_edit_request_digest",
        "note_revisions",
        "request_sha256 IS NULL OR request_sha256 ~ '^[0-9a-f]{64}$'",
    )
    # Read and append grants remain unchanged. Human history may not be updated
    # or deleted, and no pending/succeeded AI run is invented for an edit.
    op.execute("DROP POLICY tenant_append ON note_revisions")
    tenant = "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid"
    actor = "NULLIF(current_setting('app.user_id',true),'')::uuid"
    op.execute(
        "CREATE POLICY tenant_append ON note_revisions FOR INSERT WITH CHECK "
        f"({tenant} AND created_by={actor} AND ("
        "(origin='ai_generated' AND EXISTS (SELECT 1 FROM note_runs r "
        "JOIN source_snapshots s ON s.tenant_id=r.tenant_id AND s.id=r.snapshot_id "
        "JOIN notes n ON n.tenant_id=s.tenant_id AND n.source_id=s.source_id "
        "WHERE r.tenant_id=note_revisions.tenant_id AND r.id=note_revisions.run_id "
        "AND r.snapshot_id=note_revisions.snapshot_id AND r.created_by=note_revisions.created_by "
        "AND r.status='pending' AND n.id=note_revisions.note_id)) OR "
        "(origin='user_edit' AND EXISTS (SELECT 1 FROM note_revisions b "
        "JOIN notes n ON n.tenant_id=b.tenant_id AND n.id=b.note_id "
        "JOIN source_snapshots s ON s.tenant_id=b.tenant_id AND s.id=b.snapshot_id "
        "JOIN tenant_members m ON m.tenant_id=b.tenant_id "
        f"AND m.user_id={actor} AND m.active "
        "WHERE b.tenant_id=note_revisions.tenant_id AND b.id=note_revisions.base_revision_id "
        "AND b.note_id=note_revisions.note_id AND b.snapshot_id=note_revisions.snapshot_id "
        "AND n.source_id=s.source_id AND note_revisions.version=b.version+1))))"
    )
    op.drop_constraint("audit_events_action_check", "audit_events", type_="check")
    op.create_check_constraint(
        "audit_events_action_check",
        "audit_events",
        "action IN ('space.created','member.added','member.removed','job.created',"
        "'job.cancelled','job.retried','file.uploaded','file.deleted','provider.created',"
        "'provider.disabled','provider.reencrypted','settings.updated','provider.updated',"
        "'source.batch.created','source.snapshot.created','notes.generated',"
        "'source.content.imported','notes.edited')",
    )


def downgrade() -> None:
    raise RuntimeError("Human revision rollback requires an explicitly reviewed recovery plan")
