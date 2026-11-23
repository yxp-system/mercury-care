import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("backup", type=Path)
args = parser.parse_args()
root = args.backup.resolve()
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
for relative, expected in manifest["files"].items():
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise SystemExit("Backup path or digest mismatch")
with sqlite3.connect(root / "mercury.db") as connection:
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    counts = {
        table: connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        for table in ("cases", "plans", "approvals", "actions", "jobs")
    }
print(json.dumps({"integrity": "ok", "records": counts}))
