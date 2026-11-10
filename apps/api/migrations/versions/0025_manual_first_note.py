"""Allow an evidence-bound first human revision, never a fabricated AI run."""

from alembic import op

revision = "0025_manual_first_note"
down_revision = "0024_concept_cards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_note_revision_origin_binding", "note_revisions", type_="check")
    op.create_check_constraint(
        "ck_note_revision_origin_binding",
        "note_revisions",
        "(origin='ai_generated' AND run_id IS NOT NULL AND base_revision_id IS NULL "
        "AND edit_key IS NULL AND request_sha256 IS NULL) OR "
        "(origin='user_edit' AND run_id IS NULL "
        "AND (base_revision_id IS NOT NULL OR version=1) "
        "AND edit_key IS NOT NULL AND request_sha256 IS NOT NULL)",
    )
    # Existing append policy still governs AI and later human revisions. This
    # separate INSERT policy adds only version 1 and verifies the source match.
    op.execute(
        "CREATE POLICY tenant_manual_first ON note_revisions FOR INSERT WITH CHECK ("
        "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid "
        "AND created_by=NULLIF(current_setting('app.user_id',true),'')::uuid "
        "AND origin='user_edit' AND run_id IS NULL AND base_revision_id IS NULL "
        "AND version=1 AND EXISTS (SELECT 1 FROM notes n "
        "JOIN source_snapshots s ON s.tenant_id=n.tenant_id AND s.source_id=n.source_id "
        "JOIN tenant_members m ON m.tenant_id=n.tenant_id "
        "AND m.user_id=NULLIF(current_setting('app.user_id',true),'')::uuid AND m.active "
        "WHERE n.tenant_id=note_revisions.tenant_id AND n.id=note_revisions.note_id "
        "AND s.id=note_revisions.snapshot_id))"
    )


def downgrade() -> None:
    raise RuntimeError("Human first revisions require a reviewed recovery plan; no data rollback")
