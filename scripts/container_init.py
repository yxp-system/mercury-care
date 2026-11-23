"""Explicit migration role bootstraps restricted app credentials; never log passwords."""

import os
import subprocess
import sys

import psycopg
from mercury.config import Settings
from psycopg import sql

settings = Settings()
subprocess.run([sys.executable, "-m", "mercury.cli", "migrate"], check=True)
if settings.env == "demo":
    subprocess.run([sys.executable, "-m", "mercury.cli", "seed"], check=True)
url = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
with psycopg.connect(url) as connection:
    exists = connection.execute("SELECT 1 FROM pg_roles WHERE rolname='mercury_app'").fetchone()
    if not exists:
        connection.execute("CREATE ROLE mercury_app LOGIN NOSUPERUSER NOBYPASSRLS")
    connection.execute(
        sql.SQL("ALTER ROLE mercury_app PASSWORD {}").format(sql.Literal(os.environ["POSTGRES_APP_PASSWORD"]))
    )
    connection.execute("GRANT CONNECT ON DATABASE mercury TO mercury_app")
    connection.execute("GRANT USAGE ON SCHEMA public TO mercury_app")
    connection.execute("GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO mercury_app")
    connection.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO mercury_app")
    # Append-only legal records; runtime cannot overwrite or delete past choices and decisions.
    connection.execute(
        "REVOKE UPDATE ON audit, approvals, choices, business_events, stock_ledger FROM mercury_app"
    )
print("Schema migrated; restricted runtime role granted scoped access.")
