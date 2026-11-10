"""Durable, requester-owned research outcomes with fixed retrieved citations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0026_research_answers"
down_revision = "0025_manual_first_note"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("query", sa.String(120), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("answer", postgresql.JSONB()),
        sa.Column("error_code", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        sa.CheckConstraint("status IN ('pending','succeeded','failed','unknown')"),
        sa.CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$'"),
        sa.CheckConstraint("length(question) BETWEEN 1 AND 1000 AND octet_length(question)<=4000"),
        sa.CheckConstraint("length(query) BETWEEN 1 AND 120"),
        sa.CheckConstraint(
            "(status='succeeded' AND answer IS NOT NULL AND octet_length(answer::text)<=65536) "
            "OR (status<>'succeeded' AND answer IS NULL)"
        ),
    )
    op.create_index(
        "ix_research_runs_requester", "research_runs", ["tenant_id", "created_by", "created_at"]
    )
    own = (
        "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid "
        "AND created_by=NULLIF(current_setting('app.user_id',true),'')::uuid"
    )
    member = (
        "EXISTS (SELECT 1 FROM tenant_members m WHERE m.tenant_id=research_runs.tenant_id "
        "AND m.user_id=research_runs.created_by AND m.active)"
    )
    op.execute("ALTER TABLE research_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE research_runs FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON research_runs FROM PUBLIC,knowledge_app")
    op.execute("GRANT SELECT,INSERT ON research_runs TO knowledge_app")
    op.execute("GRANT UPDATE(status,answer,error_code) ON research_runs TO knowledge_app")
    op.execute(f"CREATE POLICY own_read ON research_runs FOR SELECT USING ({own} AND {member})")
    op.execute(
        f"CREATE POLICY own_append ON research_runs FOR INSERT WITH CHECK ({own} AND {member} "
        "AND status='pending' AND answer IS NULL AND error_code IS NULL)"
    )
    op.execute(
        f"CREATE POLICY own_finish ON research_runs FOR UPDATE USING ({own} AND {member} "
        f"AND status='pending') WITH CHECK ({own} AND {member} "
        "AND status IN ('succeeded','failed','unknown'))"
    )
    op.create_table(
        "research_citations",
        sa.Column("tenant_id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(4), primary_key=True),
        sa.Column("note_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_key", sa.String(4), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("provenance", sa.String(32), nullable=False),
        sa.Column("content_kind", sa.String(32), nullable=False),
        sa.Column("quote", sa.String(800), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"], ["research_runs.tenant_id", "research_runs.id"]
        ),
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
        sa.CheckConstraint("key ~ '^R00[1-8]$'"),
    )
    op.execute("ALTER TABLE research_citations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE research_citations FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON research_citations FROM PUBLIC,knowledge_app")
    op.execute("GRANT SELECT,INSERT ON research_citations TO knowledge_app")
    requester = (
        "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid AND EXISTS "
        "(SELECT 1 FROM research_runs r WHERE r.tenant_id=research_citations.tenant_id "
        "AND r.id=research_citations.run_id AND r.created_by="
        "NULLIF(current_setting('app.user_id',true),'')::uuid"
    )
    op.execute(f"CREATE POLICY own_read ON research_citations FOR SELECT USING ({requester}))")
    actual = (
        "EXISTS (SELECT 1 FROM evidence_items e JOIN note_revisions n ON "
        "n.tenant_id=e.tenant_id AND n.id=e.revision_id AND n.snapshot_id=e.snapshot_id "
        "JOIN source_snapshots s ON s.tenant_id=n.tenant_id AND s.id=n.snapshot_id WHERE "
        "e.tenant_id=research_citations.tenant_id AND e.revision_id=research_citations.revision_id "
        "AND e.snapshot_id=research_citations.snapshot_id "
        "AND e.evidence_key=research_citations.evidence_key "
        "AND e.quote=research_citations.quote AND n.note_id=research_citations.note_id "
        "AND n.version=research_citations.version AND n.title=research_citations.title "
        "AND s.provenance=research_citations.provenance "
        "AND s.content_kind=research_citations.content_kind)"
    )
    op.execute(
        f"CREATE POLICY own_append ON research_citations FOR INSERT WITH CHECK "
        f"({requester} AND r.status='pending') AND {actual})"
    )
    op.drop_constraint("audit_events_action_check", "audit_events", type_="check")
    actions = (
        "'space.created','member.added','member.removed','job.created','job.cancelled',"
        "'job.retried','file.uploaded','file.deleted','provider.created','provider.disabled',"
        "'provider.reencrypted','settings.updated','provider.updated','source.batch.created',"
        "'source.snapshot.created','notes.generated','source.content.imported','notes.edited',"
        "'organization.label_created','organization.label_attached','organization.label_removed',"
        "'organization.label_updated','notes.annotated','notes.linked','notes.unlinked',"
        "'learning.updated','organization.concept_created','research.created','research.finished'"
    )
    op.create_check_constraint(
        "audit_events_action_check", "audit_events", f"action IN ({actions})"
    )


def downgrade() -> None:
    raise RuntimeError("Research history requires reviewed recovery; no destructive downgrade")
