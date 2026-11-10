from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from uuid import UUID

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        get_settings().database_url.get_secret_value(),
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": 3},
    )


@contextmanager
def tenant_session(tenant_id: UUID) -> Iterator[Session]:
    """Call ONLY after identity + membership verification, never from a raw request header.

    SET LOCAL is transaction-scoped so pooled connections do not retain tenant state.
    No business routes are exposed until full identity/membership checks are implemented.
    """
    if not isinstance(tenant_id, UUID):
        raise ValueError("A validated tenant UUID is required")
    with Session(get_engine()) as session, session.begin():
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        yield session
