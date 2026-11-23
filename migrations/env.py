from alembic import context
from sqlalchemy import create_engine, pool

from mercury.config import Settings
from mercury.models import Base

settings = Settings()
target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(url=settings.database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(settings.database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        if engine.dialect.name == "postgresql":
            connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
            connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=engine.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()
