"""Explicit operator-only account provisioning while public registration stays closed."""

import argparse
import getpass
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.production import validate_production
from app.db.models import Tenant, TenantMember, User
from app.db.security import validate_runtime_database
from app.db.session import get_engine
from app.modules.audit.models import AuditAction
from app.modules.audit.service import record
from app.modules.identity.access import set_tenant, set_user
from app.modules.identity.models import AuthIdentity
from app.modules.identity.passwords import hash_password
from app.modules.identity.routes import Credentials
from app.modules.identity.security_events import SecurityAction, record_security_event


def create_owner(body: Credentials) -> dict[str, str]:
    """Same runtime RLS and audit as registration; no verified-email or login bypass."""
    user_id, tenant_id = uuid4(), uuid4()
    try:
        with Session(get_engine()) as session, session.begin():
            if session.scalar(select(AuthIdentity.user_id).where(AuthIdentity.email == body.email)):
                raise RuntimeError("ACCOUNT_ALREADY_EXISTS")
            set_user(session, user_id)
            set_tenant(session, tenant_id)
            session.add(
                User(id=user_id, email=body.email, password_hash=hash_password(body.password))
            )
            session.flush()
            session.add(AuthIdentity(email=body.email, user_id=user_id))
            session.add(Tenant(id=tenant_id, name="个人空间"))
            session.flush()
            session.add(TenantMember(tenant_id=tenant_id, user_id=user_id, role="owner"))
            session.flush()
            record(session, tenant_id, user_id, AuditAction.SPACE_CREATED, tenant_id)
            record_security_event(session, user_id, SecurityAction.ACCOUNT_REGISTERED)
        return {"user_id": str(user_id), "tenant_id": str(tenant_id)}
    except IntegrityError:
        raise RuntimeError("ACCOUNT_ALREADY_EXISTS") from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create-owner", action="store_true", required=True)
    parser.parse_args()
    # Interactive secrets only: no password arguments, env vars, output or generated tokens.
    try:
        validate_production(get_settings())
        validate_runtime_database()
        email = input("Account email: ")
        password = getpass.getpass("Password (12-256 characters): ")
        if password != getpass.getpass("Confirm password: "):
            raise RuntimeError("PASSWORD_CONFIRMATION_FAILED")
        create_owner(Credentials(email=email, password=password))
    except Exception:
        raise SystemExit("FAIL: account provisioning; no credentials printed") from None
    print("PASS: owner created; email remains unverified, no session issued")


if __name__ == "__main__":
    main()
