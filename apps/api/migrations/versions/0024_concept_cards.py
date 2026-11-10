"""Immutable human concept definitions and exact same-tenant evidence mentions."""

import sqlalchemy as sa
from alembic import op

revision = "0024_concept_cards"
down_revision = "0023_topic_hierarchy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_evidence_revision_snapshot_key",
        "evidence_items",
        ["tenant_id", "revision_id", "snapshot_id", "evidence_key"],
    )
    op.create_table(
        "knowledge_concepts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("note_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("evidence_key", sa.String(4), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "note_id", "snapshot_id", "revision_id"],
            [
                "note_revisions.tenant_id",
                "note_revisions.note_id",
                "note_revisions.snapshot_id",
                "note_revisions.id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "revision_id", "snapshot_id", "evidence_key"],
            [
                "evidence_items.tenant_id",
                "evidence_items.revision_id",
                "evidence_items.snapshot_id",
                "evidence_items.evidence_key",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 60 AND octet_length(name)<=240"),
        sa.CheckConstraint(
            "length(definition) BETWEEN 1 AND 2000 AND octet_length(definition)<=8000 "
            "AND length(btrim(definition))>0"
        ),
        sa.CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$'"),
    )
    op.create_index(
        "ix_concepts_revision",
        "knowledge_concepts",
        ["tenant_id", "note_id", "revision_id", "created_at", "id"],
    )
    tenant = "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid"
    actor = "NULLIF(current_setting('app.user_id',true),'')::uuid"
    member = (
        "EXISTS (SELECT 1 FROM tenant_members m "
        "WHERE m.tenant_id=knowledge_concepts.tenant_id AND m.user_id=" + actor + " AND m.active)"
    )
    op.execute("ALTER TABLE knowledge_concepts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge_concepts FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON knowledge_concepts FROM PUBLIC, knowledge_app")
    op.execute("GRANT SELECT, INSERT ON knowledge_concepts TO knowledge_app")
    op.execute(
        f"CREATE POLICY tenant_read ON knowledge_concepts FOR SELECT USING ({tenant} AND {member})"
    )
    op.execute(
        "CREATE POLICY tenant_append ON knowledge_concepts FOR INSERT WITH CHECK "
        f"({tenant} AND {member} AND created_by={actor})"
    )
    op.drop_constraint("audit_events_action_check", "audit_events", type_="check")
    actions = (
        "'space.created','member.added','member.removed','job.created','job.cancelled',"
        "'job.retried','file.uploaded','file.deleted','provider.created','provider.disabled',"
        "'provider.reencrypted','settings.updated','provider.updated','source.batch.created',"
        "'source.snapshot.created','notes.generated','source.content.imported','notes.edited',"
        "'organization.label_created','organization.label_attached','organization.label_removed',"
        "'organization.label_updated','notes.annotated','notes.linked','notes.unlinked',"
        "'learning.updated','organization.concept_created'"
    )
    op.create_check_constraint(
        "audit_events_action_check", "audit_events", f"action IN ({actions})"
    )


def downgrade() -> None:
    raise RuntimeError("Concept rollback requires an explicitly reviewed recovery plan")
