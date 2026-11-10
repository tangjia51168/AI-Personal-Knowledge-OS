"""Add reversible, versioned manual label directory maintenance."""

import sqlalchemy as sa
from alembic import op

revision = "0021_label_management"
down_revision = "0020_learning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_labels",
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(
        "knowledge_labels", sa.Column("version", sa.Integer(), server_default="1", nullable=False)
    )
    op.add_column(
        "knowledge_labels",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_check_constraint("ck_knowledge_label_version", "knowledge_labels", "version >= 1")
    op.create_check_constraint(
        "ck_knowledge_label_timestamps", "knowledge_labels", "updated_at>=created_at"
    )
    op.execute(
        "GRANT UPDATE (name,name_key,active,version,updated_at) "
        "ON knowledge_labels TO knowledge_app"
    )
    tenant = "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid"
    actor = "NULLIF(current_setting('app.user_id',true),'')::uuid"
    member = (
        "EXISTS (SELECT 1 FROM tenant_members m "
        "WHERE m.tenant_id=knowledge_labels.tenant_id AND m.user_id=" + actor + " AND m.active)"
    )
    op.execute(
        "CREATE POLICY tenant_change ON knowledge_labels FOR UPDATE USING ("
        + tenant
        + " AND "
        + member
        + ") WITH CHECK ("
        + tenant
        + " AND "
        + member
        + ")"
    )
    op.drop_constraint("audit_events_action_check", "audit_events", type_="check")
    audit_actions = (
        "'space.created','member.added','member.removed','job.created','job.cancelled',"
        "'job.retried','file.uploaded','file.deleted','provider.created','provider.disabled',"
        "'provider.reencrypted','settings.updated','provider.updated','source.batch.created',"
        "'source.snapshot.created','notes.generated','source.content.imported','notes.edited',"
        "'organization.label_created','organization.label_attached','organization.label_removed',"
        "'organization.label_updated','notes.annotated','notes.linked','notes.unlinked',"
        "'learning.updated'"
    )
    op.create_check_constraint(
        "audit_events_action_check",
        "audit_events",
        f"action IN ({audit_actions})",
    )


def downgrade() -> None:
    raise RuntimeError("Label management rollback requires an explicitly reviewed recovery plan")
