"""Private opt-in inbox and bounded SMTP receipts; no mail body in discovery."""

from alembic import op

revision = "0030_notifications"
down_revision = "0029_semantic_chunks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE notification_preferences (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, user_id uuid NOT NULL,
        enabled boolean NOT NULL, email boolean NOT NULL,
        version integer NOT NULL CHECK(version>=1),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(tenant_id,user_id), UNIQUE(tenant_id,id,user_id),
        FOREIGN KEY(tenant_id,user_id) REFERENCES tenant_members(tenant_id,user_id),
        CHECK(enabled OR NOT email)
      );
      CREATE TABLE notification_ticks (
        preference_id uuid PRIMARY KEY, tenant_id uuid NOT NULL, user_id uuid NOT NULL,
        enabled boolean NOT NULL, due_at timestamptz NOT NULL,
        FOREIGN KEY(tenant_id,preference_id,user_id)
          REFERENCES notification_preferences(tenant_id,id,user_id)
      );
      CREATE INDEX ix_notification_ticks_due ON notification_ticks(enabled,due_at);
      CREATE TABLE notification_deliveries (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, user_id uuid NOT NULL,
        preference_id uuid NOT NULL,
        preference_version integer NOT NULL CHECK(preference_version>=1),
        occurrence varchar(80) NOT NULL, kind varchar(8) NOT NULL CHECK(kind IN ('due','test')),
        note_id uuid, reminder_at timestamptz,
        email_state varchar(20) NOT NULL CHECK(email_state IN
          ('not_requested','pending','dispatched','smtp_accepted','unknown','cancelled')),
        read_at timestamptz, dispatched_at timestamptz, finished_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(tenant_id,user_id,occurrence),
        FOREIGN KEY(tenant_id,preference_id,user_id)
          REFERENCES notification_preferences(tenant_id,id,user_id),
        FOREIGN KEY(tenant_id,note_id) REFERENCES notes(tenant_id,id),
        CHECK((kind='test' AND note_id IS NULL AND reminder_at IS NULL) OR
          (kind='due' AND note_id IS NOT NULL AND reminder_at IS NOT NULL)),
        CHECK((email_state IN ('dispatched','smtp_accepted','unknown'))=
          (dispatched_at IS NOT NULL)),
        CHECK((email_state IN ('smtp_accepted','unknown','cancelled'))=(finished_at IS NOT NULL))
      );
      CREATE INDEX ix_notification_inbox
        ON notification_deliveries(tenant_id,user_id,created_at,id);
      CREATE INDEX ix_notification_pending ON notification_deliveries(preference_id,email_state);
      REVOKE ALL ON notification_ticks FROM PUBLIC,knowledge_app;
      GRANT SELECT,INSERT,UPDATE ON notification_ticks TO knowledge_app;
    """)
    actor = "NULLIF(current_setting('app.user_id',true),'')::uuid"
    for table in ("notification_preferences", "notification_deliveries"):
        own = (
            "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid "
            f"AND user_id={actor} AND EXISTS(SELECT 1 FROM tenant_members m "
            f"WHERE m.tenant_id={table}.tenant_id AND m.user_id={actor} AND m.active)"
        )
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC,knowledge_app")
        op.execute(f"GRANT SELECT,INSERT ON {table} TO knowledge_app")
        op.execute(f"CREATE POLICY own_read ON {table} FOR SELECT USING ({own})")
        op.execute(f"CREATE POLICY own_append ON {table} FOR INSERT WITH CHECK ({own})")
        op.execute(
            f"CREATE POLICY own_update ON {table} FOR UPDATE USING ({own}) WITH CHECK ({own})"
        )
    op.execute("GRANT UPDATE(enabled,email,version) ON notification_preferences TO knowledge_app")
    op.execute("""GRANT UPDATE(email_state,read_at,dispatched_at,finished_at)
        ON notification_deliveries TO knowledge_app""")
    op.execute("""
      CREATE FUNCTION notification_transition() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF OLD.read_at IS NOT NULL AND NEW.read_at IS DISTINCT FROM OLD.read_at THEN
          RAISE EXCEPTION 'NOTIFICATION_READ_IMMUTABLE';
        END IF;
        IF OLD.email_state IS DISTINCT FROM NEW.email_state AND NOT (
          (OLD.email_state='pending' AND NEW.email_state IN ('dispatched','cancelled')) OR
          (OLD.email_state='dispatched' AND NEW.email_state IN ('smtp_accepted','unknown'))
        ) THEN RAISE EXCEPTION 'NOTIFICATION_TRANSITION_INVALID'; END IF;
        IF OLD.dispatched_at IS NOT NULL AND NEW.dispatched_at IS DISTINCT FROM OLD.dispatched_at
          THEN RAISE EXCEPTION 'NOTIFICATION_DISPATCH_IMMUTABLE'; END IF;
        IF OLD.finished_at IS NOT NULL AND NEW.finished_at IS DISTINCT FROM OLD.finished_at
          THEN RAISE EXCEPTION 'NOTIFICATION_FINISH_IMMUTABLE'; END IF;
        RETURN NEW;
      END $$;
      REVOKE ALL ON FUNCTION notification_transition() FROM PUBLIC;
      CREATE TRIGGER notification_delivery_transition BEFORE UPDATE ON notification_deliveries
        FOR EACH ROW EXECUTE FUNCTION notification_transition();
    """)
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
        "notification.dispatch notification.finished"
    ).split()
    op.create_check_constraint(
        "audit_events_action_check",
        "audit_events",
        "action IN (" + ",".join(map(repr, actions)) + ")",
    )


def downgrade() -> None:
    raise RuntimeError("Notification rollback requires a reviewed recovery plan")
