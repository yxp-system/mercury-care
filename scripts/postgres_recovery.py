"""Create/restore PostgreSQL custom archives without credentials in command arguments or manifests.

Recovery is restricted to a fresh database named *_restore_* and leaves business writes frozen.
Run with owner/backup credentials; runtime credentials cannot dump all RLS scopes.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import psycopg
from mercury.config import Settings
from sqlalchemy.engine import make_url


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["backup", "restore"])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--workers-stopped", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    if settings.writes_enabled or not args.workers_stopped:
        raise SystemExit("Freeze writes and stop API/worker consumers before this offline procedure")
    url = make_url(settings.database_url)
    if url.get_backend_name() != "postgresql":
        raise SystemExit("This procedure requires PostgreSQL")
    child_env = os.environ.copy()
    child_env.update(
        PGHOST=url.host or "localhost",
        PGPORT=str(url.port or 5432),
        PGUSER=url.username or "",
        PGPASSWORD=url.password or "",
        PGDATABASE=url.database or "",
    )
    container = os.environ.get("MERCURY_PG_CLIENT_CONTAINER", "")
    prefix = []
    if container:
        import re

        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", container):
            raise SystemExit("Invalid PostgreSQL client container identifier")
        prefix = ["docker", "exec", "-i"]
        for key in ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE"):
            prefix.extend(["-e", key])
        prefix.append(container)
    root = args.directory.resolve()
    started = time.monotonic()
    if args.mode == "backup":
        if root.exists():
            raise SystemExit("Backup directory already exists; choose a new destination")
        root.mkdir(parents=True)
        with (root / "database.dump").open("wb") as archive:
            subprocess.run(
                prefix + ["pg_dump", "--format=custom", "--no-owner"],
                env=child_env,
                check=True,
                stdout=archive,
                stderr=subprocess.PIPE,
            )
        uploads = settings.data_dir / "uploads"
        if uploads.exists():
            if any(path.is_symlink() for path in uploads.rglob("*")):
                raise SystemExit("Refusing symlink in attachment backup")
            shutil.copytree(uploads, root / "uploads")
        files = {
            str(path.relative_to(root).as_posix()): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*")
            if path.is_file()
        }
        manifest = {
            "created_at": time.time(),
            "files": files,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "writes_frozen": True,
            "external_recovery_window": "unverified",
            "sessions_require_revocation_on_restore": True,
        }
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {"backup": "complete", "files": len(files), "elapsed_seconds": manifest["elapsed_seconds"]}
            )
        )
    else:
        if "_restore_" not in (url.database or ""):
            raise SystemExit("Restore requires a fresh isolated database containing _restore_ in its name")
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        for relative, expected in manifest["files"].items():
            path = (root / relative).resolve()
            if (
                not path.is_relative_to(root)
                or path.is_symlink()
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected
            ):
                raise SystemExit("Backup integrity/path check failed")
        conninfo = psycopg.conninfo.make_conninfo(
            host=url.host,
            port=url.port or 5432,
            user=url.username,
            password=url.password,
            dbname=url.database,
        )
        with psycopg.connect(conninfo) as connection:
            if connection.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0]:
                raise SystemExit("Restore refuses a non-empty database")
        with (root / "database.dump").open("rb") as archive:
            subprocess.run(
                prefix
                + [
                    "pg_restore",
                    "--exit-on-error",
                    "--single-transaction",
                    "--no-owner",
                    "--no-acl",
                    "--dbname",
                    url.database,
                ],
                env=child_env,
                check=True,
                stdin=archive,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        with psycopg.connect(conninfo) as connection:
            connection.execute("UPDATE browser_sessions SET revoked=true, refresh_ciphertext=''")
            connection.execute("DELETE FROM login_transactions")
            counts = {
                table: connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                for table in (
                    "cases",
                    "plans",
                    "approvals",
                    "actions",
                    "jobs",
                    "shipments",
                    "attachments",
                    "browser_sessions",
                )
            }
        destination = settings.data_dir / "uploads"
        if (root / "uploads").exists():
            if destination.exists():
                raise SystemExit(
                    "Restore attachment destination must be new; database restored, writes remain frozen"
                )
            shutil.copytree(root / "uploads", destination)
        with psycopg.connect(conninfo) as connection:
            for identity, expected in connection.execute("SELECT id,sha256 FROM attachments"):
                path = destination / identity
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise SystemExit(
                        "Restored attachment does not match its database record; writes remain frozen"
                    )
        receipt = {
            "restore": "complete",
            "records": counts,
            "writes_frozen": True,
            "external_window_verified": False,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "recovery-report.json").write_text(
            json.dumps(receipt, indent=2), encoding="utf-8"
        )
        print(json.dumps(receipt))


if __name__ == "__main__":
    main()
