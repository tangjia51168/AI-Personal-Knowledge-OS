"""Add same-tenant, cycle-free manual topic parents without rewriting old rows."""

import sqlalchemy as sa
from alembic import op

revision = "0023_topic_hierarchy"
down_revision = "0022_label_update_actor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_labels", sa.Column("parent_id", sa.Uuid(), nullable=True))
    op.create_unique_constraint(
        "uq_label_tenant_id_kind", "knowledge_labels", ["tenant_id", "id", "kind"]
    )
    op.create_foreign_key(
        "fk_label_topic_parent",
        "knowledge_labels",
        "knowledge_labels",
        ["tenant_id", "parent_id", "kind"],
        ["tenant_id", "id", "kind"],
    )
    op.create_check_constraint(
        "ck_label_topic_parent",
        "knowledge_labels",
        "parent_id IS NULL OR (kind='topic' AND parent_id<>id)",
    )
    op.create_index("ix_label_parent", "knowledge_labels", ["tenant_id", "parent_id"])
    op.execute("GRANT UPDATE (parent_id) ON knowledge_labels TO knowledge_app")
    # Invoker rights preserve RLS; explicit tenant predicates also protect admin
    # maintenance. The same tenant lock order as application writers prevents
    # concurrent A->B/B->A updates from both observing an acyclic old state.
    op.execute("""
        CREATE FUNCTION public.check_topic_parent() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
        DECLARE
          current_id uuid;
          next_id uuid;
          parent_kind text;
          parent_active boolean;
          visited uuid[] := ARRAY[NEW.id];
        BEGIN
          PERFORM id FROM public.tenants WHERE id=NEW.tenant_id FOR NO KEY UPDATE;
          IF NEW.parent_id IS NULL THEN RETURN NEW; END IF;
          IF TG_OP='UPDATE' THEN
            IF NEW.parent_id IS NOT DISTINCT FROM OLD.parent_id THEN RETURN NEW; END IF;
          END IF;
          current_id := NEW.parent_id;
          WHILE current_id IS NOT NULL LOOP
            IF current_id=ANY(visited) OR cardinality(visited)>=100 THEN
              RAISE EXCEPTION 'TOPIC_PARENT_CYCLE' USING ERRCODE='23514';
            END IF;
            SELECT parent_id,kind,active INTO next_id,parent_kind,parent_active
              FROM public.knowledge_labels WHERE tenant_id=NEW.tenant_id AND id=current_id
              FOR SHARE;
            IF NOT FOUND OR parent_kind<>'topic' THEN
              RAISE EXCEPTION 'TOPIC_PARENT_NOT_FOUND' USING ERRCODE='23503';
            END IF;
            IF current_id=NEW.parent_id AND NOT parent_active THEN
              RAISE EXCEPTION 'TOPIC_PARENT_ARCHIVED' USING ERRCODE='23514';
            END IF;
            visited := array_append(visited,current_id);
            current_id := next_id;
          END LOOP;
          RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER topic_parent_guard BEFORE INSERT OR UPDATE OF parent_id
        ON knowledge_labels FOR EACH ROW EXECUTE FUNCTION public.check_topic_parent()
    """)


def downgrade() -> None:
    raise RuntimeError("Topic hierarchy rollback requires an explicitly reviewed recovery plan")
