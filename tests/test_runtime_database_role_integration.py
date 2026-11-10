"""Real application/admin login checks against the dedicated acceptance database."""

import os

import pytest
from app.core.config import get_settings
from app.db.security import ROLE_CHECK_FAILED, check_database_role
from sqlalchemy import create_engine, text

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Live database opt-in required"),
]


def test_live_startup_role_gate_accepts_application_login_and_rejects_admin():
    settings = get_settings()
    assert settings.migration_database_url is not None
    application = create_engine(settings.database_url.get_secret_value())
    administrator = create_engine(settings.migration_database_url.get_secret_value())
    disguised_administrator = create_engine(
        settings.migration_database_url.get_secret_value(),
        connect_args={"options": "-c role=knowledge_app"},
    )
    try:
        check_database_role(application)
        with pytest.raises(RuntimeError, match="^" + ROLE_CHECK_FAILED + "$"):
            check_database_role(administrator)
        with disguised_administrator.connect() as connection:
            effective, login = connection.execute(text("SELECT current_user,session_user")).one()
            assert effective == "knowledge_app" and login != effective
        with pytest.raises(RuntimeError, match="^" + ROLE_CHECK_FAILED + "$"):
            check_database_role(disguised_administrator)
    finally:
        application.dispose()
        administrator.dispose()
        disguised_administrator.dispose()
