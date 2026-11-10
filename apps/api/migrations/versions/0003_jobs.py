"""Durable tenant jobs and a minimal global dispatch index. Frozen DDL."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_jobs"
down_revision = "0002_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_members",
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.execute("DROP POLICY own_memberships ON tenant_members")
    op.execute(
        "CREATE POLICY own_memberships ON tenant_members FOR SELECT USING "
        "(active AND user_id = NULLIF(current_setting('app.user_id', true), '')::uuid)"
    )
    op.execute("DROP POLICY member_spaces ON tenants")
    op.execute(
        "CREATE POLICY member_spaces ON tenants FOR SELECT USING (EXISTS "
        "(SELECT 1 FROM tenant_members m WHERE m.tenant_id=tenants.id AND m.active "
        "AND m.user_id=NULLIF(current_setting('app.user_id', true), '')::uuid))"
    )
    op.add_column("processing_jobs", sa.Column("result", postgresql.JSONB()))
    op.add_column(
        "processing_jobs",
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("processing_jobs", sa.Column("lease_until", sa.DateTime(timezone=True)))
    for name in ("job_steps", "job_attempts"):
        op.create_table(
            name,
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("tenant_id", sa.Uuid(), nullable=False),
            sa.Column("job_id", sa.Uuid(), nullable=False),
            sa.Column("number", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("error_code", sa.String(100)),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint("tenant_id", "job_id", "number"),
            sa.ForeignKeyConstraint(
                ["tenant_id", "job_id"], ["processing_jobs.tenant_id", "processing_jobs.id"]
            ),
        )
        op.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {name} USING "
            "(tenant_id=NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
            "WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {name} TO knowledge_app")
    # Global scheduling metadata only: no payload/result/credentials. Runtime has no RLS bypass.
    op.create_table(
        "job_dispatches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column(
            "due_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("done", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"], ["processing_jobs.tenant_id", "processing_jobs.id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by"], ["tenant_members.tenant_id", "tenant_members.user_id"]
        ),
    )
    op.create_index("ix_dispatch_due", "job_dispatches", ["done", "due_at"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON job_dispatches TO knowledge_app")


def downgrade() -> None:
    raise RuntimeError("Destructive rollback requires an explicitly reviewed recovery plan")
