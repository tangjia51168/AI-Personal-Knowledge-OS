"""Shared source collections with tenant composite integrity and column privileges."""

from alembic import op

revision = "0031_collections"
down_revision = "0030_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE collections (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL,
        kind varchar(16) NOT NULL CHECK(kind IN ('folder','collection')),
        name varchar(60) NOT NULL CHECK(length(name) BETWEEN 1 AND 60 AND octet_length(name)<=240),
        name_key varchar(180) NOT NULL
          CHECK(length(name_key) BETWEEN 1 AND 180 AND octet_length(name_key)<=720),
        active boolean NOT NULL DEFAULT true, version integer NOT NULL DEFAULT 1 CHECK(version>=1),
        created_by uuid NOT NULL, updated_by uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CHECK(updated_at>=created_at), UNIQUE(tenant_id,id), UNIQUE(tenant_id,kind,name_key),
        FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_members(tenant_id,user_id),
        FOREIGN KEY(tenant_id,updated_by) REFERENCES tenant_members(tenant_id,user_id)
      );
      CREATE TABLE collection_items (
        tenant_id uuid NOT NULL, collection_id uuid NOT NULL, source_id uuid NOT NULL,
        active boolean NOT NULL DEFAULT true,
        position integer NOT NULL CHECK(position BETWEEN 0 AND 199),
        created_by uuid NOT NULL, updated_by uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,collection_id,source_id), CHECK(updated_at>=created_at),
        FOREIGN KEY(tenant_id,collection_id) REFERENCES collections(tenant_id,id),
        FOREIGN KEY(tenant_id,source_id) REFERENCES sources(tenant_id,id),
        FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_members(tenant_id,user_id),
        FOREIGN KEY(tenant_id,updated_by) REFERENCES tenant_members(tenant_id,user_id)
      );
      CREATE INDEX ix_collection_source ON collection_items(tenant_id,source_id);
    """)
    actor = "NULLIF(current_setting('app.user_id',true),'')::uuid"
    for table in ("collections", "collection_items"):
        member = (
            "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid "
            f"AND EXISTS(SELECT 1 FROM tenant_members m WHERE m.tenant_id={table}.tenant_id "
            f"AND m.user_id={actor} AND m.active)"
        )
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC,knowledge_app")
        op.execute(f"GRANT SELECT,INSERT ON {table} TO knowledge_app")
        op.execute(f"CREATE POLICY member_read ON {table} FOR SELECT USING ({member})")
        op.execute(
            f"CREATE POLICY member_append ON {table} FOR INSERT WITH CHECK "
            f"({member} AND created_by={actor} AND updated_by={actor})"
        )
        op.execute(
            f"CREATE POLICY member_update ON {table} FOR UPDATE USING ({member}) "
            f"WITH CHECK ({member} AND updated_by={actor})"
        )
    op.execute(
        "GRANT UPDATE(name,name_key,active,version,updated_by,updated_at) "
        "ON collections TO knowledge_app"
    )
    op.execute(
        "GRANT UPDATE(active,position,updated_by,updated_at) ON collection_items TO knowledge_app"
    )
    op.drop_constraint("audit_events_action_check", "audit_events", type_="check")
    actions = (
        "space.created member.added member.removed job.created job.cancelled job.retried "
        "file.uploaded file.deleted provider.created provider.disabled provider.reencrypted "
        "settings.updated provider.updated source.batch.created source.snapshot.created "
        "notes.generated source.content.imported notes.edited organization.label_created "
        "organization.label_attached organization.label_removed organization.label_updated "
        "notes.annotated notes.linked notes.unlinked learning.updated organization.concept_created "
        "research.created research.finished source.schedule_created source.schedule_updated "
        "notes.indexed notification.preference notification.created notification.read "
        "notification.dispatch notification.finished "
        "collection.created collection.updated collection.item_updated"
    ).split()
    op.create_check_constraint(
        "audit_events_action_check",
        "audit_events",
        "action IN (" + ",".join(map(repr, actions)) + ")",
    )


def downgrade() -> None:
    raise RuntimeError("Collection rollback requires a reviewed recovery plan")
