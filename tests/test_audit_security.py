from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.modules.audit.models import AuditAction, AuditEvent
from app.modules.audit.routes import router
from app.modules.audit.service import record
from app.modules.identity.access import member_access
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def test_audit_record_rejects_arbitrary_text_before_session_write():
    with Session() as session:
        with pytest.raises(ValueError, match="Unsupported audit action"):
            record(session, uuid4(), uuid4(), "private-secret", uuid4())
        assert not session.new
        event = record(session, uuid4(), uuid4(), AuditAction.PROVIDER_CREATED, uuid4())
        assert event in session.new
        assert {column.name for column in AuditEvent.__table__.columns} == {
            "id",
            "tenant_id",
            "actor_id",
            "action",
            "resource_id",
            "created_at",
        }
        # Journal entries belong to the business transaction, including rollback.
        session.rollback()
        assert event not in session


def test_audit_api_requires_identity_and_admin_before_database_query():
    app = FastAPI()
    app.include_router(router)
    path = f"/api/v1/spaces/{uuid4()}/audit-events"
    client = TestClient(app)
    assert client.get(path).status_code == 401
    app.dependency_overrides[member_access] = lambda: SimpleNamespace(role="member")
    assert client.get(path).status_code == 403
    assert client.get(path + "?limit=51").status_code == 422
