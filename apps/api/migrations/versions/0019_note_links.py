"""Reversible undirected manual note links with frozen tenant-safe DDL."""

import sqlalchemy as sa
from alembic import op

revision = "0019_note_links"
down_revision = "0018_note_annotations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "note_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("left_note_id", sa.Uuid(), nullable=False),
        sa.Column("right_note_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.UniqueConstraint("tenant_id", "left_note_id", "right_note_id"),
        sa.ForeignKeyConstraint(["tenant_id", "left_note_id"], ["notes.tenant_id", "notes.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "right_note_id"], ["notes.tenant_id", "notes.id"]),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        sa.CheckConstraint("left_note_id < right_note_id"),
        sa.CheckConstraint("updated_at >= created_at"),
    )
    op.create_index(
        "ix_note_links_left",
        "note_links",
        ["tenant_id", "left_note_id", "active", "right_note_id"],
    )
    op.create_index(
        "ix_note_links_right",
        "note_links",
        ["tenant_id", "right_note_id", "active", "left_note_id"],
    )
    tenant = "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid"
    actor = "NULLIF(current_setting('app.user_id',true),'')::uuid"
    member = (
        "EXISTS (SELECT 1 FROM tenant_members m WHERE m.tenant_id=note_links.tenant_id "
        f"AND m.user_id={actor} AND m.active)"
    )
    op.execute("ALTER TABLE note_links ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE note_links FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON note_links FROM PUBLIC, knowledge_app")
    op.execute("GRANT SELECT, INSERT ON note_links TO knowledge_app")
    op.execute("GRANT UPDATE (active,updated_by,updated_at) ON note_links TO knowledge_app")
    op.execute(f"CREATE POLICY tenant_read ON note_links FOR SELECT USING ({tenant} AND {member})")
    op.execute(
        "CREATE POLICY tenant_append ON note_links FOR INSERT WITH CHECK "
        f"({tenant} AND {member} AND created_by={actor} AND updated_by={actor} AND active)"
    )
    op.execute(
        "CREATE POLICY tenant_change ON note_links FOR UPDATE "
        f"USING ({tenant} AND {member}) WITH CHECK ({tenant} AND {member} AND updated_by={actor})"
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
        "'organization.label_attached','organization.label_removed','notes.annotated',"
        "'notes.linked','notes.unlinked')",
    )


def downgrade() -> None:
    raise RuntimeError("Note link rollback requires an explicitly reviewed recovery plan")
