"""Tenant file lifecycle and ownership, frozen additive DDL."""

import sqlalchemy as sa
from alembic import op

revision = "0004_assets"
down_revision = "0003_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing infrastructure metadata has no uploader; leave it inaccessible as legacy.
    op.add_column("media_assets", sa.Column("uploaded_by", sa.Uuid(), nullable=True))
    op.add_column(
        "media_assets", sa.Column("status", sa.String(32), server_default="legacy", nullable=False)
    )
    op.add_column(
        "media_assets", sa.Column("filename", sa.String(200), server_default="file", nullable=False)
    )
    op.add_column(
        "media_assets",
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_unique_constraint("uq_assets_tenant_id", "media_assets", ["tenant_id", "id"])
    op.create_foreign_key(
        "fk_asset_uploader",
        "media_assets",
        "tenant_members",
        ["tenant_id", "uploaded_by"],
        ["tenant_id", "user_id"],
    )
    op.create_check_constraint(
        "ck_asset_status", "media_assets", "status IN ('legacy','pending','ready','deleting')"
    )
    op.create_check_constraint(
        "ck_asset_size", "media_assets", "status='legacy' OR (size >= 0 AND size <= 10485760)"
    )


def downgrade() -> None:
    raise RuntimeError("File rollback requires an explicitly reviewed recovery plan")
