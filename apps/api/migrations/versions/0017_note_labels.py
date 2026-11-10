"""Manual topics/tags and reversible classification, frozen tenant-safe DDL."""

import sqlalchemy as sa
from alembic import op

revision = "0017_note_labels"
down_revision = "0016_note_edits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_labels",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("name_key", sa.String(180), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.UniqueConstraint("tenant_id", "kind", "name_key"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        sa.CheckConstraint("kind IN ('topic','tag')", name="ck_knowledge_label_kind"),
        sa.CheckConstraint(
            "length(name) BETWEEN 1 AND 60 AND octet_length(name)<=240",
            name="ck_knowledge_label_name",
        ),
        sa.CheckConstraint(
            "length(name_key) BETWEEN 1 AND 180 AND octet_length(name_key)<=720",
            name="ck_knowledge_label_key",
        ),
    )
    op.create_table(
        "note_labels",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("note_id", sa.Uuid(), nullable=False),
        sa.Column("label_id", sa.Uuid(), nullable=False),
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
        sa.UniqueConstraint("tenant_id", "note_id", "label_id"),
        sa.ForeignKeyConstraint(["tenant_id", "note_id"], ["notes.tenant_id", "notes.id"]),
        sa.ForeignKeyConstraint(
            ["tenant_id", "label_id"], ["knowledge_labels.tenant_id", "knowledge_labels.id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        sa.CheckConstraint("updated_at>=created_at", name="ck_note_label_timestamps"),
    )
    op.create_index(
        "ix_note_labels_lookup", "note_labels", ["tenant_id", "label_id", "active", "note_id"]
    )
    tenant = "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid"
    actor = "NULLIF(current_setting('app.user_id',true),'')::uuid"
    for table in ("knowledge_labels", "note_labels"):
        member = (
            "EXISTS (SELECT 1 FROM tenant_members m "
            f"WHERE m.tenant_id={table}.tenant_id AND m.user_id={actor} AND m.active)"
        )
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_read ON {table} FOR SELECT USING ({tenant} AND {member})")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC, knowledge_app")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO knowledge_app")
        binding = f" AND updated_by={actor} AND active" if table == "note_labels" else ""
        op.execute(
            f"CREATE POLICY tenant_append ON {table} FOR INSERT WITH CHECK "
            f"({tenant} AND {member} AND created_by={actor}{binding})"
        )
    member = (
        "EXISTS (SELECT 1 FROM tenant_members m "
        f"WHERE m.tenant_id=note_labels.tenant_id AND m.user_id={actor} AND m.active)"
    )
    # Labels and association identity are immutable; only a reversible active
    # state and its real member actor/time may change. History stays in audit.
    op.execute("GRANT UPDATE (active,updated_by,updated_at) ON note_labels TO knowledge_app")
    op.execute(
        "CREATE POLICY tenant_change ON note_labels FOR UPDATE "
        f"USING ({tenant} AND {member}) "
        f"WITH CHECK ({tenant} AND {member} AND updated_by={actor})"
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
        "'organization.label_attached','organization.label_removed')",
    )


def downgrade() -> None:
    raise RuntimeError("Classification rollback requires an explicitly reviewed recovery plan")
