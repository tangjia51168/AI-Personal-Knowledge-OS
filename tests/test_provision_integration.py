"""Actual operator provisioning through least-privilege runtime role and normal login."""

import os
from uuid import uuid4

import pytest
from app.db.security import check_database_role
from app.db.session import get_engine
from app.modules.identity import provision
from app.modules.identity.provision import create_owner
from app.modules.identity.routes import Credentials
from sqlalchemy import text
from test_organization_integration import auth, endpoint, organization_accounts

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_STACK_INTEGRATION") != "1", reason="Dedicated acceptance stack required"
    ),
]


def test_real_operator_owner_provision_login_audit_duplicate_and_unverified(monkeypatch):
    check_database_role(get_engine())
    for live, admin, accounts in organization_accounts.__wrapped__():
        body = Credentials(
            email=f"provision-{uuid4()}@example.invalid", password="operator-fixture-password-2026"
        )
        result = create_owner(body)
        accounts.append(result)
        assert set(result) == {"user_id", "tenant_id"}
        response = live.post("/api/v1/auth/login", json=body.model_dump())
        assert response.status_code == 200
        account = {**result, **response.json()["data"]}
        response = live.get(endpoint(account), headers=auth(account))
        assert response.status_code == 200 and response.json()["data"]["role"] == "owner"
        with admin.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT email_verified FROM users WHERE id=:id"), {"id": result["user_id"]}
                ).scalar_one()
                is False
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM audit_events "
                        "WHERE tenant_id=:t AND action='space.created'"
                    ),
                    {"t": result["tenant_id"]},
                ).scalar_one()
                == 1
            )
        with pytest.raises(RuntimeError, match="^ACCOUNT_ALREADY_EXISTS$"):
            create_owner(body)
        failed = Credentials(
            email=f"failed-provision-{uuid4()}@example.invalid",
            password="operator-fixture-password-2026",
        )
        with monkeypatch.context() as patch:

            def reject(*args, **kwargs):
                raise RuntimeError("AUDIT_FIXTURE")

            patch.setattr(provision, "record", reject)
            with pytest.raises(RuntimeError, match="AUDIT_FIXTURE"):
                create_owner(failed)
        with admin.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM users WHERE email=:email"), {"email": failed.email}
                ).scalar_one()
                == 0
            )
