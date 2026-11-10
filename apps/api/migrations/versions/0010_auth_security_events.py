"""Append-only account security observations with a frozen action vocabulary."""

import sqlalchemy as sa
from alembic import op

revision = "0010_auth_security_events"
down_revision = "0009_provider_updates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_security_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "action IN ('account.registered','login.succeeded','session.logged_out',"
            "'session.all_revoked','email.verified','password.reset','login.failed','login.unverified')",
            name="ck_auth_security_action",
        ),
        sa.CheckConstraint(
            "(action IN ('login.failed','login.unverified') AND bucket_start IS NOT NULL) OR "
            "(action NOT IN ('login.failed','login.unverified') AND bucket_start IS NULL)",
            name="ck_auth_security_bucket_kind",
        ),
        sa.CheckConstraint(
            "user_id IS NOT NULL OR action='login.failed'", name="ck_auth_security_subject"
        ),
        sa.CheckConstraint(
            "bucket_start IS NULL OR bucket_start=date_bin(interval '15 minutes',created_at,"
            "timestamptz '2000-01-01 00:00:00+00')",
            name="ck_auth_security_bucket_time",
        ),
    )
    op.create_index(
        "ix_auth_security_user_cursor", "auth_security_events", ["user_id", "created_at", "id"]
    )
    op.create_index(
        "uq_auth_security_failure_bucket",
        "auth_security_events",
        ["user_id", "action", "bucket_start"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("bucket_start IS NOT NULL"),
    )
    op.execute("ALTER TABLE auth_security_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE auth_security_events FORCE ROW LEVEL SECURITY")
    subject = "NULLIF(current_setting('app.user_id',true),'')::uuid"
    op.execute(
        f"CREATE POLICY auth_security_read ON auth_security_events FOR SELECT "
        f"USING (user_id={subject})"
    )
    op.execute(
        f"CREATE POLICY auth_security_append ON auth_security_events FOR INSERT WITH CHECK "
        f"(created_at=now() AND ((user_id={subject}) OR "
        f"(user_id IS NULL AND {subject} IS NULL AND action='login.failed')))"
    )
    op.execute("REVOKE ALL ON auth_security_events FROM PUBLIC, knowledge_app")
    op.execute("GRANT SELECT, INSERT ON auth_security_events TO knowledge_app")


def downgrade() -> None:
    raise RuntimeError(
        "Security history retention rollback requires an explicitly reviewed recovery plan"
    )
