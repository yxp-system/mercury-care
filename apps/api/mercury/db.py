from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from mercury.config import Settings


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        sqlite = settings.database_url.startswith("sqlite")
        self.engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False, "timeout": 30} if sqlite else {},
        )
        if sqlite:

            @event.listens_for(self.engine, "connect")
            def sqlite_pragmas(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA busy_timeout=30000")

        self.factory = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self, scope=None, *, write=False):
        if write:
            from mercury.runtime import ensure_lease

            ensure_lease()
        with self.factory() as session:
            try:
                # Serialize SQLite demo writes before reading authorization/resource snapshots.
                if write and self.engine.dialect.name == "sqlite":
                    session.execute(text("BEGIN IMMEDIATE"))
                if scope and self.engine.dialect.name == "postgresql":
                    session.execute(
                        text(
                            "SELECT set_config('app.tenant_id', :tenant, true), "
                            "set_config('app.store_id', :store, true)"
                        ),
                        {"tenant": scope.tenant_id, "store": scope.store_id},
                    )
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise


def scoped(model, ctx):
    return (model.tenant_id == ctx.tenant_id, model.store_id == ctx.store_id)
