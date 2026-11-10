"""Real PostgreSQL RLS checks. No SQLite or mocked DB substitutes.

All fixtures are inside one rolled-back admin transaction and run under SET LOCAL ROLE.
Set RUN_INTEGRATION=1 and configure both database URLs against a dedicated test database.
"""

import os
from uuid import uuid4

import pytest
from app.core.config import get_settings
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Live database opt-in required"),
]


def test_live_two_tenant_isolation():
    settings = get_settings()
    assert settings.migration_database_url is not None
    engine = create_engine(settings.migration_database_url.get_secret_value())
    tenant_a, tenant_b, user_a, user_b, job_a, job_b = (uuid4() for _ in range(6))
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            for tenant, user, job in ((tenant_a, user_a, job_a), (tenant_b, user_b, job_b)):
                connection.execute(
                    text("INSERT INTO tenants (id,name,status) VALUES (:id,'RLS test','active')"),
                    {"id": tenant},
                )
                connection.execute(
                    text(
                        "INSERT INTO users (id,email,password_hash,status) "
                        "VALUES (:id,:email,'non-login-test-fixture','active')"
                    ),
                    {"id": user, "email": f"{user}@example.invalid"},
                )
                connection.execute(
                    text("INSERT INTO tenant_members VALUES (:tenant,:user,'owner')"),
                    {"tenant": tenant, "user": user},
                )
                connection.execute(
                    text(
                        "INSERT INTO processing_jobs "
                        "(id,tenant_id,requested_by,kind,status,progress,idempotency_key,payload) "
                        "VALUES (:id,:tenant,:user,'test','queued',0,:key,'{}')"
                    ),
                    {"id": job, "tenant": tenant, "user": user, "key": str(job)},
                )
            connection.execute(text("SET LOCAL ROLE knowledge_app"))
            role = connection.execute(
                text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
            ).one()
            assert not role.rolsuper and not role.rolbypassrls
            connection.execute(text("SELECT set_config('app.tenant_id','',true)"))
            assert connection.execute(text("SELECT count(*) FROM processing_jobs")).scalar() == 0
            for tenant, own, other in ((tenant_a, job_a, job_b), (tenant_b, job_b, job_a)):
                connection.execute(
                    text("SELECT set_config('app.tenant_id',:id,true)"), {"id": str(tenant)}
                )
                assert (
                    connection.execute(
                        text("SELECT id FROM processing_jobs WHERE id=:id"), {"id": own}
                    ).scalar()
                    == own
                )
                assert (
                    connection.execute(
                        text("SELECT id FROM processing_jobs WHERE id=:id"), {"id": other}
                    ).scalar()
                    is None
                )
                assert (
                    connection.execute(
                        text("UPDATE processing_jobs SET progress=99 WHERE id=:id RETURNING id"),
                        {"id": other},
                    ).scalar()
                    is None
                )
            connection.execute(
                text("SELECT set_config('app.tenant_id',:id,true)"), {"id": str(tenant_a)}
            )
            with pytest.raises(DBAPIError) as denied_write, connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO media_assets (id,tenant_id,storage_key,mime_type,size) "
                        "VALUES (:id,:tenant,'forbidden','text/plain',0)"
                    ),
                    {"id": uuid4(), "tenant": tenant_b},
                )
            assert denied_write.value.orig.sqlstate == "42501"  # RLS policy violation
            # Tenant A cannot reference user B's membership even with a valid A tenant column.
            with pytest.raises(DBAPIError) as invalid_member, connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO processing_jobs "
                        "(id,tenant_id,requested_by,kind,status,progress,idempotency_key,payload) "
                        "VALUES (:id,:tenant,:user,'test','queued',0,:key,'{}')"
                    ),
                    {"id": uuid4(), "tenant": tenant_a, "user": user_b, "key": str(uuid4())},
                )
            assert invalid_member.value.orig.sqlstate == "23503"  # Composite foreign key
        finally:
            transaction.rollback()
    engine.dispose()
