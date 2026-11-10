"""Email verification and single-use recovery tokens; frozen additive DDL."""

import sqlalchemy as sa
from alembic import op

revision = "0007_auth_recovery"
down_revision = "0006_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing accounts must verify explicitly when verification is enabled.
    op.add_column(
        "users",
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "auth_action_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("purpose IN ('verify_email', 'reset_password')"),
    )
    op.create_index("ix_auth_actions_user_purpose", "auth_action_tokens", ["user_id", "purpose"])
    op.create_index("ix_auth_actions_expiry", "auth_action_tokens", ["expires_at"])
    # Global opaque-token lookup is an authentication boundary like auth_sessions,
    # not a tenant data API. No plaintext tokens, user input or tenant payloads.
    op.execute("REVOKE ALL ON auth_action_tokens FROM PUBLIC, knowledge_app")
    op.execute("GRANT SELECT, INSERT, DELETE ON auth_action_tokens TO knowledge_app")


def downgrade() -> None:
    raise RuntimeError("Identity recovery rollback requires an explicitly reviewed plan")
