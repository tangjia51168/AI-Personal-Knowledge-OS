"""Append-only tenant business audit journal with a frozen action vocabulary."""

import sqlalchemy as sa
from alembic import op

revision = "0006_audit"
down_revision = "0005_ai_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_id"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
        sa.CheckConstraint(
            "action IN ('space.created','member.added','member.removed','job.created',"
            "'job.cancelled','job.retried','file.uploaded','file.deleted','provider.created',"
            "'provider.disabled','provider.reencrypted','settings.updated')"
        ),
    )
    op.create_index("ix_audit_tenant_cursor", "audit_events", ["tenant_id", "created_at", "id"])
    op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_events FORCE ROW LEVEL SECURITY")
    tenant = "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid"
    actor = "actor_id=NULLIF(current_setting('app.user_id',true),'')::uuid"
    op.execute(f"CREATE POLICY audit_read ON audit_events FOR SELECT USING ({tenant})")
    op.execute(
        f"CREATE POLICY audit_append ON audit_events FOR INSERT WITH CHECK ({tenant} AND {actor})"
    )
    op.execute("REVOKE ALL ON audit_events FROM PUBLIC, knowledge_app")
    op.execute("GRANT SELECT, INSERT ON audit_events TO knowledge_app")


def downgrade() -> None:
    raise RuntimeError("Audit retention rollback requires an explicitly reviewed recovery plan")
