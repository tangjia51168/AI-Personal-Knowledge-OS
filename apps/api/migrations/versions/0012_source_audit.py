"""Extend the frozen audit vocabulary for source intake batches."""

from alembic import op

revision = "0012_source_audit"
down_revision = "0011_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("audit_events_action_check", "audit_events", type_="check")
    op.create_check_constraint(
        "audit_events_action_check",
        "audit_events",
        "action IN ('space.created','member.added','member.removed','job.created',"
        "'job.cancelled','job.retried','file.uploaded','file.deleted','provider.created',"
        "'provider.disabled','provider.reencrypted','settings.updated','provider.updated',"
        "'source.batch.created')",
    )


def downgrade() -> None:
    raise RuntimeError("Audit retention rollback requires an explicitly reviewed recovery plan")
