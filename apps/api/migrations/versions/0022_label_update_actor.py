"""Bind label directory updates to the current member actor."""

import sqlalchemy as sa
from alembic import op

revision = "0022_label_update_actor"
down_revision = "0021_label_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_labels", sa.Column("updated_by", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_knowledge_labels_updated_by",
        "knowledge_labels",
        "tenant_members",
        ["tenant_id", "updated_by"],
        ["tenant_id", "user_id"],
    )
    op.execute(
        "GRANT UPDATE (name,name_key,active,version,updated_at,updated_by) "
        "ON knowledge_labels TO knowledge_app"
    )
    op.execute("DROP POLICY tenant_change ON knowledge_labels")
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
        + " AND updated_by="
        + actor
        + ")"
    )


def downgrade() -> None:
    raise RuntimeError("Label actor binding rollback requires an explicitly reviewed recovery plan")
