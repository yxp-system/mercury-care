"""Consistent SQLite demo backup. PostgreSQL procedure is documented in docs/operations.md."""

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from mercury.config import Settings

parser = argparse.ArgumentParser()
parser.add_argument("destination", type=Path)
args = parser.parse_args()
settings = Settings()
if settings.env not in {"demo", "test"} or not settings.database_url.startswith("sqlite:///"):
    raise SystemExit("This command supports only isolated SQLite demo/test databases")
if args.destination.exists():
    raise SystemExit("Choose a new backup folder; existing contents will not be overwritten")
args.destination.mkdir(parents=True)
database = Path(settings.database_url.removeprefix("sqlite:///"))
for source, name in [
    (database, "mercury.db"),
    (settings.data_dir / "checkpoints.sqlite", "checkpoints.sqlite"),
]:
    if source.exists():
        with sqlite3.connect(source) as src, sqlite3.connect(args.destination / name) as dst:
            src.backup(dst)
uploads = settings.data_dir / "uploads"
if uploads.exists():
    shutil.copytree(uploads, args.destination / "uploads")
manifest = {
    "created_at": datetime.now(timezone.utc).isoformat(),
    "environment": settings.env,
    "requirement": "Stop API and worker before invoking for consistency across files",
    "files": {},
}
for file in args.destination.rglob("*"):
    if file.is_file():
        manifest["files"][file.relative_to(args.destination).as_posix()] = hashlib.sha256(
            file.read_bytes()
        ).hexdigest()
(args.destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print("Backup written; secrets excluded. Verify with scripts/verify_backup.py.")
