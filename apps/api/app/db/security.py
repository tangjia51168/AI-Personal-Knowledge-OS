"""Verify the effective and login database roles before production work begins."""

from sqlalchemy import Engine, text

from app.core.config import get_settings
from app.db.session import get_engine

ROLE_CHECK_FAILED = "Production database role check failed"


def check_database_role(engine: Engine) -> None:
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SET LOCAL statement_timeout = '3s'"))
            roles = connection.execute(
                text(
                    "SELECT rolsuper,rolbypassrls FROM pg_roles "
                    "WHERE rolname IN (current_user,session_user)"
                )
            ).all()
            # Checking session_user also rejects an administrator login that
            # merely SET ROLE'd into an unprivileged role and can reset it.
            if not roles or any(role.rolsuper or role.rolbypassrls for role in roles):
                raise RuntimeError(ROLE_CHECK_FAILED)
    except Exception:
        # Driver errors can contain connection strings and authentication data.
        raise RuntimeError(ROLE_CHECK_FAILED) from None


def validate_runtime_database() -> None:
    if get_settings().app_env == "production":
        try:
            engine = get_engine()
            try:
                check_database_role(engine)
            finally:
                # Celery validates in its parent process before prefork. Never
                # let children inherit the startup probe's pooled connection.
                engine.dispose()
        except Exception:
            raise RuntimeError(ROLE_CHECK_FAILED) from None
