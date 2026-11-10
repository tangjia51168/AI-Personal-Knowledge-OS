"""Immutable model-versioned same-tenant revision vectors; exact bounded retrieval."""

from alembic import op

revision = "0029_semantic_chunks"
down_revision = "0028_summary_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE semantic_chunks (
          tenant_id uuid NOT NULL,
          note_id uuid NOT NULL,
          snapshot_id uuid NOT NULL,
          revision_id uuid NOT NULL,
          position integer NOT NULL CHECK(position BETWEEN 0 AND 127),
          model varchar(100) NOT NULL CHECK(model=
            'multilingual-minilm-l12-v2-quint8-avx2-e8f8c211-mean128-v1'),
          quote text NOT NULL CHECK(length(quote) BETWEEN 1 AND 96),
          embedding vector(384) NOT NULL,
          created_by uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,revision_id,model,position),
          FOREIGN KEY(tenant_id,note_id,snapshot_id,revision_id)
            REFERENCES note_revisions(tenant_id,note_id,snapshot_id,id),
          FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_members(tenant_id,user_id),
          CHECK((embedding <#> embedding) BETWEEN -1.001 AND -0.999)
        )
    """)
    op.execute("ALTER TABLE semantic_chunks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE semantic_chunks FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON semantic_chunks FROM PUBLIC,knowledge_app")
    op.execute("GRANT SELECT,INSERT ON semantic_chunks TO knowledge_app")
    tenant = "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid"
    actor = "NULLIF(current_setting('app.user_id',true),'')::uuid"
    member = (
        "EXISTS (SELECT 1 FROM tenant_members m WHERE m.tenant_id=semantic_chunks.tenant_id "
        f"AND m.user_id={actor} AND m.active)"
    )
    op.execute(
        f"CREATE POLICY tenant_read ON semantic_chunks FOR SELECT USING ({tenant} AND {member})"
    )
    exact = (
        "EXISTS (SELECT 1 FROM note_revisions r WHERE r.tenant_id=semantic_chunks.tenant_id "
        "AND r.id=semantic_chunks.revision_id AND r.note_id=semantic_chunks.note_id "
        "AND r.snapshot_id=semantic_chunks.snapshot_id "
        "AND substring(r.title || E'\\n' || r.content_md FROM "
        "semantic_chunks.position*96+1 FOR 96)=semantic_chunks.quote)"
    )
    admin = (
        "EXISTS (SELECT 1 FROM tenant_members m WHERE m.tenant_id=semantic_chunks.tenant_id "
        f"AND m.user_id={actor} AND m.active AND m.role IN ('owner','admin'))"
    )
    op.execute(
        "CREATE POLICY tenant_append ON semantic_chunks FOR INSERT WITH CHECK "
        f"({tenant} AND {member} AND {admin} AND created_by={actor} AND {exact})"
    )
    op.drop_constraint("audit_events_action_check", "audit_events", type_="check")
    # Frozen vocabulary, independent of mutable ORM enums.
    actions = (
        "space.created member.added member.removed job.created job.cancelled job.retried "
        "file.uploaded file.deleted provider.created provider.disabled provider.reencrypted "
        "settings.updated provider.updated source.batch.created source.snapshot.created "
        "notes.generated source.content.imported notes.edited organization.label_created "
        "organization.label_attached organization.label_removed organization.label_updated "
        "notes.annotated notes.linked notes.unlinked learning.updated organization.concept_created "
        "research.created research.finished source.schedule_created source.schedule_updated "
        "notes.indexed"
    ).split()
    op.create_check_constraint(
        "audit_events_action_check",
        "audit_events",
        "action IN (" + ",".join(map(repr, actions)) + ")",
    )


def downgrade() -> None:
    raise RuntimeError("Semantic index rollback requires a reviewed recovery plan")
