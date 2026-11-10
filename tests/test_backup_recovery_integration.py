"""Verify recovery detects constraint, RLS and grant drift without retaining changes."""

import importlib.util
import os
from uuid import uuid4

import pytest
from app.core.config import get_settings
from sqlalchemy import create_engine, text

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Live database opt-in required"),
]


def test_live_recovery_detects_constraint_definition_and_validation_drift():
    spec = importlib.util.spec_from_file_location("backup_recovery", "scripts/backup_recovery.py")
    assert spec and spec.loader
    backup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backup)
    settings = get_settings()
    assert settings.migration_database_url is not None
    engine = create_engine(settings.migration_database_url.get_secret_value())
    fixture = "recovery_constraint_" + uuid4().hex
    relation = "acceptance_checks." + fixture
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text("CREATE SCHEMA IF NOT EXISTS acceptance_checks"))
                connection.execute(
                    text(
                        f"CREATE TABLE {relation} (id integer PRIMARY KEY, "
                        "role varchar(20), "
                        "CONSTRAINT role_allowed CHECK (role IN ('owner','member')), "
                        "CONSTRAINT positive_id CHECK (id > 0))"
                    )
                )
                original = backup.database_fingerprint(connection)[relation]
                connection.execute(text(f"ALTER TABLE {relation} DROP CONSTRAINT role_allowed"))
                connection.execute(
                    text(
                        f"ALTER TABLE {relation} ADD CONSTRAINT role_allowed "
                        "CHECK (role::text = ANY (ARRAY['owner'::varchar::text, "
                        "'member'::varchar::text]))"
                    )
                )
                assert backup.database_fingerprint(connection)[relation] == original
                connection.execute(text(f"ALTER TABLE {relation} DROP CONSTRAINT positive_id"))
                connection.execute(
                    text(
                        f"ALTER TABLE {relation} ADD CONSTRAINT positive_id "
                        "CHECK (id > 1) NOT VALID"
                    )
                )
                changed = backup.database_fingerprint(connection)[relation]
                assert original["rows"] == changed["rows"] == 0
                assert original["sha256"] == changed["sha256"]
                assert original["rls"] == changed["rls"]
                assert original["policies"] == changed["policies"]
                assert original["constraints"] != changed["constraints"]
                connection.execute(text(f"ALTER TABLE {relation} VALIDATE CONSTRAINT positive_id"))
                validated = backup.database_fingerprint(connection)[relation]
                assert changed["constraints"] != validated["constraints"]
                connection.execute(text(f"GRANT UPDATE (role) ON {relation} TO knowledge_app"))
                limited = backup.database_fingerprint(connection)[relation]
                assert validated["rls"] == limited["rls"]
                assert validated["column_privileges"] == []
                assert limited["column_privileges"]
                connection.execute(text(f"GRANT UPDATE (id) ON {relation} TO knowledge_app"))
                widened = backup.database_fingerprint(connection)[relation]
                assert limited["rls"] == widened["rls"]
                assert limited["constraints"] == widened["constraints"]
                assert limited["column_privileges"] != widened["column_privileges"]
                connection.execute(text(f"REVOKE UPDATE (id) ON {relation} FROM knowledge_app"))
                assert backup.database_fingerprint(connection)[relation] == limited
                connection.execute(text(f"ALTER TABLE {relation} ENABLE ROW LEVEL SECURITY"))
                connection.execute(text(f"ALTER TABLE {relation} FORCE ROW LEVEL SECURITY"))
                connection.execute(
                    text(
                        f"CREATE POLICY own_finish ON {relation} FOR UPDATE TO knowledge_app "
                        "USING (id > 0) WITH CHECK "
                        "(role::text = ANY ((ARRAY['owner'::varchar,'member'::varchar])::text[]))"
                    )
                )
                policy_original = backup.database_fingerprint(connection)[relation]
                connection.execute(
                    text(
                        f"ALTER POLICY own_finish ON {relation} WITH CHECK "
                        "(role::text = ANY (ARRAY['owner'::varchar::text, "
                        "'member'::varchar::text]))"
                    )
                )
                assert backup.database_fingerprint(connection)[relation] == policy_original
                connection.execute(text(f"ALTER POLICY own_finish ON {relation} USING (id > 1)"))
                policy_using_changed = backup.database_fingerprint(connection)[relation]
                assert policy_using_changed["policies"] != policy_original["policies"]
                assert policy_using_changed["constraints"] == policy_original["constraints"]
                connection.execute(
                    text(
                        f"ALTER POLICY own_finish ON {relation} USING (id > 0) "
                        "WITH CHECK (role = 'owner')"
                    )
                )
                policy_check_changed = backup.database_fingerprint(connection)[relation]
                assert policy_check_changed["policies"] != policy_original["policies"]
                assert policy_check_changed["rls"] == policy_original["rls"]
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
