"""Append-only manual paragraph annotations with frozen tenant-safe DDL."""

import sqlalchemy as sa
from alembic import op

revision = "0018_note_annotations"
down_revision = "0017_note_labels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "note_annotations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("note_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("item_index", sa.Integer(), nullable=False),
        sa.Column("quote", sa.String(1200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
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
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        sa.CheckConstraint(
            "kind IN ('overview','core_claims','steps','examples','numbers','takeaways')"
        ),
        sa.CheckConstraint("item_index BETWEEN 0 AND 5 AND (kind<>'overview' OR item_index=0)"),
        sa.CheckConstraint("length(quote) BETWEEN 1 AND 1200 AND octet_length(quote)<=4800"),
        sa.CheckConstraint(
            "length(body) BETWEEN 1 AND 2000 AND octet_length(body)<=8000 AND length(btrim(body))>0"
        ),
        sa.CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$'"),
    )
    op.create_index(
        "ix_note_annotations_page",
        "note_annotations",
        ["tenant_id", "note_id", "revision_id", "created_at", "id"],
    )
    tenant = "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid"
    actor = "NULLIF(current_setting('app.user_id',true),'')::uuid"
    member = (
        "EXISTS (SELECT 1 FROM tenant_members m "
        "WHERE m.tenant_id=note_annotations.tenant_id AND m.user_id=" + actor + " AND m.active)"
    )
    anchor = """EXISTS (SELECT 1 FROM note_revisions r
        WHERE r.tenant_id=note_annotations.tenant_id AND r.note_id=note_annotations.note_id
        AND r.snapshot_id=note_annotations.snapshot_id AND r.id=note_annotations.revision_id
        AND CASE WHEN note_annotations.kind='overview' THEN r.document->'overview'->>'text'
        ELSE (SELECT entry->'items'->note_annotations.item_index->>'text'
              FROM jsonb_array_elements(r.document->'sections') entry
              WHERE entry->>'kind'=note_annotations.kind LIMIT 1)
        END=note_annotations.quote)"""
    op.execute("ALTER TABLE note_annotations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE note_annotations FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON note_annotations FROM PUBLIC, knowledge_app")
    op.execute("GRANT SELECT, INSERT ON note_annotations TO knowledge_app")
    op.execute(
        f"CREATE POLICY tenant_read ON note_annotations FOR SELECT USING ({tenant} AND {member})"
    )
    op.execute(
        "CREATE POLICY tenant_append ON note_annotations FOR INSERT WITH CHECK "
        f"({tenant} AND {member} AND created_by={actor} AND {anchor})"
    )
    op.drop_constraint("audit_events_action_check", "audit_events", type_="check")
    op.create_check_constraint(
        "audit_events_action_check",
        "audit_events",
        "action IN ('space.created','member.added','member.removed','job.created',"
        "'job.cancelled','job.retried','file.uploaded','file.deleted','provider.created',"
        "'provider.disabled','provider.reencrypted','settings.updated','provider.updated',"
        "'source.batch.created','source.snapshot.created','notes.generated',"
        "'source.content.imported','notes.edited','organization.label_created',"
        "'organization.label_attached','organization.label_removed','notes.annotated')",
    )


def downgrade() -> None:
    raise RuntimeError("Annotation rollback requires an explicitly reviewed recovery plan")
