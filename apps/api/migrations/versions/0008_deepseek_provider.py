"""Allow the fixed DeepSeek cloud provider in the encrypted provider table."""

from alembic import op

revision = "0008_deepseek_provider"
down_revision = "0007_auth_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai_providers DROP CONSTRAINT IF EXISTS ai_providers_kind_check")
    op.execute(
        "ALTER TABLE ai_providers ADD CONSTRAINT ai_providers_kind_check "
        "CHECK (kind IN ('openai', 'deepseek', 'ollama'))"
    )


def downgrade() -> None:
    raise RuntimeError("Provider kind rollback requires an explicitly reviewed recovery plan")
