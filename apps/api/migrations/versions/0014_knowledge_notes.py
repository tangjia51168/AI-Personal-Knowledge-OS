"""Evidence-bound knowledge notes and durable generation claims. Frozen DDL."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_knowledge_notes"
down_revision = "0013_source_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "note_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"], ["source_snapshots.tenant_id", "source_snapshots.id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        sa.CheckConstraint(
            "status IN ('pending','succeeded','failed','unknown')", name="ck_note_run_status"
        ),
    )
    op.create_index("ix_note_runs_created", "note_runs", ["tenant_id", "created_at", "id"])
    op.create_table(
        "notes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.UniqueConstraint("tenant_id", "source_id"),
        sa.ForeignKeyConstraint(["tenant_id", "source_id"], ["sources.tenant_id", "sources.id"]),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
    )
    op.create_index("ix_notes_created", "notes", ["tenant_id", "created_at", "id"])
    op.create_table(
        "note_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("note_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.UniqueConstraint("tenant_id", "note_id", "version"),
        sa.UniqueConstraint("tenant_id", "run_id"),
        sa.ForeignKeyConstraint(["tenant_id", "note_id"], ["notes.tenant_id", "notes.id"]),
        sa.ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"], ["source_snapshots.tenant_id", "source_snapshots.id"]
        ),
        sa.ForeignKeyConstraint(["tenant_id", "run_id"], ["note_runs.tenant_id", "note_runs.id"]),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        sa.CheckConstraint("version >= 1", name="ck_note_revision_version"),
        sa.CheckConstraint("length(title) BETWEEN 1 AND 200", name="ck_note_revision_title"),
        sa.CheckConstraint(
            "octet_length(content_md) BETWEEN 1 AND 131072", name="ck_note_revision_markdown"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(document)='object' AND octet_length(document::text)<=262144",
            name="ck_note_revision_document",
        ),
    )
    op.create_index(
        "ix_note_revisions_created", "note_revisions", ["tenant_id", "created_at", "id"]
    )
    op.create_table(
        "note_sections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("items", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("tenant_id", "revision_id", "kind"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "revision_id"], ["note_revisions.tenant_id", "note_revisions.id"]
        ),
        sa.CheckConstraint(
            "kind IN ('overview','core_claims','steps','examples','numbers','takeaways')",
            name="ck_note_section_kind",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(items)='array' AND jsonb_array_length(items) BETWEEN 1 AND 6 "
            "AND octet_length(items::text)<=65536",
            name="ck_note_section_items",
        ),
    )
    op.create_table(
        "evidence_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_key", sa.String(4), nullable=False),
        sa.Column("quote", sa.String(800), nullable=False),
        sa.UniqueConstraint("tenant_id", "revision_id", "evidence_key"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "revision_id"], ["note_revisions.tenant_id", "note_revisions.id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"], ["source_snapshots.tenant_id", "source_snapshots.id"]
        ),
        sa.CheckConstraint("evidence_key ~ '^E[0-9]{3}$'", name="ck_note_evidence_key"),
        sa.CheckConstraint("length(quote) BETWEEN 1 AND 800", name="ck_note_evidence_quote"),
    )
    tenant = "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid"
    actor = "NULLIF(current_setting('app.user_id',true),'')::uuid"
    for table in ("note_runs", "notes", "note_revisions", "note_sections", "evidence_items"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_read ON {table} FOR SELECT USING ({tenant})")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC, knowledge_app")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO knowledge_app")
    op.execute(
        f"CREATE POLICY tenant_append ON note_runs FOR INSERT WITH CHECK ({tenant} "
        f"AND created_by={actor} AND status='pending' AND error_code IS NULL)"
    )
    # Column-level grants preserve the immutable idempotency binding. A crash or
    # unknown provider outcome must not be reset to pending and invoke it again.
    op.execute("GRANT UPDATE (status,error_code) ON note_runs TO knowledge_app")
    op.execute(
        f"CREATE POLICY own_finish ON note_runs FOR UPDATE USING ({tenant} AND created_by={actor} "
        f"AND status='pending') WITH CHECK ({tenant} AND created_by={actor} "
        "AND status IN ('succeeded','failed','unknown'))"
    )
    op.execute(
        f"CREATE POLICY tenant_append ON notes FOR INSERT WITH CHECK ({tenant} "
        f"AND created_by={actor} "
        "AND EXISTS (SELECT 1 FROM sources s WHERE s.tenant_id=notes.tenant_id "
        "AND s.id=notes.source_id AND s.status='accepted'))"
    )
    op.execute(
        f"CREATE POLICY tenant_append ON note_revisions FOR INSERT WITH CHECK ({tenant} "
        f"AND created_by={actor} AND EXISTS (SELECT 1 FROM note_runs r "
        "JOIN source_snapshots s ON s.tenant_id=r.tenant_id AND s.id=r.snapshot_id "
        "JOIN notes n ON n.tenant_id=s.tenant_id AND n.source_id=s.source_id "
        "WHERE r.tenant_id=note_revisions.tenant_id AND r.id=note_revisions.run_id "
        "AND r.snapshot_id=note_revisions.snapshot_id AND r.created_by=note_revisions.created_by "
        "AND r.status='pending' AND n.id=note_revisions.note_id))"
    )
    for table in ("note_sections", "evidence_items"):
        snapshot = (
            " AND r.snapshot_id=evidence_items.snapshot_id "
            "AND EXISTS (SELECT 1 FROM source_snapshots s "
            "WHERE s.tenant_id=evidence_items.tenant_id AND s.id=evidence_items.snapshot_id "
            "AND strpos(s.body_text,evidence_items.quote)>0)"
            if table == "evidence_items"
            else ""
        )
        op.execute(
            f"CREATE POLICY tenant_append ON {table} FOR INSERT WITH CHECK ({tenant} "
            f"AND EXISTS (SELECT 1 FROM note_revisions r WHERE r.tenant_id={table}.tenant_id "
            f"AND r.id={table}.revision_id AND r.created_by={actor}{snapshot}))"
        )
    op.drop_constraint("audit_events_action_check", "audit_events", type_="check")
    op.create_check_constraint(
        "audit_events_action_check",
        "audit_events",
        "action IN ('space.created','member.added','member.removed','job.created',"
        "'job.cancelled','job.retried','file.uploaded','file.deleted','provider.created',"
        "'provider.disabled','provider.reencrypted','settings.updated','provider.updated',"
        "'source.batch.created','source.snapshot.created','notes.generated')",
    )


def downgrade() -> None:
    raise RuntimeError("Knowledge history rollback requires an explicitly reviewed recovery plan")
