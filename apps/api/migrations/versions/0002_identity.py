"""Opaque revocable sessions and identity-to-space membership discovery; frozen DDL."""

import sqlalchemy as sa
from alembic import op

revision = "0002_identity"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Global authentication boundary, NOT tenant business tables. No raw bearer tokens.
    op.create_table(
        "auth_identities",
        sa.Column("email", sa.String(320), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, unique=True),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_auth_sessions_user", "auth_sessions", ["user_id"])
    op.execute("GRANT SELECT, INSERT ON auth_identities TO knowledge_app")
    op.execute("GRANT SELECT, INSERT, DELETE ON auth_sessions TO knowledge_app")
    # A verified identity can discover only its own memberships before choosing a tenant.
    op.execute(
        "CREATE POLICY own_memberships ON tenant_members FOR SELECT "
        "USING (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid)"
    )
    op.execute(
        "CREATE POLICY member_spaces ON tenants FOR SELECT USING (EXISTS "
        "(SELECT 1 FROM tenant_members m WHERE m.tenant_id = tenants.id "
        "AND m.user_id = NULLIF(current_setting('app.user_id', true), '')::uuid))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY member_spaces ON tenants")
    op.execute("DROP POLICY own_memberships ON tenant_members")
    op.drop_table("auth_sessions")
    op.drop_table("auth_identities")
