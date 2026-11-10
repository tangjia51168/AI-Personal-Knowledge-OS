"""Per-member note reading states and reminder timestamps."""

import sqlalchemy as sa
from alembic import op

revision = "0020_learning"
down_revision = "0019_note_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "note_learning",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("note_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="unseen"),
        sa.Column("reminder_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.UniqueConstraint("tenant_id", "note_id", "user_id"),
        sa.ForeignKeyConstraint(["tenant_id", "note_id"], ["notes.tenant_id", "notes.id"]),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        sa.CheckConstraint("status IN ('unseen','seen','skimmed','deep_read')"),
    )
    op.create_index(
        "ix_note_learning_due", "note_learning", ["tenant_id", "user_id", "reminder_at", "status"]
    )
    tenant = "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid"
    actor = "NULLIF(current_setting('app.user_id',true),'')::uuid"
    member = (
        "EXISTS (SELECT 1 FROM tenant_members m "
        f"WHERE m.tenant_id=note_learning.tenant_id AND m.user_id={actor} AND m.active)"
    )
    op.execute("ALTER TABLE note_learning ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE note_learning FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON note_learning FROM PUBLIC, knowledge_app")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE (status,reminder_at,completed_at,updated_at) "
        "ON note_learning TO knowledge_app"
    )
    op.execute(
        f"CREATE POLICY tenant_read ON note_learning FOR SELECT "
        f"USING ({tenant} AND {member} AND user_id={actor})"
    )
    op.execute(
        f"CREATE POLICY tenant_write ON note_learning FOR INSERT "
        f"WITH CHECK ({tenant} AND {member} AND user_id={actor})"
    )
    op.execute(
        f"CREATE POLICY tenant_update ON note_learning FOR UPDATE "
        f"USING ({tenant} AND {member} AND user_id={actor}) "
        f"WITH CHECK ({tenant} AND {member} AND user_id={actor})"
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
        "'notes.linked','notes.unlinked','learning.updated')",
    )


def downgrade() -> None:
    raise RuntimeError("Learning rollback requires an explicitly reviewed recovery plan")
