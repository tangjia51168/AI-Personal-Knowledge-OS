from alembic import context
from app.core.config import get_settings
from app.db.models import Base
from sqlalchemy import create_engine, pool

target_metadata = Base.metadata
settings = get_settings()
if settings.migration_database_url is None:
    raise RuntimeError("MIGRATION_DATABASE_URL must be configured explicitly for migrations")
url = settings.migration_database_url.get_secret_value()

if context.is_offline_mode():
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
